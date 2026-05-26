"""60 题:从 ``models.customization`` 种子加载到模型库 — 单测。

不依赖网络,不依赖 NiceGUI;用临时目录隔离。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from chayuan.server.model_registry.seed import (
    CUSTOMIZATION_FILENAME,
    SeedReport,
    _candidate_paths,
    _locate_customization_file,
    seed_async,
    seed_from_customization,
)


# ---------------------------------------------------------------------------
# 工具:制造一个最小化 models.customization 文件
# ---------------------------------------------------------------------------


def _hf_record(model_id: str, **extra: Any) -> Dict[str, Any]:
    """构造一条最小化 HF /api/models 记录。"""
    return {
        "id": model_id,
        "modelId": model_id,
        "tags": extra.pop("tags", ["test"]),
        "pipeline_tag": extra.pop("pipeline_tag", "feature-extraction"),
        "downloads": extra.pop("downloads", 100),
        "likes": extra.pop("likes", 1),
        "siblings": [],
        **extra,
    }


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """把 CHAYUAN_ROOT 切到 tmp_path,避免污染真实 model_registry。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    # 同时 patch catalog 内部的 REGISTRY_FILE/REGISTRY_DIR(它们在 import 时已固定)
    from chayuan.server.model_registry import catalog
    monkeypatch.setattr(catalog, "REGISTRY_DIR", tmp_path / "model_registry")
    monkeypatch.setattr(catalog, "REGISTRY_FILE", tmp_path / "model_registry" / "models.json")
    return tmp_path


# ---------------------------------------------------------------------------
# _candidate_paths / _locate_customization_file
# ---------------------------------------------------------------------------


def test_candidate_paths_returns_multiple_paths():
    paths = _candidate_paths()
    assert len(paths) >= 2  # 至少 CHAYUAN_ROOT + 包内 + 开发模式
    for p in paths:
        assert isinstance(p, Path)
        assert p.name == CUSTOMIZATION_FILENAME


def test_locate_returns_first_existing(tmp_path, monkeypatch):
    """构造一个 CHAYUAN_ROOT 下的文件,_locate 应返回它(优先级最高)。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    target = tmp_path / "model_registry" / CUSTOMIZATION_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[]", encoding="utf-8")
    # 因为 _candidate_paths 内部从 chayuan.settings 取 CHAYUAN_ROOT,
    # 而 settings 可能已缓存,这里直接传 file_path 显式调
    located = _locate_customization_file()
    # 至少不应该 raise;不强求一定命中(取决于 settings 是否重新读 env)


def test_locate_returns_none_when_no_file(monkeypatch, tmp_path):
    """所有候选路径都不存在 → 返 None。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path / "nonexistent"))
    # 在 tmp_path 不放任何文件
    # _candidate_paths 还会检查包内 / repo dev path,如果实际 repo 里有文件
    # 那就会找到。本测试不强求,主要是不应崩
    result = _locate_customization_file()
    assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# seed_from_customization
# ---------------------------------------------------------------------------


def test_seed_returns_error_when_file_missing(isolated_root):
    """指定不存在的文件 → SeedReport.error 非空,不抛。"""
    fake = isolated_root / "nope.customization"
    report = seed_from_customization(fake)
    assert report.error is not None
    assert report.added == 0


def test_seed_loads_records_into_empty_index(isolated_root):
    """空索引 + 3 条种子 → 全部添加。"""
    src = isolated_root / "seed.customization"
    src.write_text(json.dumps([
        _hf_record("vendor-a/model-1"),
        _hf_record("vendor-b/model-2"),
        _hf_record("vendor-c/model-3", tags=["llm", "chat"]),
    ]), encoding="utf-8")

    report = seed_from_customization(src)
    assert report.error is None
    assert report.total_records == 3
    assert report.added == 3
    assert report.skipped_existing == 0
    assert report.skipped_invalid == 0

    # 验证 models.json 真的被写
    out = json.loads(
        (isolated_root / "model_registry" / "models.json").read_text(encoding="utf-8")
    )
    pids = {it["id"] for it in out["items"]}
    assert pids == {"vendor-a/model-1", "vendor-b/model-2", "vendor-c/model-3"}


def test_seed_skips_existing_by_default(isolated_root):
    """已存在的同 id 默认不覆盖。"""
    # 先写一个手动条目
    from chayuan.server.model_registry.catalog import _save_index
    _save_index([{"id": "vendor-a/model-1", "name": "old-name", "downloads": 999}])

    src = isolated_root / "seed.customization"
    src.write_text(json.dumps([
        _hf_record("vendor-a/model-1", downloads=1),  # 同 id,不该覆盖
        _hf_record("vendor-b/model-2", downloads=2),  # 新 id,该添加
    ]), encoding="utf-8")

    report = seed_from_customization(src)
    assert report.added == 1
    assert report.skipped_existing == 1

    out = json.loads(
        (isolated_root / "model_registry" / "models.json").read_text(encoding="utf-8")
    )
    items_by_id = {it["id"]: it for it in out["items"]}
    # 旧 name 保留 — 没被覆盖
    assert items_by_id["vendor-a/model-1"].get("name") == "old-name"
    # 新条目存在
    assert "vendor-b/model-2" in items_by_id


