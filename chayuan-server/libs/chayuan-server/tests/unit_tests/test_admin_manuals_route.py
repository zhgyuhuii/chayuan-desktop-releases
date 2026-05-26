"""``GET /admin/manuals/*`` 路由集成测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.admin_routes import admin_router
from chayuan.server.manuals.deploy import MANUAL_FILES, deploy_user_manuals


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(chayuan_root_tmp) -> TestClient:
    deploy_user_manuals()
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_list_endpoint_returns_manual_items(client):
    resp = client.get("/admin/manuals/list")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items
    first = items[0]
    assert "public_name" in first
    assert "md_path" in first
    assert "docx_path" in first
    assert first["md_exists"] is True


def test_download_md_returns_markdown(client):
    name = MANUAL_FILES[0].public_name
    resp = client.get(f"/admin/manuals/{name}", params={"fmt": "md"})
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    body = resp.text
    assert "察元" in body


def test_download_each_registered_manual(client):
    """所有已注册手册都应该可下载,内容非空。"""
    for spec in MANUAL_FILES:
        resp = client.get(f"/admin/manuals/{spec.public_name}", params={"fmt": "md"})
        assert resp.status_code == 200, f"{spec.public_name} 下载失败"
        assert len(resp.text) > 500, f"{spec.public_name} 内容过短"


def test_download_docx_returns_binary_or_404(client):
    """python-docx 装了就 200,没装就 404(都合法)。"""
    name = MANUAL_FILES[0].public_name
    resp = client.get(f"/admin/manuals/{name}", params={"fmt": "docx"})
    # 在带 python-docx 的开发环境会是 200;否则 404 也合法
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        ct = resp.headers["content-type"]
        assert "wordprocessingml" in ct or "vnd.openxmlformats" in ct
        assert len(resp.content) > 1000


def test_download_unknown_manual_returns_404(client):
    resp = client.get("/admin/manuals/不存在", params={"fmt": "md"})
    assert resp.status_code == 404


def test_download_rejects_invalid_fmt(client):
    name = MANUAL_FILES[0].public_name
    resp = client.get(f"/admin/manuals/{name}", params={"fmt": "pdf"})
    # FastAPI Query regex 校验失败 → 422
    assert resp.status_code == 422
