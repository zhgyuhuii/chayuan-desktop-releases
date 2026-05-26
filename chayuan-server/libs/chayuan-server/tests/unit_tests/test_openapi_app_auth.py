"""开放平台签名 + App 鉴权 + 回调分发的端到端单测。

不启 uvicorn 真端口，用 FastAPI 自带 TestClient；
用临时 apps.yaml 隔离（通过修改 CHAYUAN_ROOT 环境变量在 import 前重定向）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def _isolated_yaml(monkeypatch, tmp_path):
    """只隔离 apps.yaml 与 custom_tools.yaml 两个文件，避免切 CHAYUAN_ROOT
    牵连 KB/DB/Settings 初始化链路。"""
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(tmp_path / "apps.yaml"))
    monkeypatch.setenv(
        "CHAYUAN_CUSTOM_TOOLS_YAML", str(tmp_path / "custom_tools.yaml"),
    )
    yield tmp_path


def test_signing_roundtrip_and_tamper_detection():
    from chayuan.server.shared.app_signing import (
        make_signed_headers, new_app_id, new_app_secret, verify,
    )

    aid = new_app_id()
    sec = new_app_secret()
    body = json.dumps({"a": 1, "中文": "值"}, ensure_ascii=False).encode()
    h = make_signed_headers(aid, sec, body)

    ok, _ = verify(sec, h["X-Timestamp"], body, h["X-Sign"])
    assert ok

    # 篡改 body
    ok, reason = verify(sec, h["X-Timestamp"], body + b"!", h["X-Sign"])
    assert not ok and "mismatch" in reason

    # 过期时间戳
    ok, reason = verify(sec, str(int(time.time()) - 99999), body, h["X-Sign"])
    assert not ok and "drift" in reason


def test_app_crud(_isolated_yaml):
    from chayuan.server.config_panel.apps_store import (
        create_app, delete_app, get_app, list_apps, rotate_secret, update_app,
    )

    assert list_apps() == []
    app = create_app("测试应用", callback_url="https://ex.com/cb",
                     callback_events=["chat.completed"], scopes=["chat", "kb"])
    assert len(app.app_id) == 32
    assert len(app.app_secret) >= 30
    assert get_app(app.app_id) is not None

    up = update_app(
        app.app_id,
        name="改名",
        enabled=False,
        ku_ids=["src:10"],
        tool_ids=["search_internet"],
    )
    assert up and up.name == "改名" and up.enabled is False
    assert up.ku_ids == ["src:10"]
    assert up.tool_ids == ["search_internet"]

    old_sec = app.app_secret
    rotated = rotate_secret(app.app_id)
    assert rotated and rotated.app_secret != old_sec

    assert delete_app(app.app_id) is True
    assert get_app(app.app_id) is None


def test_openapi_ping_requires_valid_signature(_isolated_yaml):
    """构造一个 minimal FastAPI app，只挂 AppAuthMiddleware + openapi_router，
    走 TestClient 验签。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from chayuan.server.api_server.openapi_routes import (
        AppAuthMiddleware, openapi_router,
    )
    from chayuan.server.config_panel.apps_store import create_app
    from chayuan.server.shared.app_signing import make_signed_headers

    app = FastAPI()
    app.add_middleware(AppAuthMiddleware)
    app.include_router(openapi_router)
    client = TestClient(app)

    spec = create_app("ci-test", scopes=["chat"])

    # 无签名：400
    r = client.get("/openapi/v1/ping")
    assert r.status_code == 400

    # 错误签名：401
    bad_headers = {"X-App-Id": spec.app_id, "X-Timestamp": str(int(time.time())),
                   "X-Sign": "deadbeef" * 8}
    r = client.get("/openapi/v1/ping", headers=bad_headers)
    assert r.status_code == 401

    # 正确签名：200
    good_headers = make_signed_headers(spec.app_id, spec.app_secret, b"")
    r = client.get("/openapi/v1/ping", headers=good_headers)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["app_id"] == spec.app_id

    # POST /echo：body bytes 参与签名
    payload = {"hello": "世界", "n": 3}
    raw = json.dumps(payload, ensure_ascii=False).encode()
    hdrs = make_signed_headers(spec.app_id, spec.app_secret, raw)
    r = client.post("/openapi/v1/echo", content=raw, headers=hdrs)
    assert r.status_code == 200
    assert r.json()["echo"] == payload

    # 禁用后 401
    from chayuan.server.config_panel.apps_store import update_app
    update_app(spec.app_id, enabled=False)
    r = client.get("/openapi/v1/ping", headers=make_signed_headers(
        spec.app_id, spec.app_secret, b"",
    ))
    assert r.status_code == 401


