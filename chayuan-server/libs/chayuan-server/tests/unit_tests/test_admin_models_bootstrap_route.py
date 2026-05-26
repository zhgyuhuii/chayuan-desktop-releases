"""``GET /admin/models/bootstrap`` 集成测试 — TestClient 起 FastAPI 子应用。

测试要点
========

* 端点不要求 admin 身份（sidecar-gate 在用户未登录时也要拿到）
* 返回字段稳定（``ready`` / ``missing`` / ``statuses`` / ``install_hints``）
* 缺模型时 install_hints 非空
"""
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


def _entry(model_id: str, capability: str) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format="gguf",
        family=capability, size_bytes=2048,
    )


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-admin-route-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_endpoint_returns_ready_when_all_present(client):
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        return_value=None,
    ):
        resp = client.get("/admin/models/bootstrap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["missing"] == []
    assert data["install_hints"] == []
    assert "statuses" in data
    assert len(data["statuses"]) == 3


def test_endpoint_returns_install_hints_when_missing(client):
    idx = _idx_with([_entry("qwen3-4b", "chat")])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        return_value=None,
    ):
        resp = client.get("/admin/models/bootstrap")
    data = resp.json()
    assert data["ready"] is False
    assert set(data["missing"]) == {"text-embedding", "rerank"}
    # 缺模型时 install_hints 非空
    assert data["install_hints"]
    # 应该推荐 lite
    assert data["install_hints"][0]["release"] == "lite"


def test_endpoint_supports_custom_required_query(client):
    idx = _idx_with([_entry("whisper", "speech-to-text")])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        return_value=None,
    ):
        resp = client.get(
            "/admin/models/bootstrap",
            params={"required": "speech-to-text", "do_scan": "false"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True


def test_endpoint_do_scan_false_skips_scan(client):
    """do_scan=false 时不应该触发 scan_once。"""
    idx = _idx_with([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-reranker", "rerank"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        return_value=None,
    ) as scan_mock:
        resp = client.get("/admin/models/bootstrap", params={"do_scan": "false"})
    assert resp.status_code == 200
    scan_mock.assert_not_called()


def test_endpoint_does_not_require_admin_role(client):
    """sidecar-gate 在未登录时也要能调用。

    端点上**不**挂 ``require_role`` 守卫；这里通过"不传任何 auth header
    仍然 200"反向证明。
    """
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.bootstrap.scan_once",
        return_value=None,
    ):
        resp = client.get("/admin/models/bootstrap")
    assert resp.status_code == 200
