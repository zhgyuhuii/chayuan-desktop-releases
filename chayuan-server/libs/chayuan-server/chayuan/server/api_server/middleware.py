"""API Server 的跨切面中间件。

- ``RequestIDMiddleware``：给每个请求贴 ``X-Request-ID``（客户端已传就复用），
  写到 `request.state.request_id` 与响应头，便于日志串联 / nginx 记录。
- ``TokenBucketRateLimiter``：按 IP + 可选 user-id 维度的令牌桶限流。
  Redis 可用时走 Redis（多副本一致），否则本地内存回退（单副本也能用）。
  用 Starlette BaseHTTPMiddleware 而非 slowapi，避免引入新 PyPI 依赖。
- ``DBReadinessMiddleware``：DB 不可达时把业务请求统一拦成 503 + 修复指引，
  避免 SQLAlchemy 堆栈以 500 形式漏到客户端；结果带 TTL 缓存避免每请求打 DB。

开关与阈值来自 ``basic_settings``：
    RATE_LIMIT_ENABLED        - bool
    RATE_LIMIT_PER_MINUTE     - int
    REDIS_URL                 - str
    REQUEST_ID_HEADER         - str (默认 X-Request-ID)
    API_DB_GATE_ENABLED       - bool，默认 True；关闭后 DB 不可达将按旧行为 500
    API_DB_GATE_TTL_SECONDS   - float，默认 5.0；DB 探活结果的缓存窗口
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("chayuan.api.middleware")


# ---------------------------------------------------------------------------
# Request-ID
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    """给每个请求贴一个稳定的 request id，便于日志串联。

    - 若客户端已传 ``X-Request-ID`` 则复用（信任边界内）；
    - 否则生成 UUID4 去掉横杠；
    - 结果同时写到 ``request.state.request_id`` 与响应头同名。
    """

    def __init__(self, app, header: str = "X-Request-ID"):
        super().__init__(app)
        self.header = header

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rid = (request.headers.get(self.header) or "").strip()
        if not rid or len(rid) > 128:
            rid = uuid.uuid4().hex
        request.state.request_id = rid

        # 注入 contextvar：此后所有 logging 输出自动带上 rid
        try:
            from chayuan.server.observability import set_request_id
            set_request_id(rid)
        except Exception:  # noqa: BLE001
            pass

        try:
            response = await call_next(request)
        except Exception:
            logger.exception("[rid=%s] unhandled exception", rid)
            raise
        response.headers[self.header] = rid
        return response


# ---------------------------------------------------------------------------
# 内存回退令牌桶
# ---------------------------------------------------------------------------

class _MemoryWindow:
    """滑动窗口计数（粗粒度，秒级）。单 worker 时可用，多 worker 建议 Redis。"""

    def __init__(self):
        self._buckets: Dict[str, Deque[float]] = {}

    def incr_and_count(self, key: str, window: float) -> int:
        now = time.monotonic()
        dq = self._buckets.get(key)
        if dq is None:
            dq = deque()
            self._buckets[key] = dq
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        dq.append(now)
        return len(dq)


_MEM_WINDOW = _MemoryWindow()


# ---------------------------------------------------------------------------
# Redis 令牌桶（固定窗口 INCR + EXPIRE；足够支撑限流场景）
# ---------------------------------------------------------------------------

async def _redis_incr(key: str, window_seconds: int) -> Optional[int]:
    """Redis 可用时返回当前窗口内计数；否则 None 让调用方回退到内存。

    Redis 连不上时用 shared.redis_health 打一条 5min 一次的限频 warning，
    附上「请到配置面板 ⑨ 性能与可扩展性 → Redis 连接 配置 Redis」的指引。
    """
    from chayuan.server.shared import get_redis, disabled_reason
    from chayuan.server.shared.redis_health import warn_redis_unavailable

    r = get_redis()
    if r is None:
        warn_redis_unavailable("ratelimit", reason=disabled_reason() or "get_redis() returned None")
        return None
    try:
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, window_seconds)
        counts = await pipe.execute()
        return int(counts[0])
    except Exception as e:  # noqa: BLE001
        warn_redis_unavailable("ratelimit", reason=f"{type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# 限流主体
# ---------------------------------------------------------------------------

# 跳过限流的路径前缀：健康检查、openapi、静态资源等不计数
_SKIP_PREFIXES: Tuple[str, ...] = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/img",
    "/media",
)


class TokenBucketRateLimiter(BaseHTTPMiddleware):
    """按「IP + 可选用户」维度的滑动窗口限流。

    - 只有当 ``RATE_LIMIT_ENABLED=true`` 时才真正生效；
    - 拒绝时返回 429 + JSON，响应头带 ``Retry-After``；
    - 响应头带 ``X-RateLimit-Limit / X-RateLimit-Remaining``，便于客户端自适应。
    """

    WINDOW_SECONDS = 60

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 客户端在请求处理途中断开(慢请求 / 前端轮询超时很常见 —— 如首次
        # 加载 faiss 耗时几十秒)时,Starlette BaseHTTPMiddleware 的 call_next
        # 会抛 RuntimeError("No response returned.")。这不是真错误,服务器
        # 仍正常 —— 安静收掉,别刷一大坨吓人的 ASGI traceback。
        try:
            return await self._dispatch(request, call_next)
        except RuntimeError as e:
            if "No response returned" in str(e):
                from starlette.responses import Response as _Resp
                return _Resp(status_code=499)  # client closed request
            raise

    async def _dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from chayuan.settings import Settings

        bs = Settings.basic_settings
        enabled = bool(getattr(bs, "RATE_LIMIT_ENABLED", False))
        if not enabled:
            return await call_next(request)

        path = request.url.path
        if path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        limit = int(getattr(bs, "RATE_LIMIT_PER_MINUTE", 120) or 120)
        if limit <= 0:
            return await call_next(request)

        key = self._identity_key(request)
        count = await _redis_incr(key, self.WINDOW_SECONDS)
        if count is None:
            # 内存回退：仅在当前 worker 内一致
            count = _MEM_WINDOW.incr_and_count(key, float(self.WINDOW_SECONDS))

        remaining = max(0, limit - count)
        if count > limit:
            rid = getattr(request.state, "request_id", "")
            logger.warning(
                "[rid=%s] rate-limited: key=%s count=%d limit=%d",
                rid, key, count, limit,
            )
            resp = JSONResponse(
                status_code=429,
                content={
                    "code": 429,
                    "msg": "请求过于频繁，请稍后再试。",
                    "request_id": rid,
                },
            )
            resp.headers["Retry-After"] = str(self.WINDOW_SECONDS)
            resp.headers["X-RateLimit-Limit"] = str(limit)
            resp.headers["X-RateLimit-Remaining"] = "0"
            return resp

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    # --- utils ---

    @staticmethod
    def _identity_key(request: Request) -> str:
        """优先使用认证后的用户；否则回退到客户端 IP。

        这里对 ``X-Forwarded-For`` 做 best-effort 解析；前提是你把 API 放在
        nginx/traefik 后面（见 docker/prod/nginx.conf 模板）。
        """
        user = getattr(request.state, "user_id", None) or request.headers.get(
            "X-User-ID"
        )
        if user:
            return f"rl:u:{user}"
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            ip = xff.split(",", 1)[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        return f"rl:ip:{ip}"


# ---------------------------------------------------------------------------
# DB readiness gate
# ---------------------------------------------------------------------------

# DB 探活结果缓存（模块级）。
# - 缓存 ok=True 的结果，避免每请求都 SELECT 1；
# - 也缓存 ok=False，避免 DB 宕机时每次请求都重连触发 TCP 超时堆积，
#   造成请求体验更差 + 日志刷屏。
_DB_PROBE_CACHE: Dict[str, Any] = {"ok": None, "detail": "", "at": 0.0}
_DB_PROBE_LOCK = asyncio.Lock()


async def _probe_db_ready(ttl: float) -> Tuple[bool, str]:
    """带 TTL 缓存的 DB 探活，返回 (ok, detail)。

    - 同一时刻只允许一个协程真正去 connect（锁），防止并发打 DB；
    - 真正探活操作是同步的 engine.connect()，用 to_thread 卸载，
      避免阻塞事件循环（连接超时时会卡 N 秒）。
    """
    now = time.monotonic()
    cached = _DB_PROBE_CACHE
    if cached["ok"] is not None and (now - cached["at"]) < ttl:
        return bool(cached["ok"]), str(cached["detail"])

    async with _DB_PROBE_LOCK:
        now = time.monotonic()
        if _DB_PROBE_CACHE["ok"] is not None and (now - _DB_PROBE_CACHE["at"]) < ttl:
            return bool(_DB_PROBE_CACHE["ok"]), str(_DB_PROBE_CACHE["detail"])

        def _sync_probe() -> Tuple[bool, str]:
            try:
                from sqlalchemy import text

                from chayuan.server.db.base import engine

                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return True, "SELECT 1 OK"
            except Exception as e:  # noqa: BLE001
                return False, f"{type(e).__name__}: {e}"

        ok, detail = await asyncio.to_thread(_sync_probe)
        _DB_PROBE_CACHE["ok"] = ok
        _DB_PROBE_CACHE["detail"] = detail
        _DB_PROBE_CACHE["at"] = time.monotonic()

        if not ok:
            logger.warning(
                "DB readiness probe failed (将在 %.1fs 内对业务请求统一 503)：%s",
                ttl, detail,
            )
        return ok, detail


# DB 未就绪时**允许**通过的路径前缀：
# - 健康检查 / openapi / 静态资源：不依赖 DB，且用于排障；
# - 鉴权登出（只清 cookie 不查库）；
# 注：/auth/login 不在白名单——登录本身就要 DB；此时允许通过反而会让前端收到
# 更诡异的 500 堆栈，不如统一 503 提示「先修 DB」。
_DB_GATE_SKIP_PREFIXES: Tuple[str, ...] = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/img",
    "/media",
    "/auth/logout",
)


class DBReadinessMiddleware(BaseHTTPMiddleware):
    """DB 不可达时统一拦成 503，附带「去哪修」的指引。

    行为：
    - 默认启用（``basic_settings.API_DB_GATE_ENABLED`` 缺省视为 True）；
    - 白名单路径直通（见 ``_DB_GATE_SKIP_PREFIXES``）；
    - 其它路径在真正进入业务处理之前，先做一次带 TTL 缓存的 `SELECT 1` 探活；
    - DB 不 OK 时返回 503 + JSON body，附 ``X-DB-Ready: false`` 响应头，
      body 里给出配置面板修改 DB 连接串的路径。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from chayuan.settings import Settings

        bs = Settings.basic_settings
        enabled = bool(getattr(bs, "API_DB_GATE_ENABLED", True))
        if not enabled:
            return await call_next(request)

        path = request.url.path
        if path.startswith(_DB_GATE_SKIP_PREFIXES):
            return await call_next(request)

        ttl = float(getattr(bs, "API_DB_GATE_TTL_SECONDS", 5.0) or 5.0)
        ok, detail = await _probe_db_ready(ttl)
        if ok:
            response = await call_next(request)
            response.headers["X-DB-Ready"] = "true"
            return response

        rid = getattr(request.state, "request_id", "")
        short_detail = detail.split("\n", 1)[0][:200] if detail else ""
        body = {
            "code": 503,
            "msg": "数据库暂不可用，请稍后重试；或先打开配置面板修正 DB 连接串后重启服务。",
            "hint": (
                "1) 确认目标数据库已启动、端口/账号可连；"
                "2) 在配置面板（chayuan start -c，默认 :8502）修改 "
                "basic_settings.SQLALCHEMY_DATABASE_URI；"
                "3) 修改后点「立即重启」或执行 chayuan start -a 重新拉起。"
            ),
            "detail": short_detail,
            "request_id": rid,
        }
        resp = JSONResponse(status_code=503, content=body)
        resp.headers["X-DB-Ready"] = "false"
        resp.headers["Retry-After"] = str(max(1, int(ttl)))
        return resp
