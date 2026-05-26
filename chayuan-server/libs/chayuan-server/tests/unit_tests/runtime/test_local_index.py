"""local_index 扫描 / 增量比对测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan.server.model_registry import local_index as _li
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
    ScanDelta,
    scan_once,
)


def _entry(mid: str, **kw) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=mid,
        path=kw.get("path", f"/tmp/{mid}"),
        relpath=kw.get("relpath", mid),
        capability=kw.get("capability", "chat"),
        family=kw.get("family", ""),
        format=kw.get("format", "gguf"),
        size_bytes=int(kw.get("size_bytes", 1024)),
        mtime=float(kw.get("mtime", 1700000000)),
        confidence=float(kw.get("confidence", 0.9)),
        evidence=list(kw.get("evidence", [])),
        meta=dict(kw.get("meta", {})),
        source_tag=kw.get("source_tag", "models"),
    )


def test_replace_all_initial_all_added(tmp_path: Path):
    idx = LocalModelIndex(path=tmp_path / "local_models.json")
    delta = idx.replace_all([_entry("a"), _entry("b")])
    assert sorted(e.model_id for e in delta.added) == ["a", "b"]
    assert delta.updated == [] and delta.removed == []
    assert {e.model_id for e in idx.list_entries()} == {"a", "b"}


def test_replace_all_detects_updated_and_removed(tmp_path: Path):
    idx = LocalModelIndex(path=tmp_path / "local_models.json")
    idx.replace_all([_entry("a", size_bytes=100), _entry("b")])
    delta = idx.replace_all([
        _entry("a", size_bytes=200),  # size changed → updated
        _entry("c"),                  # new
    ])
    assert [e.model_id for e in delta.added] == ["c"]
    assert [e.model_id for e in delta.updated] == ["a"]
    assert delta.removed == ["b"]


def test_replace_all_no_change(tmp_path: Path):
    idx = LocalModelIndex(path=tmp_path / "local_models.json")
    items = [_entry("a", size_bytes=100, mtime=1700000000)]
    idx.replace_all(items)
    delta = idx.replace_all(items)
    assert not delta.changed


def test_persist_and_reload(tmp_path: Path):
    p = tmp_path / "local_models.json"
    idx = LocalModelIndex(path=p)
    idx.replace_all([_entry("a", capability="text-embedding")])
    # 直接读 JSON
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["items"][0]["capability"] == "text-embedding"
    # 再 new 一个 LocalModelIndex 应该能恢复同名 entry
    idx2 = LocalModelIndex(path=p)
    assert idx2.get("a") is not None


def test_by_capability(tmp_path: Path):
    idx = LocalModelIndex(path=tmp_path / "x.json")
    idx.replace_all([
        _entry("c1", capability="chat"),
        _entry("e1", capability="text-embedding"),
        _entry("c2", capability="chat"),
    ])
    chats = idx.by_capability("chat")
    assert sorted(e.model_id for e in chats) == ["c1", "c2"]


def test_scan_delta_changed_property():
    d = ScanDelta()
    assert not d.changed
    d.added.append(_entry("a"))
    assert d.changed


# ─────────────── cap-scoped scan_once(只扫单个能力子目录) ───────────────


def _make_bundled_model(root: Path, cap_dir: str, repo: str) -> Path:
    """在 ``root/models/bundled/<cap_dir>/<repo>/`` 造一个最小可识别模型仓库。"""
    d = root / "models" / "bundled" / cap_dir / repo
    d.mkdir(parents=True, exist_ok=True)
    # config.json 是 DIR_MARKER_FILES 之一 → _is_dir_repo 命中
    (d / "config.json").write_text('{"model_type": "bert"}', encoding="utf-8")
    (d / "model.gguf").write_bytes(b"\x00" * 64)
    return d


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    """把 CHAYUAN_ROOT 指到 tmp,并强制 local_index 单例按新路径重建。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    # 单例缓存了上一次的 path,强制 reload 到新 CHAYUAN_ROOT
    _li.get_local_index(force_reload=True)
    yield tmp_path
    _li.get_local_index(force_reload=True)


def test_scan_once_cap_scoped_only_picks_target_cap(_isolated_root):
    """cap-scoped:只扫 bundled/<cap>/,别的 cap 目录里的模型不进本次结果。"""
    _make_bundled_model(_isolated_root, "rerank", "gpustack--bge-reranker-v2-m3-GGUF")
    _make_bundled_model(_isolated_root, "embedding", "gpustack--bge-m3-GGUF")

    delta = scan_once(bundled_cap_dir="rerank")
    # 本次只扫 rerank 子目录 → 只发现 1 个
    assert len(delta.added) == 1
    assert "bundled/rerank/" in delta.added[0].model_id
    # embedding 那个不该出现在 added 里
    assert all("embedding" not in e.model_id for e in delta.added)


def test_scan_once_cap_scoped_preserves_other_caps(_isolated_root):
    """cap-scoped 扫 rerank 时,先前扫到的 embedding 条目必须原样保留(不被当 removed 删)。"""
    _make_bundled_model(_isolated_root, "rerank", "gpustack--bge-reranker-v2-m3-GGUF")
    _make_bundled_model(_isolated_root, "embedding", "gpustack--bge-m3-GGUF")

    # 先全量扫一次 → 两个 cap 都进索引
    scan_once()
    idx = _li.get_local_index()
    assert len(idx.list_entries()) == 2

    # 再 cap-scoped 扫 rerank:embedding 条目不能被删
    delta = scan_once(bundled_cap_dir="rerank")
    assert delta.removed == []  # embedding 没被当成 removed
    ids = {e.model_id for e in idx.list_entries()}
    assert any("bundled/embedding/" in i for i in ids)
    assert any("bundled/rerank/" in i for i in ids)
    assert len(ids) == 2


def test_scan_once_cap_scoped_detects_removal_within_cap(_isolated_root):
    """cap-scoped:本 cap 子目录里的模型被删,应被 ScanDelta.removed 捕获;别的 cap 不受影响。"""
    r = _make_bundled_model(_isolated_root, "rerank", "gpustack--bge-reranker-v2-m3-GGUF")
    _make_bundled_model(_isolated_root, "embedding", "gpustack--bge-m3-GGUF")
    scan_once()  # 全量,两个都入索引

    # 删掉 rerank 那个仓库
    import shutil
    shutil.rmtree(r)

    delta = scan_once(bundled_cap_dir="rerank")
    assert len(delta.removed) == 1
    assert "bundled/rerank/" in delta.removed[0]
    # embedding 仍在
    ids = {e.model_id for e in _li.get_local_index().list_entries()}
    assert any("bundled/embedding/" in i for i in ids)


def test_scan_once_cap_scoped_missing_dir_is_noop(_isolated_root):
    """cap-scoped 扫一个根本不存在的 bundled/<cap> 目录:不报错,added 为空。"""
    _make_bundled_model(_isolated_root, "embedding", "gpustack--bge-m3-GGUF")
    scan_once()
    delta = scan_once(bundled_cap_dir="rerank")  # rerank 目录不存在
    assert delta.added == [] and delta.removed == []
    # embedding 条目保留
    assert len(_li.get_local_index().list_entries()) == 1


def test_scan_once_full_scan_still_works(_isolated_root):
    """不传 bundled_cap_dir 时维持全量扫描行为。"""
    _make_bundled_model(_isolated_root, "rerank", "r1")
    _make_bundled_model(_isolated_root, "embedding", "e1")
    delta = scan_once()
    assert len(delta.added) == 2
