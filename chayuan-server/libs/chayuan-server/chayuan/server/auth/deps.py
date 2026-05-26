"""FastAPI dependencies for auth.

- ``get_current_user_optional(request)``：无 token / token 非法都返回 None；
- ``get_current_user(request)``：没拿到有效用户就 401；
- ``require_role("admin")``：常用的 role gate；
- ``require_auth_enabled``：当 AUTH_REQUIRED=false 时依赖 optional；否则等价 required。

`request.state.user_id` / `request.state.user` 会被提前中间件写好，deps 只读。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger("chayuan.auth.deps")


def _user_from_state(request: Request) -> Optional[Dict[str, Any]]:
    u = getattr(request.state, "user", None)
    if u and isinstance(u, dict):
        return u
    return None


def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """可选登录。返回 dict，字段：id / username / role。"""
    return _user_from_state(request)


# 70 题:游客身份 — 单机应用 / AUTH_REQUIRED=false 场景下,所有路由透明放行
# user_id=-1 与正常 user.id (>0 自增) 不冲突;username/role 用约定值供权限判断
GUEST_USER: Dict[str, Any] = {
    "id": -1,
    "username": "guest",
    "role": "user",
    "is_guest": True,
}


def get_current_user(request: Request) -> Dict[str, Any]:
    """获取当前用户。

    - **已登录**:返回真实 user dict(``id`` / ``username`` / ``role``)
    - **未登录 + AUTH_REQUIRED=true**:抛 401(原行为)
    - **未登录 + AUTH_REQUIRED=false**(70 题游客模式):返回 :data:`GUEST_USER`,
      所有依赖 get_current_user 的路由透明工作 — 用户可以问答 / 选模型 /
      列应用,数据按 user_id=-1 隔离

    单机应用部署:用户启动 chayuan-server 时 AUTH_REQUIRED=false(默认),
    chayuan-client 不要求登录就能用全部功能。
    """
    u = _user_from_state(request)
    if u is not None:
        return u
    # 未登录 — 看 AUTH_REQUIRED 决定 401 还是放游客
    try:
        from chayuan.settings import Settings
        required = bool(getattr(Settings.basic_settings, "AUTH_REQUIRED", False))
    except Exception:  # noqa: BLE001
        required = False
    if required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # AUTH_REQUIRED=false → 游客身份,后续路由按 user_id=-1 处理
    return dict(GUEST_USER)


def require_role(*roles: str):
    roles_set = set(roles or ())

    def _dep(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if user.get("role") not in roles_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient role",
            )
        return user

    return _dep


def require_auth_enabled():
    """根据 basic_settings.AUTH_REQUIRED 动态决定是否强制登录。

    - AUTH_REQUIRED=true  → 等价 get_current_user
    - AUTH_REQUIRED=false → 等价 get_current_user_optional（返回 None 也放行）
    这样同一套代码既能给单机调试用又能上多租户。
    """
    def _dep(request: Request) -> Optional[Dict[str, Any]]:
        try:
            from chayuan.settings import Settings
            required = bool(getattr(Settings.basic_settings, "AUTH_REQUIRED", False))
        except Exception:
            required = False
        if required:
            return get_current_user(request)
        return get_current_user_optional(request)

    return _dep
