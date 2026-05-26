"""``POST /admin/models/scan_and_mount`` 集成测试 — cap-scoped 行为。

测试要点
========
* 不传 ``capability`` → 全量扫描(scan_once 不带 bundled_cap_dir),
  summary 反映所有 cap。
* 传 ``capability`` → scan_once 带上对应 bundled 子目录,summary / auto_started
  只反映该 cap,不把别的 cap 的算进来。
* capability 短名(``embedding`` / ``rerank``)能被识别;非法 cap 回 400。
"""
from __future__ import annotations

import tempfile
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.admin_routes import admin_router
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
    ScanDelta,
)


def _entry(model_id: str, capability: str) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format="gguf",
        family=capability, size_bytes=2048,
    )


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-scan-route-test-"))
    p = td / "local_models.json"
    p.write_text(
        json.dumps({"version": 1, "items": [e.to_dict() for e in entries]}),
        encoding="utf-8",
    )
    return LocalModelIndex(p)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_scan_no_capability_does_full_scan(client):
    """不传 capability:scan_once 不带 bundled_cap_dir,summary 含所有 cap。"""
    idx = _idx_with([
        _entry("models/bundled/rerank/r1", "rerank"),
        _entry("models/bundled/embedding/e1", "text-embedding"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.scan_once",
        return_value=ScanDelta(),
    ) as scan_mock, mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.install_model.trigger_auto_start_for_cap",
        return_value=False,
    ):
        resp = client.post("/admin/models/scan_and_mount")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 全量:scan_once(bundled_cap_dir=None)
    assert scan_mock.call_args.kwargs.get("bundled_cap_dir") is None
    assert data["summary"]["total"] == 2
    assert set(data["summary"]["by_capability"]) == {"rerank", "text-embedding"}


def test_scan_with_capability_scopes_to_that_cap(client):
    """传 capability=rerank:scan_once 带 bundled_cap_dir='rerank',summary 只含 rerank。"""
    idx = _idx_with([
        _entry("models/bundled/rerank/r1", "rerank"),
        _entry("models/bundled/embedding/e1", "text-embedding"),
    ])
    with mock.patch(
        "chayuan.server.model_registry.local_index.scan_once",
        return_value=ScanDelta(),
    ) as scan_mock, mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.install_model.trigger_auto_start_for_cap",
        return_value=False,
    ) as auto_mock:
        resp = client.post("/admin/models/scan_and_mount", json={"capability": "rerank"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    # rerank → bundled 子目录就叫 rerank
    assert scan_mock.call_args.kwargs.get("bundled_cap_dir") == "rerank"
    # summary 只反映 rerank,不把 text-embedding 的算进来
    assert data["summary"]["total"] == 1
    assert set(data["summary"]["by_capability"]) == {"rerank"}
    # auto-start 只对当前 cap 触发一次
    assert auto_mock.call_count == 1
    assert auto_mock.call_args.args[0] == "rerank"


def test_scan_embedding_short_name_maps_to_embedding_dir(client):
    """短名 embedding → catalog 长名 text-embedding → bundled 子目录 embedding。"""
    idx = _idx_with([_entry("models/bundled/embedding/e1", "text-embedding")])
    with mock.patch(
        "chayuan.server.model_registry.local_index.scan_once",
        return_value=ScanDelta(),
    ) as scan_mock, mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.install_model.trigger_auto_start_for_cap",
        return_value=False,
    ):
        resp = client.post(
            "/admin/models/scan_and_mount", json={"capability": "embedding"}
        )
    assert resp.status_code == 200
    # embedding(短名) / text-embedding(长名)都映射到 bundled/embedding/
    assert scan_mock.call_args.kwargs.get("bundled_cap_dir") == "embedding"


def test_scan_image_embedding_maps_to_image_dir(client):
    """image-embedding 走 bundled/image/ 子目录(不是 image-embedding/)。"""
    idx = _idx_with([])
    with mock.patch(
        "chayuan.server.model_registry.local_index.scan_once",
        return_value=ScanDelta(),
    ) as scan_mock, mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.model_registry.install_model.trigger_auto_start_for_cap",
        return_value=False,
    ):
        resp = client.post(
            "/admin/models/scan_and_mount", json={"capability": "image-embedding"}
        )
    assert resp.status_code == 200
    assert scan_mock.call_args.kwargs.get("bundled_cap_dir") == "image"


def test_scan_unknown_capability_returns_400(client):
    """非法 capability 回 400,不静默全量扫描。"""
    resp = client.post(
        "/admin/models/scan_and_mount", json={"capability": "not-a-real-cap"}
    )
    assert resp.status_code == 400
