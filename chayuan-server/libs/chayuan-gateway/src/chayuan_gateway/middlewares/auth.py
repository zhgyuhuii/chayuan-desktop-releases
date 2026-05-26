"""API-key middleware (OpenAI-compatible).

Skip-list: paths under /healthz, /v1/models stay open for the UI.
Allowed keys come from config.gateway.api_keys.
"""
from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from chayuan_core import load_config

OPEN_PATHS = ("/healthz", "/v1/models", "/v1/system", "/docs", "/openapi.json", "/redoc")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in OPEN_PATHS):
            return await call_next(request)
        cfg = load_config()
        keys = set(cfg.gateway.api_keys)
        if not keys:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token not in keys:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