def test_openapi_scope_enforcement(_isolated_yaml):
    """/openapi/v1/* 按 scope 返回 200 / 403。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from chayuan.server.api_server.openapi_routes import (
        AppAuthMiddleware, openapi_router,
    )
    from chayuan.server.config_panel.apps_store import create_app, update_app
    from chayuan.server.shared.app_signing import make_signed_headers

    app = FastAPI()
    app.add_middleware(AppAuthMiddleware)
    app.include_router(openapi_router)
    client = TestClient(app)

    spec = create_app("scope-test", scopes=["chat:read"])

    # /ping: 无 scope 要求 → 200
    r = client.get("/openapi/v1/ping", headers=make_signed_headers(
        spec.app_id, spec.app_secret, b"",
    ))
    assert r.status_code == 200

    # /chat/history: 需 chat:read → 200
    import json as _json
    r = client.get(
        "/openapi/v1/chat/history?conversation_id=abc",
        headers=make_signed_headers(spec.app_id, spec.app_secret, b""),
    )
    assert r.status_code == 200

    # /chat/complete: 需 chat:write → 403
    payload = {"prompt": "hi"}
    raw = _json.dumps(payload).encode()
    r = client.post(
        "/openapi/v1/chat/complete",
        content=raw,
        headers=make_signed_headers(spec.app_id, spec.app_secret, raw),
    )
    assert r.status_code == 403
    body = r.json()
    assert "chat:write" in body["detail"]["missing"]

    # 升级到 chat:* 再试 → 200
    update_app(spec.app_id, scopes=["chat:*"])
    r = client.post(
        "/openapi/v1/chat/complete",
        content=raw,
        headers=make_signed_headers(spec.app_id, spec.app_secret, raw),
    )
    assert r.status_code == 200

    # /admin/apps: 需 admin:read → 仍然 403
    r = client.get(
        "/openapi/v1/admin/apps",
        headers=make_signed_headers(spec.app_id, spec.app_secret, b""),
    )
    assert r.status_code == 403

    # 超管 *:* 全通
    update_app(spec.app_id, scopes=["*:*"])
    r = client.get(
        "/openapi/v1/admin/apps",
        headers=make_signed_headers(spec.app_id, spec.app_secret, b""),
    )
    assert r.status_code == 200


def test_custom_tool_dynamic_registration(_isolated_yaml):
    """自定义工具：yaml 写入 → 动态注册 → 出现在 _TOOLS_REGISTRY。"""
    from chayuan.server.config_panel.custom_tools_store import (
        CustomToolSpec, ParamSpec, save_tool,
    )
    from chayuan.server.agent.tools_factory import custom_tools_runtime
    from chayuan.server.agent.tools_factory.tools_registry import _TOOLS_REGISTRY

    spec = CustomToolSpec(
        name="unit_test_tool",
        title="UT Tool",
        description="just for unit test",
        method="GET",
        url="https://httpbin.org/get",
        params=[ParamSpec(name="q", type="string", required=True)],
        enabled=True,
    )
    save_tool(spec)
    custom_tools_runtime.load_and_register()
    assert "unit_test_tool" in _TOOLS_REGISTRY

    # 禁用后 reload 应该被卸载
    spec.enabled = False
    save_tool(spec)
    custom_tools_runtime.load_and_register()
    assert "unit_test_tool" not in _TOOLS_REGISTRY
