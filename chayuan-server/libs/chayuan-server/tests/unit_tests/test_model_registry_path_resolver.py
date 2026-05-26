"""``path_resolver`` 行为测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)
from chayuan.server.model_registry.path_resolver import (
    maybe_resolve,
    resolve_model_id_to_path,
)


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-resolver-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


def _entry(model_id: str, capability: str, path: str = "") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=path or f"/tmp/fake/{model_id}",
        relpath=model_id,
        capability=capability,
        format="hf_transformers",
        family=capability.replace("-", "_"),
        size_bytes=1024,
    )


# ───────────────────────── 主要分支 ─────────────────────────


def test_translates_local_model_id_to_path():
    idx = _idx_with([
        _entry("models/rerank/BAAI--bge-reranker-v2-m3", "rerank",
               path="/opt/chayuan/models/rerank/BAAI--bge-reranker-v2-m3"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        path = resolve_model_id_to_path("models/rerank/BAAI--bge-reranker-v2-m3")
    assert path == "/opt/chayuan/models/rerank/BAAI--bge-reranker-v2-m3"


def test_passthrough_when_not_in_local_index():
    """HF repo id 这类非 local_index 标识应该原样返回。"""
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        out = resolve_model_id_to_path("BAAI/bge-reranker-v2-m3")
    assert out == "BAAI/bge-reranker-v2-m3"


def test_passthrough_when_absolute_path():
    """绝对路径不应被改写。"""
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        out = resolve_model_id_to_path("/opt/manual/model")
    assert out == "/opt/manual/model"


def test_empty_input_returns_empty():
    assert resolve_model_id_to_path("") == ""


def test_local_index_failure_falls_through():
    """local_index 抛异常时应返回原值,不阻塞 loader。"""
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        side_effect=RuntimeError("disk error"),
    ):
        out = resolve_model_id_to_path("BAAI/bge-m3")
    assert out == "BAAI/bge-m3"


def test_entry_with_empty_path_falls_through():
    """LocalModelEntry 存在但 path 字段空时,fallback 到原值。"""
    # 显式构造 path="" 的 entry,绕过 _entry 默认填路径的行为
    empty_path_entry = LocalModelEntry(
        model_id="some-id", path="", relpath="some-id",
        capability="rerank", format="hf_transformers", family="rerank",
        size_bytes=0,
    )
    idx = _idx_with([empty_path_entry])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        out = resolve_model_id_to_path("some-id")
    # path 空 → 不翻译,返回原 model_id
    assert out == "some-id"


# ───────────────────────── maybe_resolve ─────────────────────────


def test_maybe_resolve_handles_none():
    assert maybe_resolve(None) is None


def test_maybe_resolve_handles_empty():
    assert maybe_resolve("") == ""


def test_maybe_resolve_translates_known():
    idx = _idx_with([
        _entry("models/embedding/BAAI--bge-m3", "text-embedding",
               path="/models/embedding/BAAI--bge-m3"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        out = maybe_resolve("models/embedding/BAAI--bge-m3")
    assert out == "/models/embedding/BAAI--bge-m3"
