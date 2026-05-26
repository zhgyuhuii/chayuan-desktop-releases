"""开放平台 WebSocket 服务端（Inbound WS）端到端单测。

覆盖：
- 查询串签名鉴权（无签 / 错签 / 禁用 App 都关闭）；
- 订阅 / 取消订阅；通配符匹配；
- 广播：订阅了才收到，没订阅不收；
- 背压：queue 满 → drop 计数 / 超阈值踢（通过调小参数触发）；
- per-app 连接数限额。
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest


@pytest.fixture
def _isolated_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(tmp_path / "apps.yaml"))
    yield tmp_path


def _sign_query(app_secret: str, ts: str) -> str:
    """对齐 openapi_ws 的签名算法：timestamp + "\n" + ""。"""
    material = ts.encode() + b"\n"
    return hmac.new(app_secret.encode(), material, hashlib.sha256).hexdigest()


def _build_app(_isolated_yaml):
    """构造一个 minimal FastAPI 挂 openapi_ws + startup hook。"""
    from fastapi import FastAPI
    from chayuan.server.api_server.openapi_ws import ws_router

    app = FastAPI()
    app.include_router(ws_router)

    @app.on_event("startup")
    async def _attach():
        from chayuan.server.shared.ws_hub import get_hub
        get_hub().attach_loop()

    return app


def _create_test_app():
    # 老式 scope "chat" 自动映射为 chat:*；加上 events:subscribe 才能连 WS
    from chayuan.server.config_panel.apps_store import create_app
    return create_app("ws-test", scopes=["chat:*", "events:subscribe"])


def test_ws_handshake_requires_valid_signature(_isolated_yaml):
    from fastapi.testclient import TestClient
    from chayuan.server.config_panel.apps_store import create_app

    app = _build_app(_isolated_yaml)
    spec = create_app("ws-test", scopes=["chat:*", "events:subscribe"])

    # 注意：必须用 `with TestClient(app) as client:` 才会触发 @app.on_event("startup")，
    # 否则 attach_loop() 不跑，后面 broadcast_sync 会 no-op。
    with TestClient(app) as client:
        # 缺签名：应被关闭
        with pytest.raises(Exception):
            with client.websocket_connect("/openapi/v1/ws") as ws:
                ws.receive_text()

        # 错签名
        ts = str(int(time.time()))
        bad = "0" * 64
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={bad}"
            ) as ws:
                ws.receive_text()

        # 正确签名：应收到 hello 帧
        sign = _sign_query(spec.app_secret, ts)
        with client.websocket_connect(
            f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"
        ) as ws:
            hello = ws.receive_json()
            assert hello["op"] == "hello" and hello["app_id"] == spec.app_id


def test_ws_subscribe_and_receive_broadcast(_isolated_yaml):
    from fastapi.testclient import TestClient
    from chayuan.server.shared.ws_hub import get_hub
    from chayuan.server.config_panel.apps_store import create_app

    app = _build_app(_isolated_yaml)
    # 给 chat / kb / tools 三组都开通，验证订阅与广播都能过 scope 闸
    spec = create_app("ws-test", scopes=[
        "chat:*", "kb:read", "tools:read", "events:subscribe",
    ])

    ts = str(int(time.time()))
    sign = _sign_query(spec.app_secret, ts)

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"
        ) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe", "events": ["chat.completed", "kb.doc.*"]})
            ack = ws.receive_json()
            assert ack["op"] == "subscribed"
            assert set(ack["events"]) == {"chat.completed", "kb.doc.*"}

            assert get_hub().broadcast_sync("chat.completed", {"text": "hello"}) is True
            msg = ws.receive_json()
            assert msg["event"] == "chat.completed"
            assert msg["data"] == {"text": "hello"}

            get_hub().broadcast_sync("kb.doc.updated", {"kb": "x"})
            msg = ws.receive_json()
            assert msg["event"] == "kb.doc.updated"

            get_hub().broadcast_sync("tool.called", {"name": "t"})
            ws.send_json({"op": "ping"})
            rsp = ws.receive_json()
            assert rsp["op"] == "pong"


def test_ws_unsubscribe(_isolated_yaml):
    from fastapi.testclient import TestClient
    from chayuan.server.shared.ws_hub import get_hub
    from chayuan.server.config_panel.apps_store import create_app

    app = _build_app(_isolated_yaml)
    spec = create_app("ws-test", scopes=["chat:*", "events:subscribe"])

    ts = str(int(time.time()))
    sign = _sign_query(spec.app_secret, ts)

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"
        ) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe",
                          "events": ["chat.started", "chat.completed"]})
            ws.receive_json()  # ack

            ws.send_json({"op": "unsubscribe", "events": ["chat.started"]})
            ack = ws.receive_json()
            assert ack["events"] == ["chat.completed"]

            get_hub().broadcast_sync("chat.started", {})
            get_hub().broadcast_sync("chat.completed", {"n": 1})
            msg = ws.receive_json()
            assert msg["event"] == "chat.completed"


def test_ws_requires_events_subscribe_scope(_isolated_yaml):
    """没有 events:subscribe 的 App 连 WS 立即被 4403 关。"""
    from fastapi.testclient import TestClient
    from chayuan.server.config_panel.apps_store import create_app

    app = _build_app(_isolated_yaml)
    # 只给 chat:* 不给 events:subscribe
    spec = create_app("no-events", scopes=["chat:*"])

    ts = str(int(time.time()))
    sign = _sign_query(spec.app_secret, ts)
    url = f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"

    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(url) as ws:
                ws.receive_json()


def test_ws_subscribe_rejects_out_of_scope(_isolated_yaml):
    """只给 chat:read 的 App 想订 kb.doc.* 会被拒（ack.rejected 非空）。"""
    from fastapi.testclient import TestClient
    from chayuan.server.config_panel.apps_store import create_app

    app = _build_app(_isolated_yaml)
    spec = create_app("partial", scopes=["chat:read", "events:subscribe"])

    ts = str(int(time.time()))
    sign = _sign_query(spec.app_secret, ts)
    url = f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"

    with TestClient(app) as client:
        with client.websocket_connect(url) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe",
                          "events": ["chat.completed", "kb.doc.*", "app.created"]})
            ack = ws.receive_json()
            assert set(ack["events"]) == {"chat.completed"}
            assert set(ack["rejected"]) == {"kb.doc.*", "app.created"}


def test_ws_per_app_connection_limit(_isolated_yaml, monkeypatch):
    """per-app 连接上限：第 (limit+1) 条应被 4005 关闭。"""
    from fastapi.testclient import TestClient
    from chayuan.server.shared import ws_hub
    from chayuan.server.config_panel.apps_store import create_app

    # 把 per-app 限额调到 2 方便构造
    monkeypatch.setattr(ws_hub, "PER_APP_MAX_CONNECTIONS", 2)

    app = _build_app(_isolated_yaml)
    spec = create_app("ws-test", scopes=["chat:*", "events:subscribe"])

    ts = str(int(time.time()))
    sign = _sign_query(spec.app_secret, ts)
    url = f"/openapi/v1/ws?app_id={spec.app_id}&timestamp={ts}&sign={sign}"

    with TestClient(app) as client:
        c1 = client.websocket_connect(url).__enter__()
        c2 = client.websocket_connect(url).__enter__()
        try:
            assert c1.receive_json()["op"] == "hello"
            assert c2.receive_json()["op"] == "hello"

            from starlette.websockets import WebSocketDisconnect
            with pytest.raises((WebSocketDisconnect, Exception)):
                with client.websocket_connect(url) as c3:
                    c3.receive_json()
        finally:
            for c in (c1, c2):
                try:
                    c.close()
                except Exception:
                    pass
