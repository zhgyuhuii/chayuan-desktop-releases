"""P1 工具中心新模块的单元测试。

覆盖:
  - ``chayuan.server.tools.catalog.load_catalog`` 与 NiceGUI 面板共用同一份
    dataclass(reuse 不破坏老代码)
  - ``chayuan.server.tools.config_service`` 读 catalog + 当前 yaml 值的合并视图
  - ``chayuan.server.tools.openapi_importer`` 解析 OpenAPI 3.x / Swagger 2.0
  - ``chayuan.server.tools.desc_synthesizer`` 三层兜底
  - ``api_server.tool_routes`` 新增的 7 个端点(端到端 TestClient smoke)

不依赖真实 ``$CHAYUAN_ROOT`` 数据(env 变量 isolate yaml 路径)。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_custom_tools_yaml(monkeypatch, tmp_path):
    """单测共享:让 custom_tools.yaml 走临时路径,不污染真实 ``$CHAYUAN_ROOT``。

    config_center 关掉,纯文件 IO,失败概率最低。
    """
    monkeypatch.setenv("CHAYUAN_CONFIG_CENTER_DISABLED", "1")
    monkeypatch.setenv("CHAYUAN_CUSTOM_TOOLS_YAML", str(tmp_path / "custom_tools.yaml"))


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def test_catalog_load_returns_specs_and_categories():
    from chayuan.server.tools.catalog import load_catalog
    specs, cats = load_catalog()
    assert specs, "目录至少要返回一个工具"
    assert cats, "categories 不应为空"
    keys = {s.key for s in specs}
    # 几个核心内置工具必须存在
    assert {"search_local_knowledgebase", "calculate"}.issubset(keys)


def test_catalog_dataclass_reused_in_nicegui_page():
    """tools_page.py 应该 import 共享 dataclass,不再持有副本。"""
    from chayuan.server.config_panel import tools_page
    from chayuan.server.tools.catalog import ToolField, ToolSpec
    assert tools_page.ToolField is ToolField
    assert tools_page.ToolSpec is ToolSpec


# ---------------------------------------------------------------------------
# desc_synthesizer
# ---------------------------------------------------------------------------


def test_desc_synthesizer_prefers_summary():
    from chayuan.server.tools.desc_synthesizer import synthesize
    out = synthesize(method="GET", url="http://x", summary="获取用户", description="详情")
    assert "获取用户" in out and "详情" in out


def test_desc_synthesizer_falls_back_to_signature():
    from chayuan.server.tools.desc_synthesizer import synthesize
    out = synthesize(method="POST", url="http://x/users", params=["a", "b"])
    assert "POST" in out and "/users" in out and "a" in out


# ---------------------------------------------------------------------------
# openapi_importer
# ---------------------------------------------------------------------------


def _openapi3_spec() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Demo", "version": "1.0"},
        "servers": [{"url": "http://api.example.com"}],
        "paths": {
            "/users/{id}": {
                "get": {
                    "operationId": "get_user",
                    "summary": "获取用户详情",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True,
                         "schema": {"type": "integer"}},
                        {"name": "fields", "in": "query",
                         "schema": {"type": "string"},
                         "description": "返回字段"},
                    ],
                },
            },
            "/users": {
                "post": {
                    "operationId": "create_user",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/User"},
                            },
                        },
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name"],
                },
            },
        },
    }


def _swagger2_spec() -> dict:
    return {
        "swagger": "2.0",
        "info": {"title": "Legacy", "version": "1"},
        "host": "legacy.example.com",
        "basePath": "/api/v1",
        "schemes": ["https"],
        "paths": {
            "/ping": {
                "get": {
                    "operationId": "ping",
                    "summary": "心跳",
                    "parameters": [],
                },
            },
        },
    }


def test_openapi3_basic_parse():
    from chayuan.server.tools.openapi_importer import parse_dict
    preview = parse_dict(_openapi3_spec())
    assert preview.base_url == "http://api.example.com"
    assert len(preview.drafts) == 2

    by_name = {d.name: d for d in preview.drafts}
    g = by_name["get_user"]
    assert g.method == "GET"
    assert g.url == "http://api.example.com/users/{id}"
    assert {p.name for p in g.params} == {"id", "fields"}
    assert next(p for p in g.params if p.name == "id").type == "integer"

    c = by_name["create_user"]
    assert c.method == "POST"
    body_params = [p for p in c.params if p.location == "body"]
    assert {p.name for p in body_params} == {"name", "age"}
    assert c.body_template and "name" in c.body_template


def test_swagger2_basic_parse():
    from chayuan.server.tools.openapi_importer import parse_dict
    preview = parse_dict(_swagger2_spec())
    assert preview.base_url == "https://legacy.example.com/api/v1"
    assert len(preview.drafts) == 1
    assert preview.drafts[0].name == "ping"
    assert preview.drafts[0].url.endswith("/ping")


def test_openapi_importer_normalizes_unsafe_names():
    """没有 operationId 时按 method+path 生成合法 Python 标识符。"""
    from chayuan.server.tools.openapi_importer import parse_dict
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x", "version": "1"},
        "servers": [{"url": "http://x"}],
        "paths": {"/users/{id}/posts": {"get": {}}},
    }
    preview = parse_dict(spec)
    name = preview.drafts[0].name
    assert name.replace("_", "").isalnum()
    assert not name[0].isdigit()


# ---------------------------------------------------------------------------
# REST 端点 smoke
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from chayuan.server.api_server.tool_routes import tool_router

    app = FastAPI()
    app.include_router(tool_router)
    return TestClient(app)


def test_get_catalog_returns_cards(client):
    r = client.get("/tools/catalog")
    assert r.status_code == 200
    body = r.json()["data"]
    assert "cards" in body and "categories" in body
    assert body["cards"], "目录里应该有内置工具"
    sample = body["cards"][0]
    assert "fields" in sample and "enabled" in sample


def test_get_tool_config_404_for_unknown(client):
    r = client.get("/tools/__nonexistent__/config")
    assert r.status_code == 404


def test_custom_tool_crud_flow(client):
    # 初始空
    r = client.get("/tools/custom")
    assert r.status_code == 200 and r.json()["data"] == []

    payload = {
        "name": "my_demo",
        "title": "演示",
        "description": "测试用",
        "method": "GET",
        "url": "https://example.com/api",
        "params": [{"name": "q", "type": "string", "required": True}],
    }
    r = client.post("/tools/custom", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "my_demo"

    # 重复创建 → 409
    r = client.post("/tools/custom", json=payload)
    assert r.status_code == 409

    # PUT 改 title
    upd = {**payload, "title": "演示-改"}
    r = client.put("/tools/custom/my_demo", json=upd)
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "演示-改"

    # PUT 不一致名 → 400
    r = client.put("/tools/custom/my_demo", json={**payload, "name": "renamed"})
    assert r.status_code == 400

    # DELETE
    r = client.delete("/tools/custom/my_demo")
    assert r.status_code == 200

    # 重复 DELETE → 404
    r = client.delete("/tools/custom/my_demo")
    assert r.status_code == 404


def test_openapi_preview_then_commit(client):
    r = client.post(
        "/tools/import/openapi/preview",
        json={"spec_text": json.dumps(_openapi3_spec())},
    )
    assert r.status_code == 200, r.text
    drafts = r.json()["data"]["drafts"]
    assert len(drafts) == 2

    # commit 这两条
    r = client.post(
        "/tools/import/openapi/commit",
        json={"drafts": drafts, "overwrite": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["total_created"] == 2
    assert body["total_skipped"] == 0

    # 重复 commit 应跳过(overwrite=False)
    r = client.post(
        "/tools/import/openapi/commit",
        json={"drafts": drafts, "overwrite": False},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["total_created"] == 0
    assert body["total_skipped"] == 2

    # overwrite=True 时变成 updated
    r = client.post(
        "/tools/import/openapi/commit",
        json={"drafts": drafts, "overwrite": True},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["total_updated"] == 2


def test_openapi_preview_400_when_no_input(client):
    r = client.post("/tools/import/openapi/preview", json={})
    assert r.status_code == 400