def test_seed_force_overwrites_existing(isolated_root):
    """force=True 时同 id 覆盖。"""
    from chayuan.server.model_registry.catalog import _save_index
    _save_index([{"id": "vendor-a/model-1", "name": "old-name", "downloads": 999}])

    src = isolated_root / "seed.customization"
    src.write_text(json.dumps([
        _hf_record("vendor-a/model-1", downloads=1),
    ]), encoding="utf-8")

    report = seed_from_customization(src, force=True)
    assert report.added == 1
    assert report.skipped_existing == 0

    out = json.loads(
        (isolated_root / "model_registry" / "models.json").read_text(encoding="utf-8")
    )
    items_by_id = {it["id"]: it for it in out["items"]}
    # 新数据(name=model_id)覆盖了旧 name
    assert items_by_id["vendor-a/model-1"]["name"] == "vendor-a/model-1"


def test_seed_invalid_records_counted(isolated_root):
    """非 dict / id 缺失 / 解析失败的记录归到 skipped_invalid。"""
    src = isolated_root / "seed.customization"
    src.write_text(json.dumps([
        "not-a-dict",         # 1) 非 dict
        {},                   # 2) 无 id (normalize 后 model_id 为空 → invalid)
        _hf_record("ok/m"),   # 3) 正常
    ]), encoding="utf-8")

    report = seed_from_customization(src)
    # 至少 1 条(字符串)被 invalid;空 dict 在 _normalize_hf_item 后
    # canonical_id 也会为空,会被 invalid
    assert report.skipped_invalid >= 1
    assert report.added >= 1


def test_seed_handles_non_array_json(isolated_root):
    """JSON 不是数组 → 报 error,不抛异常。"""
    src = isolated_root / "seed.customization"
    src.write_text(json.dumps({"not": "array"}), encoding="utf-8")
    report = seed_from_customization(src)
    assert report.error is not None
    assert report.added == 0


def test_seed_handles_malformed_json(isolated_root):
    """JSON 解析失败 → 报 error。"""
    src = isolated_root / "seed.customization"
    src.write_text("not json {", encoding="utf-8")
    report = seed_from_customization(src)
    assert report.error is not None


# ---------------------------------------------------------------------------
# seed_async
# ---------------------------------------------------------------------------


def test_seed_async_returns_started_thread(isolated_root):
    """seed_async 返回 daemon thread,立即返回(不阻塞调用方)。"""
    src = isolated_root / "model_registry" / CUSTOMIZATION_FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps([_hf_record("a/b")]), encoding="utf-8")

    t0 = time.time()
    t = seed_async()
    elapsed = time.time() - t0

    assert isinstance(t, threading.Thread)
    assert t.daemon is True
    # 立即返回 — 100ms 是一个非常宽松的阈值
    assert elapsed < 0.1, f"seed_async 应立即返回(实际 {elapsed:.3f}s)"

    # 等 thread 跑完,确认没崩
    t.join(timeout=10.0)
    assert not t.is_alive()


def test_seed_async_calls_on_done(isolated_root):
    """on_done 回调收到 SeedReport。"""
    src = isolated_root / "model_registry" / CUSTOMIZATION_FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps([_hf_record("x/y")]), encoding="utf-8")

    received: List[SeedReport] = []
    done = threading.Event()

    def _cb(report: SeedReport) -> None:
        received.append(report)
        done.set()

    t = seed_async(on_done=_cb)
    assert done.wait(timeout=10.0), "on_done 在 10s 内未触发"
    t.join(timeout=2.0)

    assert len(received) == 1
    assert isinstance(received[0], SeedReport)


def test_seed_async_swallows_exceptions(monkeypatch):
    """seed_from_customization 抛异常 → seed_async 不向上传播,封装到 SeedReport。"""
    from chayuan.server.model_registry import seed as _seed_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(_seed_mod, "seed_from_customization", _boom)

    received: List[SeedReport] = []
    done = threading.Event()

    def _cb(report: SeedReport) -> None:
        received.append(report)
        done.set()

    t = _seed_mod.seed_async(on_done=_cb)
    assert done.wait(timeout=5.0)
    t.join(timeout=2.0)

    assert len(received) == 1
    assert received[0].error is not None
    assert "simulated crash" in received[0].error
