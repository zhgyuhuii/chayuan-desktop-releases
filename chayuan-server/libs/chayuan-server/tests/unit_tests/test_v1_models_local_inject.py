"""``GET /v1/models`` 把 local_index 扫到的本地模型作为独立分组注入测试。

预期:
* 至少有一条 ``platform_name='local'`` / ``platform_display_name='本地模型'``;
* ``model_type`` 按 entry.capability 翻译(chat → llm, text-embedding → embed, ...);
* ``?type=llm`` 过滤只剩对话类的本地模型,non-llm 不出现;
* 配置端没有任何云厂商时,本地模型依然能出现(不依赖云配置)。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.openai_routes import openai_router
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _entry(model_id: str, capability: str) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format="gguf",
        family=capability, size_bytes=2048,
    )


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-v1models-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(openai_router)
    return TestClient(app)


def test_local_chat_models_appear_as_dedicated_platform(client):
    fake_idx = _idx_with([
        _entry("bundled/chat/qwen3-4b.gguf", "chat"),
        _entry("bundled/embedding/bge-m3.onnx", "text-embedding"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=fake_idx,
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={},
    ):
        r = client.get("/v1/models")
    assert r.status_code == 200
    items = r.json()["data"]
    local_items = [m for m in items if m.get("platform_name") == "local"]
    assert len(local_items) == 2, f"expected 2 local, got {local_items}"

    chat = next(m for m in local_items if m["model_type"] == "llm")
    assert chat["id"] == "bundled/chat/qwen3-4b.gguf"
    assert chat["platform_display_name"] == "本地模型"
    assert chat["owned_by"] == "local"
    assert chat["available"] is True

    embed = next(m for m in local_items if m["model_type"] == "embed")
    assert embed["id"] == "bundled/embedding/bge-m3.onnx"
    assert embed["platform_display_name"] == "本地模型"


def test_type_llm_filter_excludes_non_chat_locals(client):
    fake_idx = _idx_with([
        _entry("bundled/chat/q.gguf", "chat"),
        _entry("bundled/embedding/e.onnx", "text-embedding"),
        _entry("bundled/rerank/r", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=fake_idx,
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={},
    ):
        r = client.get("/v1/models", params={"type": "llm"})
    assert r.status_code == 200
    items = r.json()["data"]
    local = [m for m in items if m.get("platform_name") == "local"]
    assert len(local) == 1
    assert local[0]["id"] == "bundled/chat/q.gguf"
    assert local[0]["model_type"] == "llm"


def test_unknown_capability_is_dropped(client):
    """capability='other' 或不在映射表里的不应出现在 /v1/models 里。"""
    fake_idx = _idx_with([
        _entry("bundled/custom/unknown.bin", "other"),
        _entry("bundled/chat/q.gguf", "chat"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=fake_idx,
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={},
    ):
        r = client.get("/v1/models")
    items = r.json()["data"]
    local = [m for m in items if m.get("platform_name") == "local"]
    assert len(local) == 1
    assert local[0]["id"] == "bundled/chat/q.gguf"


def test_empty_local_index_does_not_break(client):
    fake_idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=fake_idx,
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={},
    ):
        r = client.get("/v1/models")
    assert r.status_code == 200
    items = r.json()["data"]
    assert [m for m in items if m.get("platform_name") == "local"] == []


def test_local_index_import_failure_does_not_break(client):
    """get_local_index 抛异常时,/v1/models 仍返回(没有 local 条目)。"""
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        side_effect=RuntimeError("disk gone"),
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={},
    ):
        r = client.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_local_and_cloud_coexist(client):
    """同时有云厂商和本地模型时,两个 platform_name 都出现在返回里。"""
    fake_idx = _idx_with([_entry("bundled/chat/q.gguf", "chat")])
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=fake_idx,
    ), mock.patch(
        "chayuan.server.api_server.openai_routes.get_config_platforms",
        return_value={
            "deepseek": {
                "enabled": True,
                "platform_type": "openai",
                "llm_models": ["deepseek-chat"],
                "embed_models": [],
                "rerank_models": [],
                "text2image_models": [],
                "image2text_models": [],
                "speech2text_models": [],
                "text2speech_models": [],
                "disabled_models": [],
            },
        },
    ):
        r = client.get("/v1/models")
    items = r.json()["data"]
    platforms = {m.get("platform_name") for m in items}
    assert "local" in platforms
    assert "deepseek" in platforms
