"""事件回调分发器（outbound：Chayuan → App）。

场景：Chayuan 里发生事件（chat.completed / kb.doc.updated / tool.called 等），
需要把签名后的 JSON POST 给订阅了该事件的 App 的 ``callback_url``。

调用约定（对齐微信 / 飞书开放平台的 Webhook 规范）：
- POST 请求；
- Header 三件套：``X-App-Id / X-Timestamp / X-Sign``；
- 签名材料：``timestamp + "\\n" + raw_body``，HMAC-SHA256 over app_secret；
- body 固定为 JSON：
    {
      "event": "chat.completed",
      "ts": 1713700000,
      "data": { ... 事件具体负载 ... }
    }

接收端应：① 校验签名；② 快速返回 2xx；③ 异步处理避免阻塞回调。
本分发器默认 5s 超时 + 2 次重试；失败只打日志，**不阻塞主业务**。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List

from chayuan.server.shared.app_signing import make_signed_headers


logger = logging.getLogger("chayuan.callback")


_TIMEOUT = 5.0
_RETRIES = 2
_RETRY_SLEEP = 0.5


def dispatch_event(event: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """同步向所有订阅该事件的 App 回调 + 同步广播到 WS Hub。

    - HTTP 回调：失败不抛出，返回值里 ``ok=False`` 的条目附带 ``error``；
    - WS 广播：``ws_hub.broadcast_sync`` 投到事件循环，不在本函数等投递完成；
    - 两条通道互不影响——Webhook 慢不会拖累 WS，反之亦然；
    - 建议用 ``dispatch_event_async`` 在守护线程里调本函数，避免业务线程等网络。
    """
    # WS 侧：无论有没有 App 订阅 callback，都先把事件推到 hub；订阅匹配由 hub 做。
    try:
        from chayuan.server.shared.ws_hub import get_hub
        get_hub().broadcast_sync(event, data)
    except Exception:  # noqa: BLE001
        logger.exception("ws hub broadcast_sync failed")

    from chayuan.server.config_panel.apps_store import list_apps

    results: List[Dict[str, Any]] = []
    apps = [a for a in list_apps() if a.enabled and a.callback_url
            and event in (a.callback_events or [])]
    if not apps:
        return results

    payload = {"event": event, "ts": int(time.time()), "data": data}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for app in apps:
        r = _deliver_with_retry(
            url=app.callback_url, app_id=app.app_id,
            app_secret=app.app_secret, body=body,
        )
        r["app_id"] = app.app_id
        r["event"] = event
        results.append(r)
        if not r["ok"]:
            logger.warning(
                "callback delivery failed: app=%s url=%s error=%s",
                app.app_id, app.callback_url, r.get("error"),
            )
    return results


def dispatch_event_async(event: str, data: Dict[str, Any]) -> None:
    """在守护线程里触发回调；主业务不被拖慢。"""
    def _run():
        try:
            dispatch_event(event, data)
        except Exception as e:  # noqa: BLE001
            logger.exception("async callback dispatch crashed: %r", e)

    t = threading.Thread(target=_run, name=f"callback-{event}", daemon=True)
    t.start()


def _deliver_with_retry(
    *, url: str, app_id: str, app_secret: str, body: bytes,
) -> Dict[str, Any]:
    """用 raw body 发送请求，避免签名失效。"""
    import httpx

    last_err = ""
    for attempt in range(_RETRIES + 1):
        # 对同一份 bytes 重新计算签名（timestamp 可能变化）
        headers = make_signed_headers(app_id, app_secret, body)
        try:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as cli:
                resp = cli.post(url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                return {"ok": True, "status": resp.status_code, "attempt": attempt + 1}
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(_RETRY_SLEEP * (attempt + 1))
    return {"ok": False, "status": None, "error": last_err, "attempt": _RETRIES + 1}
