"""GET/POST /runtime/llama/{capability}/* + /runtime/llama/registry 路由测试。"""
from __future__ import annotations

from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.runtime_routes import runtime_router
from chayuan.server.model_registry.local_runtime import RuntimeStatus


@pytest.fixture
def client(monkeypatch):
    """注入 fake registry 让 4 个 capability 全可 mock。"""
    fake_managers = {}
    caps = ("chat", "embedding", "rerank", "asr", "image-embedding")
    for cap in caps:
        fm = mock.MagicMock()
        fm.status = RuntimeStatus(state="stopped")
        idx = caps.index(cap)
        fm.start = mock.AsyncMock(return_value=RuntimeStatus(state="ready", endpoint=f"http://127.0.0.1:{62582 + idx}"))
        fm.stop = mock.AsyncMock(return_value=None)
        fm.restart = mock.AsyncMock(return_value=RuntimeStatus(state="ready"))
        fake_managers[cap] = fm

    fake_registry = mock.MagicMock()
    fake_registry._managers = fake_managers

    def fake_get(cap):
        if cap not in fake_managers:
            raise ValueError(f"unknown capability: {cap!r}")
        return fake_managers[cap]
    fake_registry.get = fake_get
    fake_registry.all_statuses = lambda: {cap: fm.status for cap, fm in fake_managers.items()}

    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: fake_registry,
    )

    app = FastAPI()
    app.include_router(runtime_router)
    return TestClient(app), fake_managers


def test_llama_registry_returns_five_caps(client):
    c, _ = client
    r = c.get("/runtime/llama/registry")
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data.keys()) == {"chat", "embedding", "rerank", "asr", "image-embedding"}


def test_llama_capability_status_chat(client):
    c, fms = client
    fms["chat"].status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62582", pid=1)
    r = c.get("/runtime/llama/chat/status")
    assert r.status_code == 200
    assert r.json()["data"]["state"] == "ready"


def test_llama_capability_status_embedding(client):
    c, fms = client
    fms["embedding"].status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62583", pid=2)
    r = c.get("/runtime/llama/embedding/status")
    assert r.status_code == 200
    assert r.json()["data"]["endpoint"] == "http://127.0.0.1:62583"


def test_llama_capability_status_unknown_400(client):
    c, _ = client
    r = c.get("/runtime/llama/tts/status")
    assert r.status_code == 400


def test_llama_capability_start_embedding(client):
    c, fms = client
    r = c.post("/runtime/llama/embedding/start")
    assert r.status_code == 200
    fms["embedding"].start.assert_awaited_once()
    fms["chat"].start.assert_not_called()


def test_llama_capability_stop_rerank(client):
    c, fms = client
    r = c.post("/runtime/llama/rerank/stop")
    assert r.status_code == 200
    fms["rerank"].stop.assert_awaited_once()


def test_llama_capability_restart_chat(client):
    c, fms = client
    r = c.post("/runtime/llama/chat/restart")
    assert r.status_code == 200
    fms["chat"].restart.assert_awaited_once()


def test_llama_capability_status_asr(client):
    c, fms = client
    fms["asr"].status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62585", pid=99)
    r = c.get("/runtime/llama/asr/status")
    assert r.status_code == 200
    assert r.json()["data"]["state"] == "ready"
    assert r.json()["data"]["endpoint"] == "http://127.0.0.1:62585"


def test_llama_capability_start_asr(client):
    c, fms = client
    r = c.post("/runtime/llama/asr/start")
    assert r.status_code == 200
    fms["asr"].start.assert_awaited_once()
    fms["chat"].start.assert_not_called()


def test_llama_capability_status_image_embedding(client):
    c, fms = client
    fms["image-embedding"].status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62586", pid=88)
    r = c.get("/runtime/llama/image-embedding/status")
    assert r.status_code == 200
    assert r.json()["data"]["state"] == "ready"
    assert r.json()["data"]["endpoint"] == "http://127.0.0.1:62586"


def test_llama_capability_start_image_embedding(client):
    c, fms = client
    r = c.post("/runtime/llama/image-embedding/start")
    assert r.status_code == 200
    fms["image-embedding"].start.assert_awaited_once()
    fms["chat"].start.assert_not_called()
