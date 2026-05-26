"""``chayuan.server.model_registry.bootstrap.check_bootstrap`` 行为测试。

只测纯逻辑：注入一个内存里捏好的 ``LocalModelIndex``，不动磁盘扫盘路径。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.bootstrap import (
    BootstrapReport,
    CapabilityStatus,
    DEFAULT_REQUIRED,
    check_bootstrap,
)
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    """构造一个临时 LocalModelIndex 实例，用持久化文件回写。"""
    td = Path(tempfile.mkdtemp(prefix="chayuan-bootstrap-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


def _entry(model_id: str, capability: str, fmt: str = "gguf") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=f"/tmp/fake/{model_id}",
        relpath=model_id,
        capability=capability,
        format=fmt,
        family=capability,
        size_bytes=1024,
    )


# ───────────────────────────── 主流程 ──────────────────────────────


def test_default_required_is_three_pieces():
    """默认必需 capability 是 chat / text-embedding / rerank 三件套。"""
    assert DEFAULT_REQUIRED == ("chat", "text-embedding", "rerank")


def test_ready_when_all_capabilities_present():
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=False)
    assert r.ready is True
    assert r.missing == []
    assert len(r.statuses) == 3
    assert all(s.satisfied for s in r.statuses)


def test_reports_missing_capabilities():
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=False)
    assert r.ready is False
    assert set(r.missing) == {"text-embedding", "rerank"}
    assert sum(1 for s in r.statuses if s.satisfied) == 1


def test_custom_required_list():
    """用户可以传自定义 capability，不强吃默认三件套。"""
    idx = _idx_with([_entry("whisper-tiny", "speech-to-text")])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(required=("speech-to-text",), do_scan=False)
    assert r.ready is True
    assert r.missing == []


def test_multiple_candidates_per_capability_are_returned():
    """同一 capability 有多条记录时，全部应作为候选返回，便于前端展示。"""
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("qwen3-7b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=False)
    chat_status = next(s for s in r.statuses if s.capability == "chat")
    assert len(chat_status.candidates) == 2
    assert {e.model_id for e in chat_status.candidates} == {"qwen3-4b", "qwen3-7b"}


# ───────────────────────────── 序列化 ──────────────────────────────


def test_report_to_dict_is_json_safe():
    """到前端要走 JSON；任何字段必须可序列化。"""
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=False)
    s = json.dumps(r.to_dict())
    d = json.loads(s)
    assert d["ready"] is False
    assert "missing" in d
    assert "statuses" in d
    # 每个 status 必须包含 candidates 列表
    for st in d["statuses"]:
        assert "capability" in st
        assert "satisfied" in st
        assert "candidates" in st


def test_capability_status_to_dict_shape():
    s = CapabilityStatus(
        capability="chat",
        satisfied=True,
        candidates=[_entry("qwen3-4b", "chat")],
    )
    d = s.to_dict()
    assert d["capability"] == "chat"
    assert d["satisfied"] is True
    assert d["candidate_count"] == 1
    assert d["candidates"][0]["model_id"] == "qwen3-4b"
    assert d["candidates"][0]["format"] == "gguf"


# ─────────────────────── do_scan 容错 ───────────────────────


def test_scan_failure_does_not_block_check():
    """``scan_once`` 抛异常时不应阻塞整个检测——按已有索引继续判断。"""
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        side_effect=RuntimeError("disk error"),
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=True)
    assert r.ready is True
    assert r.missing == []


def test_empty_index_is_not_ready():
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ):
        r = check_bootstrap(do_scan=False)
    assert r.ready is False
    assert set(r.missing) == set(DEFAULT_REQUIRED)
