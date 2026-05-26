"""``/runtime/llama/*`` 路由测试。

mock LlamaRuntimeManager,验证路由 → manager 方法的转发 + 响应 JSON 结构。
"""
from __future__ import annotations

from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.runtime_routes import runtime_router
from chayuan.server.model_registry.local_runtime import RuntimeStatus


@pytest.fixture
def client(monkeypatch):
    fake = mock.MagicMock()
    fake.status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62582", pid=1234)
    fake.start = mock.AsyncMock(return_value=fake.status)
    fake.stop = mock.AsyncMock(return_value=None)
    fake.restart = mock.AsyncMock(return_value=fake.status)
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime.get_manager",
        lambda: fake,
    )
    app = FastAPI()
    app.include_router(runtime_router)
    return TestClient(app), fake


def test_llama_status(client):
    c, _ = client
    r = c.get("/runtime/llama/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["state"] == "ready"
    assert data["endpoint"] == "http://127.0.0.1:62582"


def test_llama_start(client):
    c, fake = client
    r = c.post("/runtime/llama/start")
    assert r.status_code == 200
    fake.start.assert_called_once()


def test_llama_stop(client):
    c, fake = client
    r = c.post("/runtime/llama/stop")
    assert r.status_code == 200
    fake.stop.assert_called_once()


def test_llama_restart(client):
    c, fake = client
    r = c.post("/runtime/llama/restart")
    assert r.status_code == 200
    fake.restart.assert_called_once()


def test_llama_config_get(client):
    c, fake = client
    from chayuan.server.model_registry.local_runtime import LocalRuntimeSettings
    fake.settings = LocalRuntimeSettings()

    r = c.get("/runtime/llama/config")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["port"] == 62582
    assert data["preload_on_startup"] is True


def test_llama_config_post(client):
    c, fake = client
    from chayuan.server.model_registry.local_runtime import LocalRuntimeSettings
    fake.settings = LocalRuntimeSettings()
    fake.set_config = mock.MagicMock(return_value=LocalRuntimeSettings(port=62590, expose_lan=True))

    r = c.post("/runtime/llama/config", json={"port": 62590, "expose_lan": True})
    assert r.status_code == 200
    fake.set_config.assert_called_once()
    args, _ = fake.set_config.call_args
    cfg_update = args[0]
    assert cfg_update["port"] == 62590
    assert cfg_update["expose_lan"] is True


def test_llama_config_post_port_not_int_returns_422(client):
    """前端传 port='abc' 应该 422,不是 500 ValueError"""
    c, fake = client
    fake.set_config = mock.MagicMock()
    r = c.post("/runtime/llama/config", json={"port": "abc"})
    assert r.status_code == 422
    assert "port" in r.json().get("detail", "")
    fake.set_config.assert_not_called()


def test_llama_config_post_port_out_of_range_returns_422(client):
    """port=80 (< 1024) 应该 422"""
    c, fake = client
    fake.set_config = mock.MagicMock()
    r = c.post("/runtime/llama/config", json={"port": 80})
    assert r.status_code == 422
    fake.set_config.assert_not_called()


def test_llama_config_post_port_null_ok(client):
    """body 不带 port (None) 应该通过校验,直接 forward 给 set_config"""
    from chayuan.server.model_registry.local_runtime import LocalRuntimeSettings
    c, fake = client
    fake.set_config = mock.MagicMock(return_value=LocalRuntimeSettings(api_key="k"))
    r = c.post("/runtime/llama/config", json={"api_key": "k"})
    assert r.status_code == 200
    fake.set_config.assert_called_once()


def test_llama_install_info(client, tmp_path):
    c, fake = client
    fake.chayuan_root = tmp_path
    (tmp_path / "models" / "bundled").mkdir(parents=True)
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    (services / "VERSION").write_text("b4404\n")
    fake.find_llama_server_exe = mock.MagicMock(return_value=services / "llama-server.exe")

    r = c.get("/runtime/llama/install-info")
    assert r.status_code == 200
    d = r.json()["data"]
    assert "models_root" in d
    assert "llama_server_exe" in d
    assert d["llama_server_exe"].endswith("llama-server.exe")
    assert d["build_version"] == "b4404"
