"""GET /runtime/diagnose 路由集成测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.runtime_routes import runtime_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(runtime_router)
    return TestClient(app)


def test_runtime_diagnose_returns_ok_envelope(client):
    r = client.get("/runtime/diagnose")
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 0
    data = body["data"]
    assert "timestamp" in data
    assert "platform" in data
    assert "checks" in data
    assert "summary" in data
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) == 14
    assert data["summary"]["ok"] + data["summary"]["warn"] + data["summary"]["fail"] == 14


def test_runtime_diagnose_each_check_has_required_fields(client):
    r = client.get("/runtime/diagnose")
    for c in r.json()["data"]["checks"]:
        assert "name" in c
        assert "ok" in c
        assert "severity" in c
        assert c["severity"] in ("ok", "warn", "fail")
        assert "detail" in c
