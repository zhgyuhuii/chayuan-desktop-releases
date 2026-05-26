"""共享状态：Redis 客户端工厂 + 优雅降级。

设计原则：
- Redis 是 P1 可选依赖；`redis` / `redis.asyncio` 没装也应保证 API 能起来；
- `basic_settings.REDIS_URL` 为空或 redis 包缺失时，返回 None，由调用方走内存回退路径；
- 复用同一个 `AsyncRedis` 连接池，避免每个请求新建。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("chayuan.shared.redis")

_CLIENT: Optional[Any] = None
_PROBED: bool = False
_DISABLED_REASON: str = ""


def _detect_url() -> str:
    from chayuan.settings import Settings
    return (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()


def get_redis() -> Optional[Any]:
    """返回一个 ``redis.asyncio.Redis`` 实例；未配置或包缺失时返回 ``None``。

    - 进程内单例；失败原因会记一次日志，后续调用直接短路。
    - 不主动 `ping`；调用方可在 `/readyz` 中显式探活。
    """
    global _CLIENT, _PROBED, _DISABLED_REASON
    if _PROBED:
        return _CLIENT

    _PROBED = True
    url = _detect_url()
    if not url:
        _DISABLED_REASON = "REDIS_URL 未配置"
        return None

    try:
        try:
            from chayuan.server.shared.deps import ensure_pkg
            ensure_pkg("redis", "redis>=5.0,<6.0")
        except Exception:  # noqa: BLE001 —— auto-install 失败不阻塞 import
            pass
        from redis import asyncio as redis_asyncio  # type: ignore
    except ImportError as e:
        _DISABLED_REASON = f"redis 包未安装：{e!r}；请 `pip install 'redis>=5,<6'`"
        logger.warning("Redis disabled: %s", _DISABLED_REASON)
        return None

    try:
        _CLIENT = redis_asyncio.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        logger.info("Redis client ready: %s", _sanitize(url))
    except Exception as e:  # noqa: BLE001
        _DISABLED_REASON = f"Redis 连接失败：{e!r}"
        logger.warning("Redis init failed: %s", _DISABLED_REASON)
        _CLIENT = None

    return _CLIENT


def disabled_reason() -> str:
    """最近一次 get_redis() 失败 / 跳过的原因（用于 /readyz 与体检页）。"""
    if not _PROBED:
        get_redis()
    return _DISABLED_REASON


def _sanitize(url: str) -> str:
    """隐藏 Redis URL 里的密码部分。"""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    auth, host = rest.split("@", 1)
    if ":" in auth:
        user = auth.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return f"{scheme}://***@{host}"
