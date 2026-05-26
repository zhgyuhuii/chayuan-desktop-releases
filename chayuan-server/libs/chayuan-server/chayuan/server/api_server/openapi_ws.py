"""开放平台长连接端点：``/openapi/v1/ws``。

外部 App 用查询串签名建立 WS，之后按 ``op=subscribe/unsubscribe/ping`` 管理订阅。
服务端在 ``callback_dispatcher`` 发事件时，同步用 ``ws_hub.broadcast_sync`` 把事件
投递到匹配的连接。

查询串签名（与 HTTP 同一套 HMAC-SHA256）：

    wss://host/openapi/v1/ws?app_id=X&timestamp=T&sign=S
    sign = hmac_sha256( app_secret, T + "\n" + "" )

为什么 body 部分是空串：WS 握手没 body；"\n" 保留保持和 HTTP 版签名算法一致，
服务端调用 ``verify(secret, T, b"", sign)`` 即可校验。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from chayuan.server.shared.app_signing import verify
from chayuan.server.shared.ws_hub import (
    CLOSE_RESOURCE,
    Connection,
    MAX_FRAME_BYTES,
    get_hub,
    heartbeat_task,
    writer_task,
)


logger = logging.getLogger("chayuan.openapi.ws")

ws_router = APIRouter(prefix="/openapi/v1", tags=["OpenAPI WS"])


@ws_router.websocket("/ws")
async def openapi_ws(ws: WebSocket) -> None:
    # ---- 1) 握手鉴权 ----
    app_id = ws.query_params.get("app_id", "")
    timestamp = ws.query_params.get("timestamp", "")
    given_sign = ws.query_params.get("sign", "")
    if not app_id or not timestamp or not given_sign:
        await ws.close(code=4401, reason="missing app_id/timestamp/sign")
        return

    try:
        from chayuan.server.config_panel.apps_store import get_app
    except Exception:  # noqa: BLE001
        logger.exception("apps_store import failed")
        await ws.close(code=4500, reason="apps store error")
        return

    app = get_app(app_id)
    if not app or not app.enabled:
        await ws.close(code=4401, reason="unknown or disabled app")
        return

    ok, reason = verify(app.app_secret, timestamp, b"", given_sign)
    if not ok:
        await ws.close(code=4401, reason=f"invalid signature: {reason}")
        return

    # scope 闸门：必须显式授予 events:subscribe 才能开 WS 长连接
    from chayuan.server.shared.scopes import covers, sanitize_scope_list
    app_scopes = sanitize_scope_list(app.scopes)
    if not covers(app_scopes, "events:subscribe"):
        await ws.close(code=4403, reason="missing scope: events:subscribe")
        return

    await ws.accept()

    # ---- 2) 注册到 hub ----
    peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "-"
    conn = Connection(
        ws=ws, app_id=app_id, peer=peer,
        app_scopes=set(app_scopes),
        conn_id=uuid.uuid4().hex[:12],
    )
    ok, err = await get_hub().add(conn)
    if not ok:
        await ws.close(code=CLOSE_RESOURCE, reason=err)
        return

    # ---- 3) 给客户端一个 hello 帧 + 跑 writer / heartbeat ----
    try:
        await ws.send_json({
            "op": "hello", "app_id": app_id, "name": app.name,
            "conn_id": conn.conn_id,
            "server_time": int(time.time()),
            "hint": "send {\"op\":\"subscribe\",\"events\":[\"chat.completed\", \"kb.doc.*\"]}",
        })
    except Exception:  # noqa: BLE001
        await get_hub().remove(conn)
        return

    writer = asyncio.create_task(writer_task(conn), name=f"ws-writer-{conn.conn_id}")
    heart = asyncio.create_task(heartbeat_task(conn), name=f"ws-heart-{conn.conn_id}")

    # ---- 4) 主循环：处理客户端控制帧 ----
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            conn.last_activity = time.monotonic()

            if len(raw) > MAX_FRAME_BYTES:
                await ws.send_json({
                    "op": "error", "msg": f"frame too large: {len(raw)} > {MAX_FRAME_BYTES}",
                })
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"op": "error", "msg": "invalid json"})
                continue

            op = (msg or {}).get("op", "")
            if op == "subscribe":
                events = msg.get("events") or []
                if not isinstance(events, list):
                    await ws.send_json({"op": "error", "msg": "events must be array"})
                    continue
                from chayuan.server.shared.scopes import filter_subscribable
                accepted, rejected = filter_subscribable(
                    [str(e) for e in events], conn.app_scopes,
                )
                for e in accepted:
                    conn.subs.add(e)
                await ws.send_json({
                    "op": "subscribed",
                    "events": sorted(conn.subs),
                    "rejected": rejected,   # 告诉客户端哪些因 scope 不足被拒
                })
            elif op == "unsubscribe":
                events = msg.get("events") or []
                if not isinstance(events, list):
                    await ws.send_json({"op": "error", "msg": "events must be array"})
                    continue
                for e in events:
                    conn.subs.discard(str(e))
                await ws.send_json({"op": "subscribed", "events": sorted(conn.subs)})
            elif op == "ping":
                await ws.send_json({"op": "pong", "ts": int(time.time())})
            elif op == "pong":
                # 客户端对服务端 ping 的回应，用 last_activity 就够，不用额外处理
                pass
            else:
                await ws.send_json({"op": "error", "msg": f"unknown op: {op}"})
    except Exception as e:  # noqa: BLE001
        logger.info("ws loop ended: app=%s peer=%s err=%r", app_id, peer, e)
    finally:
        writer.cancel()
        heart.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        await get_hub().remove(conn)


@ws_router.get("/ws/stats")
async def ws_stats() -> Dict[str, Any]:
    """运维端点：当前 WS 连接总量 / 分布 / 限额。

    受 ``AppAuthMiddleware`` 保护，对接方凭自己的 app_id 就能看到全局。
    生产如果不希望对外暴露，可加 ``scopes=admin`` 后再前置一层作用域检查。
    """
    hub = get_hub()
    return {
        "stats": await hub.stats(),
        "connections": await hub.snapshot(),
    }
