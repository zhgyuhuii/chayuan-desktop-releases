"""``candidates_bridge`` 的本地 → panel candidates 桥接测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.candidates_bridge import (
    LOCAL_TO_PANEL_CAP,
    local_candidates_for,
    merge_local_into_candidates,
)
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-bridge-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


def _entry(model_id: str, capability: str, fmt: str = "gguf") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format=fmt,
        family=capability.replace("-", "_"), size_bytes=1024 * 1024 * 250,
    )


# ─────────────────────────── 映射完整性 ───────────────────────────


def test_mapping_covers_all_panel_caps():
    """CAPABILITY_LABELS 里有的每个 cap 都应该有对应的 local 词来源。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        CAPABILITY_LABELS,
    )
    panel_caps = {c for c, _ in CAPABILITY_LABELS}
    mapped_panels = set(LOCAL_TO_PANEL_CAP.values())
    missing = panel_caps - mapped_panels
    assert not missing, f"以下 panel cap 没有 local 来源: {missing}"


def test_mapping_local_keys_match_identifier_vocabulary():
    """LOCAL_TO_PANEL_CAP 的 key 必须是 identifier 可能输出的 capability 词。

    覆盖 identifier 内部四套字面量源:模型类型字典 / pipeline 字典 / diffusers
    字典 / 路径关键字。
    """
    from chayuan.server.model_registry import identifier
    known: set[str] = set()
    known.update(identifier._CAPABILITY_BY_MODELTYPE.values())
    known.update(identifier._CAPABILITY_BY_PIPELINE.values())
    known.update(identifier._DIFFUSERS_PIPELINE_KIND.values())
    for _kw, cap_value in identifier._PATH_HINTS:
        known.add(cap_value)
    for local_cap in LOCAL_TO_PANEL_CAP:
        assert local_cap in known, (
            f"LOCAL_TO_PANEL_CAP key {local_cap!r} 不在 identifier 已知 capability 集中"
        )


# ───────────────────────── local_candidates_for ─────────────────────────


def test_local_candidates_for_chat():
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        cands = local_candidates_for("chat")
    assert len(cands) == 1
    assert cands[0]["id"] == "qwen3-4b"
    assert cands[0]["runtime"] == "local"
    assert cands[0]["source"] == "local_index"
    assert cands[0]["format"] == "gguf"
    assert "path" in cands[0]


def test_local_candidates_for_embedding_uses_text_embedding():
    """panel cap 'embedding' 应该映射到 local cap 'text-embedding'。"""
    idx = _idx_with([_entry("bge-m3", "text-embedding")])
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        cands = local_candidates_for("embedding")
    assert len(cands) == 1
    assert cands[0]["id"] == "bge-m3"


def test_local_candidates_for_unknown_returns_empty():
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ):
        assert local_candidates_for("nonsense-cap") == []


def test_local_candidates_dedupe_by_id():
    """同一 model_id 出现多次只保留一条。"""
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("qwen3-4b", "chat"),  # 重复，构造测试
    ])
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        cands = local_candidates_for("chat")
    # local_index 自己已经 dict[model_id]=entry，不会真重复；但函数实现的
    # seen 去重也要保证调用 N 次结果一致。
    assert len(cands) == 1


# ───────────────────────── merge_local_into_candidates ─────────────────────────


def test_merge_appends_local_when_panel_empty():
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker-v2", "rerank"),
    ])
    candidates: dict[str, list[dict]] = {"chat": [], "embedding": [], "rerank": []}
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        merge_local_into_candidates(candidates)
    assert [c["id"] for c in candidates["chat"]] == ["qwen3-4b"]
    assert [c["id"] for c in candidates["embedding"]] == ["bge-m3"]
    assert [c["id"] for c in candidates["rerank"]] == ["bge-reranker-v2"]


def test_merge_does_not_duplicate_when_platform_already_has_same_id():
    """platform 已有同名 model_id 时不再追加本地条目（platform 字段更丰富）。"""
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    candidates: dict[str, list[dict]] = {
        "chat": [
            {"id": "qwen3-4b", "runtime": "ollama", "format": None,
             "size_bytes": 0, "is_default": False},
        ],
    }
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        merge_local_into_candidates(candidates)
    # 只保留 platform 的那条
    assert len(candidates["chat"]) == 1
    assert candidates["chat"][0]["runtime"] == "ollama"


def test_merge_marks_is_default_from_defaults_dict():
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("qwen3-7b", "chat"),
    ])
    candidates: dict[str, list[dict]] = {"chat": []}
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        merge_local_into_candidates(candidates, defaults={"chat": "qwen3-7b"})
    by_id = {c["id"]: c for c in candidates["chat"]}
    assert by_id["qwen3-7b"]["is_default"] is True
    assert by_id["qwen3-4b"]["is_default"] is False


def test_merge_scans_once_at_most_for_multiple_caps():
    """N 个 cap 调一次扫盘就够,避免重复 IO。"""
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
    ])
    candidates: dict[str, list[dict]] = {"chat": [], "embedding": []}
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ) as scan_mock:
        merge_local_into_candidates(candidates, do_scan=True)
    # scan_once 总共最多触发一次
    assert scan_mock.call_count <= 1


def test_merge_do_scan_false_never_scans():
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    candidates: dict[str, list[dict]] = {"chat": []}
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ) as scan_mock:
        merge_local_into_candidates(candidates, do_scan=False)
    scan_mock.assert_not_called()


def test_merge_returns_same_dict():
    """API 设计上返回同一字典，方便链式调用。"""
    idx = _idx_with([])
    candidates: dict[str, list[dict]] = {"chat": []}
    with mock.patch(
        "chayuan.server.model_registry.candidates_bridge.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.candidates_bridge.scan_once",
        return_value=None,
    ):
        out = merge_local_into_candidates(candidates)
    assert out is candidates
