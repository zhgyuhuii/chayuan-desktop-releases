"""多租户上下文（N-9 基础）。

用 ``contextvars.ContextVar`` 在一个 request 的生命周期里全链路传递
``tenant_id`` / ``user_id`` / ``user_role``，避免到处手工参数传递。

工作流：
1. FastAPI 中间件在 request 到达时读 JWT / Header，调 ``set_tenant_ctx(...)``
2. 下游任意深度的代码（ChatGraph / Connector / Repository）都可以 ``get_tenant_ctx()``
3. SQL Connector 在 _exec_sql 里按 ctx 自动注入 WHERE tenant_id=...
4. Postgres RLS 策略依赖 ``SET LOCAL app.tenant_id = '...'``

**Thread-safe + async-safe**：contextvars 是 Python stdlib 的正道。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TenantCtx:
    tenant_id: Optional[str] = None     # 多租户租户 id（字符串）
    user_id: Optional[int] = None
    user_role: str = ""
    request_id: str = ""
    # 可扩展：region / project / labels 等
    extra: dict = field(default_factory=dict)


_CTX: contextvars.ContextVar[TenantCtx] = contextvars.ContextVar(
    "chayuan.tenant_ctx", default=TenantCtx(),
)


def set_tenant_ctx(ctx: TenantCtx) -> contextvars.Token:
    """设置 ctx；返回 Token 可用于 reset。"""
    return _CTX.set(ctx)


def reset_tenant_ctx(token: contextvars.Token) -> None:
    try:
        _CTX.reset(token)
    except Exception:  # noqa: BLE001
        pass


def get_tenant_ctx() -> TenantCtx:
    return _CTX.get()


def current_tenant_id() -> Optional[str]:
    return get_tenant_ctx().tenant_id


def current_user_role() -> str:
    return get_tenant_ctx().user_role


# ---------------------------------------------------------------------------
# FastAPI 中间件：从 JWT / Header 抽取 tenant_id 并 set_tenant_ctx
# ---------------------------------------------------------------------------

class TenantContextMiddleware:
    """Starlette middleware：把 request.state.user 转成 TenantCtx。

    tenant_id 来源优先级：
      1. request.headers["X-Tenant-Id"]（对接网关时用）
      2. user.tenant_id（若 UserModel 将来扩了这个字段）
      3. 否则用 role 充当（role 级策略也能用）
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        # 延迟 import 避免循环
        from starlette.requests import Request
        request = Request(scope, receive=receive)

        # TENANT_HEADER 可配，默认 "X-Tenant-Id"
        header_name = "X-Tenant-Id"
        try:
            from chayuan.settings import Settings
            header_name = str(
                getattr(Settings.basic_settings, "TENANT_HEADER", "") or "X-Tenant-Id"
            )
        except Exception:  # noqa: BLE001
            pass
        tenant_id = request.headers.get(header_name) or ""
        user = getattr(request.state, "user", None)
        user_id = None
        user_role = ""
        if isinstance(user, dict):
            user_id = user.get("id")
            user_role = str(user.get("role") or "")
            if not tenant_id:
                tenant_id = str(user.get("tenant_id") or user.get("role") or "")
        request_id = str(getattr(request.state, "request_id", "") or "")
        ctx = TenantCtx(
            tenant_id=tenant_id or None,
            user_id=(int(user_id) if user_id is not None else None),
            user_role=user_role,
            request_id=request_id,
        )
        token = set_tenant_ctx(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant_ctx(token)
