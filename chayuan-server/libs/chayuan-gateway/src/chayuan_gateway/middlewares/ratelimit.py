"""Token-bucket rate limiter (in-memory; can be swapped for redis later)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, qpm: int = 600) -> None:
        super().__init__(app)
        self.cap = qpm
        self.refill_per_sec = qpm / 60.0
        self._buckets: dict[str, tuple[float, float]] = defaultdict(lambda: (qpm, time.time()))
        self._lock = threading.Lock()

    def _consume(self, key: str) -> bool:
        with self._lock:
            tokens, ts = self._buckets[key]
            now = time.time()
            tokens = min(self.cap, tokens + (now - ts) * self.refill_per_sec)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            tokens -= 1
            self._buckets[key] = (tokens, now)
            return True

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get("authorization") or request.client.host if request.client else "anon"
        if not self._consume(key):
            return JSONResponse({"error": "rate-limit exceeded"}, status_code=429)
        return await call_next(request)
