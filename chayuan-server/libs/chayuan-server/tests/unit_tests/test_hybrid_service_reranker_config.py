"""``hybrid_service._reranker_config`` 行为测试。

新行为(B3):
* capability_router.resolve_model('rerank') 优先于 Settings
* local_index 风格的 model_id 会被翻译成磁盘路径
* router 抛异常时静默 fallback 到 Settings,不阻塞 retrieval
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.file_rag.hybrid_service import _reranker_config
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-rerank-cfg-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


def _entry(model_id: str, path: str) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=path, relpath=model_id,
        capability="rerank", format="hf_transformers",
        family="rerank", size_bytes=1024,
    )


# ─────────────────────── 路由优先级 ───────────────────────


def test_uses_capability_router_when_available():
    """capability_router 返回的 model_id 应该优先于 Settings 默认。"""
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value="BAAI/bge-reranker-v2-m3-custom",
    ):
        model_name, *_ = _reranker_config()
    assert model_name == "BAAI/bge-reranker-v2-m3-custom"


def test_falls_back_to_settings_when_router_empty():
    """capability_router 返回空时应使用 Settings 默认。"""
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value=None,
    ):
        model_name, *_ = _reranker_config()
    # 应该回到 Settings.kb_settings.RERANKER_MODEL 或硬编码默认
    assert model_name  # 至少不是空字符串
    assert "rerank" in model_name.lower() or model_name == "BAAI/bge-reranker-v2-m3"


def test_router_exception_falls_through_silently():
    """capability_router 抛异常不应阻塞 retrieval。"""
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        side_effect=RuntimeError("router broken"),
    ):
        model_name, *_ = _reranker_config()
    # 仍能拿到一个合法 model_name(Settings 兜底)
    assert model_name


# ─────────────────────── 路径翻译 ───────────────────────


def test_local_index_model_id_is_translated_to_path():
    """capability_router 给的是 local_index 风格的 model_id,应翻译成 entry.path。"""
    idx = _idx_with([
        _entry("models/rerank/BAAI--bge-reranker-v2-m3",
               "/opt/chayuan/models/rerank/BAAI--bge-reranker-v2-m3"),
    ])
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value="models/rerank/BAAI--bge-reranker-v2-m3",
    ), mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        model_name, *_ = _reranker_config()
    assert model_name == "/opt/chayuan/models/rerank/BAAI--bge-reranker-v2-m3"


def test_hf_repo_id_passes_through_unchanged():
    """HF repo id 不应被改写。"""
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value="BAAI/bge-reranker-v2-m3",
    ), mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        model_name, *_ = _reranker_config()
    assert model_name == "BAAI/bge-reranker-v2-m3"


# ─────────────────────── 缓存 key ───────────────────────


def test_cache_key_uses_resolved_model_name():
    """切模型后 cache key 必须不同,保证 reranker 单例自动重建。"""
    idx = _idx_with([
        _entry("models/rerank/A", "/m/A"),
        _entry("models/rerank/B", "/m/B"),
    ])
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value="models/rerank/A",
    ), mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        _, _, _, _, key_a, _ = _reranker_config()
    with mock.patch(
        "chayuan.server.capability_router.resolve_model",
        return_value="models/rerank/B",
    ), mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ):
        _, _, _, _, key_b, _ = _reranker_config()
    assert key_a != key_b
    assert "/m/A" in key_a
    assert "/m/B" in key_b
