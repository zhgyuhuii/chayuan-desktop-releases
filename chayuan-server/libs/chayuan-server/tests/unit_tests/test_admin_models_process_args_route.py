"""``GET /admin/models/process_args`` 集成测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.admin_routes import admin_router
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _entry(model_id: str, capability: str, fmt: str = "gguf",
           path: str = "") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=path or f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format=fmt,
        family=capability.replace("-", "_"),
        size_bytes=1024 * 1024 * 250,
    )


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-pa-route-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_returns_three_processes(client):
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.process_args.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.process_args._load_defaults",
        return_value={},
    ):
        resp = client.get("/admin/models/process_args")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data.keys()) == {"llamacpp", "infinity", "ollama"}


def test_resolved_llamacpp_args_when_chat_available(client):
    idx = _idx_with([
        _entry("qwen3-4b", "chat", fmt="gguf", path="/m/q.gguf"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.process_args.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.process_args._load_defaults",
        return_value={"chat": "qwen3-4b"},
    ):
        resp = client.get("/admin/models/process_args")
    payload = resp.json()["data"]["llamacpp"]
    assert payload["ok"] is True
    assert "--model" in payload["args"]
    assert "/m/q.gguf" in payload["args"]
    assert payload["resolved_models"]["chat"] == "qwen3-4b"


def test_missing_chat_reflected_in_payload(client):
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.process_args.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.process_args._load_defaults",
        return_value={},
    ):
        resp = client.get("/admin/models/process_args")
    llamacpp = resp.json()["data"]["llamacpp"]
    assert llamacpp["ok"] is False
    assert "chat" in llamacpp["missing"]


def test_does_not_require_admin_role(client):
    """与 /admin/models/bootstrap 一致 —— 首启 / GUI 调试不需要 admin。"""
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.process_args.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.process_args._load_defaults",
        return_value={},
    ):
        resp = client.get("/admin/models/process_args")
    assert resp.status_code == 200
