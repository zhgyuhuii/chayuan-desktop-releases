"""配置变更订阅。

分两层：
- **进程内**：业务 store（apps_store / custom_tools_store / ws_endpoints_store）
  启动时调 ``register_callback(ns, cb)`` 注册，事件到达时触发 `cb(event)`。
- **跨副本**：后台 asyncio task 订阅 Redis ``chayuan:config:events``，收到事件后
  也调 ``dispatch_local``。

这样：单副本 & 多副本 & Redis 挂掉 三种部署都能热更新。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger("chayuan.config_center.subscribe")


# {namespace: [callback1, callback2, ...]}；``*`` 作为 key 表示订阅全部 namespace
_LOCAL: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
_LOCAL_LOCK = threading.Lock()

_REDIS_TASK: Optional[asyncio.Task] = None
_REDIS_STOP = threading.Event()


def register_callback(
    namespace: str, cb: Callable[[Dict[str, Any]], None],
) -> None:
    """在业务 store 的模块 init 时调。回调签名：``cb(event: dict)``。

    ``event`` 形如 ``{op, namespace, key, version, ts}``；回调里做什么都行，
    典型是「清本地 LRU 缓存 + 重新 load」或「直接调 ``get_hub().broadcast_sync``」。
    """
    with _LOCAL_LOCK:
        _LOCAL.setdefault(namespace, []).append(cb)


def dispatch_local(event: Dict[str, Any]) -> None:
    """被 ``store._publish_event`` 在 Redis 缺失时直接调；也被 Redis 订阅 task 调。"""
    ns = event.get("namespace") or ""
    with _LOCAL_LOCK:
        cbs = list(_LOCAL.get(ns, [])) + list(_LOCAL.get("*", []))
    for cb in cbs:
        try:
            cb(event)
        except Exception:  # noqa: BLE001
            logger.exception("config subscriber callback failed: %s", cb)


# ---------------------------------------------------------------------------
# Redis 订阅后台 task
# ---------------------------------------------------------------------------

async def _redis_loop() -> None:
    from chayuan.server.shared import get_redis

    # 全局 redis client 的 socket_timeout=2s 是为短 RPC 设计的；对 pubsub.listen
    # 这种长阻塞而言会每 2s 触发 TimeoutError → 日志刷屏。这里单独在 pubsub 层
    # catch 然后 continue，不把它当成 crash 重连。
    try:
        from redis.exceptions import TimeoutError as RedisTimeoutError
    except Exception:  # noqa: BLE001
        RedisTimeoutError = Exception  # type: ignore

    while not _REDIS_STOP.is_set():
        r = get_redis()
        if r is None:
            await asyncio.sleep(3)
            continue
        pubsub = None
        try:
            pubsub = r.pubsub()
            await pubsub.subscribe("chayuan:config:events")
            while not _REDIS_STOP.is_set():
                try:
                    # 用 get_message(timeout=...) 而不是 async for pubsub.listen()；
                    # 这样 timeout 走正常 return None，我们直接 continue 循环。
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=15.0,
                    )
                except RedisTimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.warning("pubsub get_message err: %r", e)
                    await asyncio.sleep(1)
                    continue
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    data = msg.get("data") or "{}"
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode("utf-8", "replace")
                    data = json.loads(data)
                except Exception:  # noqa: BLE001
                    continue
                dispatch_local(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("config redis subscriber crashed: %r; retrying in 3s", e)
            await asyncio.sleep(3)
        finally:
            try:
                if pubsub is not None:
                    await pubsub.close()
            except Exception:  # noqa: BLE001
                pass


def start_background_subscriber() -> None:
    """在 ``@app.on_event('startup')`` 时调一次。"""
    global _REDIS_TASK
    _REDIS_STOP.clear()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("start_background_subscriber: 没有 running event loop，跳过")
        return
    if _REDIS_TASK is not None and not _REDIS_TASK.done():
        return
    _REDIS_TASK = loop.create_task(_redis_loop(), name="config-center-subscriber")


def stop_background_subscriber() -> None:
    _REDIS_STOP.set()
    if _REDIS_TASK is not None and not _REDIS_TASK.done():
        _REDIS_TASK.cancel()
