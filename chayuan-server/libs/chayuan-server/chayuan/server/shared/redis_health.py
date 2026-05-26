"""Redis 健康 / 不可用统一提示。

目的
----
项目里有多处模块依赖 Redis：限流、语义缓存、异步入库队列、/readyz 探活。
之前每个模块各写自己的 fall-back 代码，用户看到的只是若干条零碎 warning，
并不知道"去哪配 Redis"；我们统一：

- 任何 redis 相关的失败/跳过，一律通过 :func:`warn_redis_unavailable` 记日志，
  自动附上"请到 [配置面板 ⑨ 性能与可扩展性 → Redis 连接] 配置 Redis"的指引；
- 每个 *context*（e.g. ``"semcache"`` / ``"ratelimit"`` / ``"ingest_async"``）
  在 ``COOLDOWN_SECONDS`` 内只打一条，避免日志刷屏；
- :func:`sync_probe` 做一次轻量同步 PING，带 60s 结果缓存，供路由层（如 /readyz
  或者文件上传成功响应）决定要不要把 hint 带给前端；
- :func:`redis_hint` 是给面向用户的 JSON/notify 用的中文指引字符串。

所有工具都是 best-effort：redis 包未装、URL 未配置、连接异常都不会抛出，只会
返回 ``(False, reason)`` 或返回一个带 ``hint`` 的 dict。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("chayuan.shared.redis_health")


#: 面向用户展示的"去哪配 Redis"指引。路由 JSON / 前端 notify 统一用它，
#: 一旦文案/路径变动，这里改一处即可。
REDIS_HINT_ZH = (
    "请在 [配置面板 ⑨ 性能与可扩展性 → Redis 连接] 配置 Redis，"
    "保存后重启服务；未配置 Redis 将退化为单机内存模式。"
)


def redis_hint() -> str:
    """面向 UI / JSON 的中文指引文案。"""
    return REDIS_HINT_ZH


# ---------------------------------------------------------------------------
# 日志限频
# ---------------------------------------------------------------------------

COOLDOWN_SECONDS = 300
"""同一 ``context`` 的 warn_redis_unavailable 最短间隔。"""

_LAST_WARNED: Dict[str, float] = {}
_LOCK = threading.Lock()


def warn_redis_unavailable(context: str, reason: str = "") -> None:
    """带 cooldown 的 warning：首次 / 5 分钟外才真正落日志。

    - ``context``：逻辑分组，e.g. ``"semcache"`` / ``"ratelimit"``；
    - ``reason``：异常摘要，拼到日志尾部方便排查；
    - 无返回值；不会抛异常。
    """
    now = time.time()
    with _LOCK:
        last = _LAST_WARNED.get(context, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return
        _LAST_WARNED[context] = now

    tail = f"（{reason}）" if reason else ""
    logger.warning(
        "[%s] Redis 不可用%s。%s",
        context, tail, REDIS_HINT_ZH,
    )


# ---------------------------------------------------------------------------
# 同步探活（带 TTL 缓存）
# ---------------------------------------------------------------------------

_PROBE_CACHE_TTL = 60.0
_PROBE_CACHE: Dict[str, Tuple[float, bool, str]] = {}


def _redis_url() -> str:
    try:
        from chayuan.settings import Settings
        return (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
    except Exception:
        return ""


def sync_probe(timeout: float = 2.0, *, use_cache: bool = True) -> Tuple[bool, str]:
    """返回 ``(ok, reason)``。

    - ``ok=True`` 表示 PING 通；``reason`` 是诸如 ``"Redis 7.2.4"`` 的说明；
    - ``ok=False`` 时 ``reason`` 是失败原因（URL 未配置 / 包未装 / 异常）；
    - 默认 60s 缓存：同一个 URL 不会被每次请求重复 PING。
    """
    url = _redis_url()
    now = time.time()
    if use_cache:
        cached = _PROBE_CACHE.get(url)
        if cached and now - cached[0] < _PROBE_CACHE_TTL:
            return cached[1], cached[2]

    ok, reason = _probe_once(url, timeout=timeout)
    _PROBE_CACHE[url] = (now, ok, reason)
    return ok, reason


def _probe_once(url: str, *, timeout: float) -> Tuple[bool, str]:
    if not url:
        return False, "REDIS_URL 未配置"
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
    except Exception as e:  # noqa: BLE001
        return False, f"redis 包未安装（自动安装失败）：{e}"

    client = None
    try:
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            decode_responses=True,
        )
        if not client.ping():
            return False, "PING 返回 false"
        version = ""
        try:
            version = str((client.info("server") or {}).get("redis_version") or "")
        except Exception:
            pass
        return True, (f"Redis {version}" if version else "PING OK")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def invalidate_probe_cache() -> None:
    """保存新的 REDIS_URL 或重启连通性后手动清缓存。"""
    _PROBE_CACHE.clear()


# ---------------------------------------------------------------------------
# 供 JSON 响应拼"带 hint"的字段
# ---------------------------------------------------------------------------

def attach_hint(payload: Dict[str, Any], *, context: str, reason: str = "") -> Dict[str, Any]:
    """给响应体加一个 ``redis_hint`` 字段，顺手打一条限频 warning。"""
    payload.setdefault("redis_hint", REDIS_HINT_ZH)
    warn_redis_unavailable(context, reason=reason)
    return payload
