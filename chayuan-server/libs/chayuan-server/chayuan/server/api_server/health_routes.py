"""生产环境健康检查端点。

- ``/healthz``：进程存活即 200（给 k8s liveness / nginx active check 用）；
- ``/readyz``：深入探活：DB 可查询、Redis（如配置）可 ping。任一不过返回 503。
- ``/metrics``：若安装了 ``prometheus_client`` 则暴露标准 /metrics，否则 501。

所有端点都挂在根路径，不走鉴权与限流（见 middleware._SKIP_PREFIXES）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Response
from sqlalchemy import text

logger = logging.getLogger("chayuan.api.health")

health_router = APIRouter()


@health_router.get("/healthz", include_in_schema=False)
async def healthz() -> Dict[str, Any]:
    """最轻量存活探针：只要能响应就算活着。"""
    return {"status": "ok", "ts": int(time.time())}


@health_router.get("/readyz", include_in_schema=False)
async def readyz(response: Response) -> Dict[str, Any]:
    """就绪探针：DB 可查、Redis（可选）可 ping。任一失败返回 503。"""
    out: Dict[str, Any] = {"status": "ok", "checks": {}}

    # ---- DB ----
    db_ok, db_detail = _check_db()
    out["checks"]["db"] = {"ok": db_ok, "detail": db_detail}

    # ---- Redis（可选） ----
    redis_ok, redis_detail = await _check_redis()
    out["checks"]["redis"] = {"ok": redis_ok, "detail": redis_detail}

    # Redis 是可选项：未配置时不计入 not-ready
    hard_checks = [db_ok]
    if _redis_configured():
        hard_checks.append(redis_ok)

    if not all(hard_checks):
        out["status"] = "not-ready"
        response.status_code = 503
        # 给运维 / 前端一条清晰的「去哪修」指引
        if _redis_configured() and not redis_ok:
            from chayuan.server.shared.redis_health import redis_hint, warn_redis_unavailable
            out["redis_hint"] = redis_hint()
            warn_redis_unavailable("readyz", reason=redis_detail)
    return out


def _check_db() -> tuple[bool, str]:
    try:
        from chayuan.server.db.base import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "SELECT 1 OK"
    except Exception as e:  # noqa: BLE001
        logger.warning("readyz db check failed: %r", e)
        return False, f"{type(e).__name__}: {e}"


def _redis_configured() -> bool:
    from chayuan.settings import Settings
    return bool((getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip())


async def _check_redis() -> tuple[bool, str]:
    from chayuan.server.shared import disabled_reason, get_redis

    r = get_redis()
    if r is None:
        return False, disabled_reason() or "redis 不可用"
    try:
        pong = await r.ping()
        return bool(pong), "PING OK" if pong else "PING returned falsy"
    except Exception as e:  # noqa: BLE001
        logger.warning("readyz redis check failed: %r", e)
        return False, f"{type(e).__name__}: {e}"


@health_router.get("/metrics", include_in_schema=False)
async def metrics(response: Response):
    """Prometheus exposition。

    由 ``observability.metrics`` 统一埋点；本路由只负责把结果渲染出来。
    - `prometheus_client` 未安装：501；
    - METRICS_ENABLED=false：404，表示用户主动关闭；
    """
    from chayuan.server.observability.metrics import (
        is_metrics_enabled,
        metrics_content_type,
        render_metrics,
    )

    if not is_metrics_enabled():
        # 区分"没装包"与"主动关"：分别返回 501 / 404，便于排障
        try:
            import prometheus_client  # type: ignore  # noqa: F401
        except ImportError:
            response.status_code = 501
            return {
                "code": 501,
                "msg": "prometheus_client 未安装；请 `pip install prometheus-client` 后启用。",
            }
        response.status_code = 404
        return {"code": 404, "msg": "METRICS_ENABLED=false，未开启指标。"}

    data = render_metrics()
    if data is None:
        response.status_code = 500
        return {"code": 500, "msg": "metrics unavailable"}
    return Response(content=data, media_type=metrics_content_type())
