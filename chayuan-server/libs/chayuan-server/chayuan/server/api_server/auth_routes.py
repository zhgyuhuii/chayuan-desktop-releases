"""/auth/* 路由。

- POST /auth/register：开放注册（受 `AUTH_ALLOW_REGISTRATION` 控制）；
- POST /auth/login：签发 access + refresh token；
- POST /auth/refresh：拿 refresh token 换一对新 token；
- GET  /auth/me：拿到当前用户资料；
- POST /auth/logout：客户端直接扔掉 token 即可；这里仅回 204 方便前端调用。
- GET  /auth/users：管理员看用户列表；
- POST /auth/users：管理员新增用户；
- PATCH /auth/users/{id}/password：管理员或本人改密；
- PATCH /auth/users/{id}/active：管理员启/禁用。

所有路由 ** 都显式**写了依赖，而不是通过全局中间件拦；这样调试更直观。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

# Cookie 名固定;前端如果走 cookie 模式直接 credentials='include' 即可,
# JS 侧不需要也不应该读这两个 cookie(HttpOnly)
_COOKIE_ACCESS = "chayuan_access_token"
_COOKIE_REFRESH = "chayuan_refresh_token"


def _cookie_kwargs(token_kind: str) -> Dict[str, Any]:
    """统一的 cookie 设置:HttpOnly + SameSite=Lax;dev 走 http 也允许(secure=False)。
    生产 https 部署应在反代层把 secure 升 True(本进程不感知 scheme)。"""
    from chayuan.settings import Settings
    if token_kind == "access":
        max_age = int(getattr(Settings.basic_settings, "JWT_ACCESS_TTL_MIN", 60)) * 60
    else:
        max_age = int(getattr(Settings.basic_settings, "JWT_REFRESH_TTL_DAY", 14)) * 86400
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": False,  # 反代 HTTPS 时浏览器仍按 https 发;开发 http 不能强 True
        "max_age": max_age,
        "path": "/",
    }


def _set_token_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(_COOKIE_ACCESS, access, **_cookie_kwargs("access"))
    response.set_cookie(_COOKIE_REFRESH, refresh, **_cookie_kwargs("refresh"))


def _clear_token_cookies(response: Response) -> None:
    response.delete_cookie(_COOKIE_ACCESS, path="/")
    response.delete_cookie(_COOKIE_REFRESH, path="/")
from pydantic import BaseModel, Field

from chayuan.server.auth import service as svc
from chayuan.server.auth.deps import get_current_user, require_role
from chayuan.server.auth.tokens import TokenError, decode_token
from chayuan.server.db.models.user_model import UserPublicSchema

logger = logging.getLogger("chayuan.auth.routes")

auth_router = APIRouter(prefix="/auth", tags=["Auth 多用户鉴权"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class _LoginBody(BaseModel):
    username: str
    password: str


class _RegisterBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=256)
    email: Optional[str] = None


class _RefreshBody(BaseModel):
    refresh_token: str


class _TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublicSchema


class _CreateUserBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=256)
    role: str = "user"
    email: Optional[str] = None


class _ChangePasswordBody(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=256)
    old_password: Optional[str] = None


# ---------------------------------------------------------------------------
# 公开端点
# ---------------------------------------------------------------------------

@auth_router.get(
    "/config",
    summary="公开:鉴权策略",
)
def auth_config() -> Dict[str, bool]:
    """前端在启动时拉一次,根据 `auth_required` 决定:
    - true  → 主页就弹登录框,不允许游客 / 不允许关闭弹框
    - false → 走游客态;登录是可选

    `auth_allow_registration` 控制注册入口是否显示。
    无需鉴权访问;字段全是布尔,不暴露任何敏感信息。
    """
    from chayuan.settings import Settings
    return {
        "auth_required": bool(getattr(Settings.basic_settings, "AUTH_REQUIRED", False)),
        "auth_allow_registration": bool(
            getattr(Settings.basic_settings, "AUTH_ALLOW_REGISTRATION", True)
        ),
    }


@auth_router.post(
    "/register",
    response_model=UserPublicSchema,
    status_code=status.HTTP_201_CREATED,
    summary="注册",
)
def register(body: _RegisterBody):
    from chayuan.settings import Settings
    if not bool(getattr(Settings.basic_settings, "AUTH_ALLOW_REGISTRATION", True)):
        raise HTTPException(status_code=403, detail="self-registration disabled")

    try:
        u = svc.create_user(body.username, body.password, email=body.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UserPublicSchema.model_validate(u)


@auth_router.post("/login", response_model=_TokenPair, summary="登录")
def login(body: _LoginBody, response: Response):
    try:
        u = svc.authenticate(body.username, body.password)
    except svc.AuthError as e:
        logger.warning(
            "login failed: username=%r reason=%s",
            (body.username or "")[:64], str(e) or "unknown",
        )
        raise HTTPException(
            status_code=401, detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access, refresh = svc.issue_token_pair(u)
    # 同时下发 HttpOnly cookie + body token:
    # - body 兼容现有 Bearer 模式前端
    # - cookie 让走 credentials='include' 模式的前端不必在 JS 里持有 token(防 XSS)
    _set_token_cookies(response, access, refresh)
    return _TokenPair(
        access_token=access,
        refresh_token=refresh,
        user=UserPublicSchema.model_validate(u),
    )


@auth_router.post("/refresh", response_model=_TokenPair, summary="刷新 token")
def refresh(request: Request, response: Response, body: Optional[_RefreshBody] = None):
    # 优先取 body.refresh_token(Bearer 模式),fallback 取 cookie(cookie 模式)
    refresh_tok = (body.refresh_token if body else "") or request.cookies.get(_COOKIE_REFRESH) or ""
    refresh_tok = refresh_tok.strip()
    if not refresh_tok:
        raise HTTPException(status_code=401, detail="refresh token missing")
    try:
        decoded = decode_token(refresh_tok, expected_type="refresh")
    except TokenError as e:
        raise HTTPException(status_code=401, detail=f"refresh token invalid: {e}")
    if decoded.user_id is None:
        raise HTTPException(status_code=401, detail="refresh token invalid: no sub")
    u = svc.get_user_by_id(decoded.user_id)
    if u is None or not u.is_active:
        raise HTTPException(status_code=401, detail="user gone or disabled")
    access, new_refresh = svc.issue_token_pair(u)
    _set_token_cookies(response, access, new_refresh)
    return _TokenPair(
        access_token=access,
        refresh_token=new_refresh,
        user=UserPublicSchema.model_validate(u),
    )


@auth_router.get("/me", response_model=UserPublicSchema, summary="当前用户资料")
def me(user: Dict[str, Any] = Depends(get_current_user)):
    full = svc.get_user_by_id(int(user["id"]))
    if full is None:
        raise HTTPException(status_code=404, detail="user not found")
    return UserPublicSchema.model_validate(full)


@auth_router.get("/me/summary", summary="当前用户资料 + 汇总计数")
def me_summary(user: Dict[str, Any] = Depends(get_current_user)):
    """给前端用的轻量接口，一次拿全：资料 / 拥有 KB 数 / 会话数。"""
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    from chayuan.server.db.models.conversation_model import ConversationModel

    full = svc.get_user_by_id(int(user["id"]))
    if full is None:
        raise HTTPException(status_code=404, detail="user not found")

    uid = int(user["id"])
    with session_scope() as s:
        kb_owned = s.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.owner_id == uid
        ).count()
        conv_count = s.query(ConversationModel).filter(
            ConversationModel.user_id == uid
        ).count()

    return {
        "user": UserPublicSchema.model_validate(full).model_dump(mode="json"),
        "stats": {
            "kb_owned": int(kb_owned),
            "conversations": int(conv_count),
        },
    }


@auth_router.post(
    "/logout",
    response_class=Response,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="登出（前端丢 token 即可）",
)
def logout():
    # JWT 无状态，服务端无需作为；若做吊销，需要引入 jti 黑名单（Redis）
    # 注意：新版 FastAPI 在 204 时会强校验「函数不得声明返回体」，
    # 这里用 Response() 显式返回空 body，避免启动时 AssertionError。
    # 同时清掉 cookie:cookie 模式的客户端无法在 JS 删 HttpOnly cookie,必须服务端帮删
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_token_cookies(resp)
    return resp


# ---------------------------------------------------------------------------
# 管理员端点
# ---------------------------------------------------------------------------

@auth_router.get(
    "/users", response_model=List[UserPublicSchema], summary="[admin] 列用户"
)
def admin_list_users(_=Depends(require_role("admin")), limit: int = 100, offset: int = 0):
    users = svc.list_users(limit=limit, offset=offset)
    return [UserPublicSchema.model_validate(u) for u in users]


@auth_router.post(
    "/users", response_model=UserPublicSchema,
    status_code=status.HTTP_201_CREATED, summary="[admin] 新建用户"
)
def admin_create_user(
    body: _CreateUserBody,
    _=Depends(require_role("admin")),
):
    try:
        u = svc.create_user(body.username, body.password, role=body.role, email=body.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UserPublicSchema.model_validate(u)


@auth_router.patch("/users/{user_id}/password", summary="改密（admin 或本人）")
def change_password(
    user_id: int,
    body: _ChangePasswordBody,
    cur: Dict[str, Any] = Depends(get_current_user),
):
    is_admin = cur.get("role") == "admin"
    is_self = int(cur.get("id")) == int(user_id)
    if not (is_admin or is_self):
        raise HTTPException(status_code=403, detail="forbidden")

    if is_self and not is_admin:
        if not body.old_password:
            raise HTTPException(status_code=400, detail="old_password required")
        u = svc.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="user not found")
        from chayuan.server.auth.password import verify_password
        if not verify_password(body.old_password, u.password_hash or ""):
            raise HTTPException(status_code=400, detail="old password mismatch")

    try:
        svc.set_password(user_id, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "msg": "ok"}


@auth_router.patch("/users/{user_id}/active", summary="[admin] 启/禁用账号")
def set_active(
    user_id: int,
    is_active: bool,
    _=Depends(require_role("admin")),
):
    try:
        svc.set_active(user_id, is_active)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "msg": "ok"}
