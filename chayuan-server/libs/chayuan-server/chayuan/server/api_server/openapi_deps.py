"""开放平台路由共享依赖。

- ``require_scopes(*scope)``：检查 ``request.state.app`` 的 scope 是否覆盖给定列表，
  未覆盖返回 403；同时通过 ``fastapi.security.APIKeyHeader`` 声明三头签名协议，
  让 Swagger (``/docs``) 自动展示「X-App-Id / X-Timestamp / X-Sign」字段与描述。
- 签名真正校验仍由 ``openapi_routes.AppAuthMiddleware`` 在 ASGI 层完成（那一层能
  拿到 raw body 参与 HMAC），本模块只承担：① 文档展示 ② scope 检查 ③ 把
  ``AppSpec`` 注入 endpoint。
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from chayuan.server.config_panel.apps_store import AppSpec
from chayuan.server.shared.scopes import covers, missing, sanitize_scope_list


# --- Swagger 文档用的三头声明 ---
# auto_error=False：真正的缺头报错由 AppAuthMiddleware 做（返回统一 JSON）；
# 这里只负责让 Swagger 展示「这个接口要求这三个头」。
_app_id_hdr = APIKeyHeader(
    name="X-App-Id", scheme_name="X-App-Id",
    description="App 唯一标识（32 位 hex）。"
                "在「配置面板 → App 管理 → 创建应用」时分配。",
    auto_error=False,
)
_ts_hdr = APIKeyHeader(
    name="X-Timestamp", scheme_name="X-Timestamp",
    description="签名时间戳（秒级 Unix），与服务端时钟漂移需 ≤ 5 分钟。",
    auto_error=False,
)
_sign_hdr = APIKeyHeader(
    name="X-Sign", scheme_name="X-Sign",
    description="HMAC-SHA256(app_secret, timestamp + '\\n' + raw_body) 的 hex。",
    auto_error=False,
)


def require_scopes(*required: str) -> Callable[..., AppSpec]:
    """返回一个 FastAPI 依赖，校验当前 App scope 覆盖 ``required`` 全部项。

    用法::

        @router.get("/foo", response_model=FooResp)
        async def foo(app: AppSpec = Depends(require_scopes("chat:read"))):
            ...
    """
    required_list = list(required)

    async def _dep(
        request: Request,
        _xai: Optional[str] = Security(_app_id_hdr),
        _xts: Optional[str] = Security(_ts_hdr),
        _xsi: Optional[str] = Security(_sign_hdr),
    ) -> AppSpec:
        app: AppSpec | None = getattr(request.state, "app", None)
        if app is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        if required_list:
            have = sanitize_scope_list(app.scopes)
            if not all(covers(have, r) for r in required_list):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": 403,
                        "msg": "insufficient scopes",
                        "required": required_list,
                        "missing": missing(have, required_list),
                    },
                )
        return app

    # 把参数说明写清楚便于 Swagger 展示
    _dep.__doc__ = (
        "要求当前 App 拥有以下 scope（全都要）："
        f"{', '.join(required_list) if required_list else '(none)'}。"
    )
    return _dep


# 常用：只要求签过名但不限 scope
def require_app() -> Callable[..., AppSpec]:
    return require_scopes()
