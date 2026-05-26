"""AuthMiddleware：解析 Authorization / Cookie 里的 JWT，写到 request.state。

- `request.state.user_id` 供限流 / metrics / 日志使用；
- `request.state.user` = {id, username, role}；业务侧 FastAPI deps 读它。

**不在这里拒绝请求** —— 是否强制登录由 `get_current_user` 依赖在路由层判断，
这样 /auth/login、/healthz 等不需要 token 的路径不会被拦。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from chayuan.server.auth.tokens import TokenError, decode_token

logger = logging.getLogger("chayuan.auth.middleware")


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = self._extract_token(request)
        if token:
            try:
                decoded = decode_token(token, expected_type="access")
                if decoded.user_id is not None:
                    request.state.user_id = decoded.user_id
                    request.state.user = {
                        "id": decoded.user_id,
                        "username": decoded.username or "",
                        "role": decoded.payload.get("role") or "user",
                    }
            except TokenError as e:
                # 非法 token：不写 state.user；业务层 Depends(get_current_user) 会 401
                logger.debug("token rejected: %r", e)
        return await call_next(request)

    # 只有对"浏览器直接 GET 下载 / 预览"这种场景我们允许 ?token=<jwt>；
    # POST / 业务接口依然只认 Authorization 头，降低 token 被日志 / Referer 泄露的面。
    _QUERY_TOKEN_ALLOWED_PATHS = (
        "/knowledge_base/download_doc",
        "/knowledge_base/preview_doc",
        "/media/",
    )

    # 优先级:Authorization > HttpOnly cookie > 白名单 query 参数
    # cookie 路径让前端可以走 credentials='include' 模式,完全不在 JS 里持有 token
    # (避免 XSS 拿到);要求前端配合开启 cookie 模式 + 后端 CORS 允许 credentials
    @classmethod
    def _extract_token(cls, request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()

        cookie_tok = (request.cookies.get("chayuan_access_token") or "").strip()
        if cookie_tok:
            return cookie_tok

        # 浏览器点击 <a href=".../download_doc?...">：没法塞 header，放行 query 参数
        path = request.url.path or ""
        if any(path.startswith(p) for p in cls._QUERY_TOKEN_ALLOWED_PATHS):
            tok = (request.query_params.get("token") or "").strip()
            if tok:
                return tok
        return None
