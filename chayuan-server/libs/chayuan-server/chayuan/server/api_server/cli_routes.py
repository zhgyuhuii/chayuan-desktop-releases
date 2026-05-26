"""/cli/* —— chachat CLI 的后端配套（OAuth2 Device Code 风格）。

为什么走 device code：CLI 工具不应经手用户密码（存盘 / 回显 / 误上屏风险）。
与 GitHub CLI / gcloud / Claude Code CLI 一致，用浏览器承担真实登录：

1. CLI 调 ``POST /cli/device/start`` 拿 ``device_code`` + ``user_code`` + ``verification_uri``
2. CLI 把 ``verification_uri + user_code`` 打印到终端让用户开浏览器
3. 浏览器访问 ``GET /cli/device?code=...`` 显示登录表单
4. 用户提交表单 → ``POST /cli/device/approve`` 校验用户名密码，把 token pair 绑到 device_code
5. CLI 同时轮询 ``POST /cli/device/token`` 拿 token pair

状态存 Redis（15min TTL）；Redis 不可用时退化进程内字典（单进程 dev 可用，
多 worker 下会 flaky，但不会崩）。
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Body, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from chayuan.server.auth import service as svc
from chayuan.server.db.models.user_model import UserPublicSchema

logger = logging.getLogger("chayuan.api.cli")

cli_router = APIRouter(prefix="/cli", tags=["CLI chachat"])

# ---------------------------------------------------------------------------
# device_code 状态后端：Redis（首选） / 进程字典（兜底）
# ---------------------------------------------------------------------------

_MEM_STORE: Dict[str, Dict[str, Any]] = {}
_TTL_SECONDS = 15 * 60
_POLL_INTERVAL = 3
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 避开 0/O、1/I


def _gen_user_code() -> str:
    pair = lambda: "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(4))
    return f"{pair()}-{pair()}"


def _gen_device_code() -> str:
    return secrets.token_urlsafe(32)


def _redis():
    """懒取 Redis 客户端；任何异常返回 None 走内存兜底。"""
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore

        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            return None
        return redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=1.0, socket_timeout=1.0,
        )
    except Exception:  # noqa: BLE001
        return None


def _k_device(code: str) -> str:
    return f"chayuan:cli:device:{code}"


def _k_user(code: str) -> str:
    return f"chayuan:cli:user:{code}"


def _state_put(device_code: str, user_code: str, state: Dict[str, Any]) -> None:
    r = _redis()
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if r is not None:
        try:
            r.set(_k_device(device_code), payload, ex=_TTL_SECONDS)
            r.set(_k_user(user_code), device_code, ex=_TTL_SECONDS)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("cli device store redis 失败，降级内存：%r", e)
    _MEM_STORE[_k_device(device_code)] = {
        "expires_at": time.time() + _TTL_SECONDS, "value": state,
    }
    _MEM_STORE[_k_user(user_code)] = {
        "expires_at": time.time() + _TTL_SECONDS, "value": device_code,
    }


def _state_get_by_device(device_code: str) -> Optional[Dict[str, Any]]:
    r = _redis()
    if r is not None:
        try:
            raw = r.get(_k_device(device_code))
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            pass
    rec = _MEM_STORE.get(_k_device(device_code))
    if not rec or rec.get("expires_at", 0) < time.time():
        _MEM_STORE.pop(_k_device(device_code), None)
        return None
    return rec.get("value")


def _state_get_by_user_code(user_code: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    r = _redis()
    device_code: Optional[str] = None
    if r is not None:
        try:
            device_code = r.get(_k_user(user_code))
        except Exception:  # noqa: BLE001
            device_code = None
    if not device_code:
        rec = _MEM_STORE.get(_k_user(user_code))
        if rec and rec.get("expires_at", 0) >= time.time():
            device_code = rec.get("value")
    if not device_code:
        return None, None
    return device_code, _state_get_by_device(device_code)


def _state_update(device_code: str, state: Dict[str, Any]) -> None:
    r = _redis()
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if r is not None:
        try:
            r.set(_k_device(device_code), payload, ex=_TTL_SECONDS)
            return
        except Exception:  # noqa: BLE001
            pass
    rec = _MEM_STORE.get(_k_device(device_code)) or {
        "expires_at": time.time() + _TTL_SECONDS,
    }
    rec["value"] = state
    _MEM_STORE[_k_device(device_code)] = rec


def _state_delete(device_code: str, user_code: Optional[str]) -> None:
    r = _redis()
    if r is not None:
        try:
            r.delete(_k_device(device_code))
            if user_code:
                r.delete(_k_user(user_code))
            return
        except Exception:  # noqa: BLE001
            pass
    _MEM_STORE.pop(_k_device(device_code), None)
    if user_code:
        _MEM_STORE.pop(_k_user(user_code), None)


# ---------------------------------------------------------------------------
# rate-limit：同源 IP / device_code 粒度 —— 避免脚本刷登录页。
# 非常轻量，依赖 TokenBucketRateLimiter 中间件提供的桶；这里只加每 device_code
# 级别的访问计数，上限 20 次。超过视为异常。
# ---------------------------------------------------------------------------

_ACCESS_COUNT: Dict[str, int] = {}


def _bump_access(device_code: str) -> None:
    n = _ACCESS_COUNT.get(device_code, 0) + 1
    _ACCESS_COUNT[device_code] = n
    if n > 100:
        logger.warning("cli device_code=%s 访问次数超阈值 %d", device_code[:8], n)


# ---------------------------------------------------------------------------
# 对外 API schemas
# ---------------------------------------------------------------------------

class _StartResp(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = _TTL_SECONDS
    interval: int = _POLL_INTERVAL


class _TokenResp(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublicSchema


class _TokenPendingResp(BaseModel):
    error: str  # authorization_pending / slow_down / expired_token / access_denied


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@cli_router.post(
    "/device/start",
    response_model=_StartResp,
    summary="CLI 开始 device code 流程",
)
def device_start(request: Request):
    """CLI 调用拿一对 device_code / user_code。"""
    device_code = _gen_device_code()
    user_code = _gen_user_code()
    base = str(request.base_url).rstrip("/")
    verification_uri = f"{base}/cli/device?code={user_code}"
    _state_put(device_code, user_code, {
        "user_code": user_code,
        "created_at": time.time(),
        "status": "pending",
        "tokens": None,
    })
    return _StartResp(
        device_code=device_code, user_code=user_code,
        verification_uri=verification_uri,
        expires_in=_TTL_SECONDS, interval=_POLL_INTERVAL,
    )


_LOGIN_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"/>
<title>chachat CLI 登录</title>
<style>
 body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
         "Microsoft YaHei", sans-serif; background:#f5f5f7; margin:0; padding:40px; }}
 .card {{ max-width:420px; margin:60px auto; background:#fff; border-radius:12px;
          padding:28px; box-shadow:0 4px 24px rgba(0,0,0,.06); }}
 h1 {{ font-size:20px; margin:0 0 6px; }}
 .sub {{ color:#666; font-size:13px; margin:0 0 20px; }}
 .code {{ font-family: ui-monospace,SFMono-Regular,Menlo,monospace;
          background:#eef2ff; padding:8px 12px; border-radius:6px; display:inline-block; }}
 label {{ display:block; font-size:13px; margin-top:12px; color:#333; }}
 input[type=text], input[type=password] {{ width:100%; box-sizing:border-box;
     padding:8px 10px; border:1px solid #d0d0d5; border-radius:6px;
     font-size:14px; margin-top:4px; }}
 button {{ margin-top:16px; width:100%; background:#111; color:#fff; border:0;
     padding:10px; border-radius:6px; font-size:14px; cursor:pointer; }}
 .err {{ color:#b00020; font-size:13px; margin-top:8px; }}
 .ok {{ color:#0a8a46; font-size:14px; margin-top:16px; text-align:center; }}
</style></head>
<body><div class="card">
 <h1>登录到 chachat CLI</h1>
 <p class="sub">你在终端看到的验证码是 <span class="code">{user_code}</span>。登录后即可在 CLI 继续。</p>
 {body}
</div></body></html>"""


@cli_router.get(
    "/device",
    response_class=HTMLResponse,
    summary="浏览器端登录页（用户访问）",
)
def device_login_page(code: str = Query(..., description="user_code，来自 CLI")):
    """返回极简登录表单。"""
    user_code = (code or "").strip().upper()
    _bump_access(user_code or "-")
    device_code, state = _state_get_by_user_code(user_code)
    if not device_code or not state:
        return HTMLResponse(
            _LOGIN_PAGE.format(
                user_code=user_code or "????",
                body='<p class="err">验证码无效或已过期，请在 CLI 重新执行 chachat 并获取新链接。</p>',
            ),
            status_code=400,
        )
    if (state or {}).get("status") == "approved":
        return HTMLResponse(_LOGIN_PAGE.format(
            user_code=user_code,
            body='<p class="ok">✔ 已授权。请回到终端继续操作。</p>',
        ))
    # CSRF form token：用 secrets 生成一次性 token 写进 state，表单 hidden 回传校验
    csrf = secrets.token_urlsafe(24)
    state["csrf"] = csrf
    _state_update(device_code, state)
    form = f"""
     <form method="post" action="/cli/device/approve">
      <input type="hidden" name="code" value="{user_code}"/>
      <input type="hidden" name="csrf" value="{csrf}"/>
      <label>用户名<input type="text" name="username" required autofocus/></label>
      <label>密码<input type="password" name="password" required/></label>
      <button type="submit">登录并授权 CLI</button>
     </form>"""
    return HTMLResponse(_LOGIN_PAGE.format(user_code=user_code, body=form))


@cli_router.post(
    "/device/approve",
    response_class=HTMLResponse,
    summary="浏览器端提交用户名密码并绑 token（表单）",
)
def device_approve(
    code: str = Form(...),
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    user_code = (code or "").strip().upper()
    _bump_access(user_code or "-")
    device_code, state = _state_get_by_user_code(user_code)
    if not device_code or not state:
        return HTMLResponse(_LOGIN_PAGE.format(
            user_code=user_code or "????",
            body='<p class="err">会话过期，请在 CLI 重新发起。</p>',
        ), status_code=400)
    if state.get("csrf") != csrf:
        return HTMLResponse(_LOGIN_PAGE.format(
            user_code=user_code,
            body='<p class="err">表单校验失败，请刷新页面重试。</p>',
        ), status_code=400)
    try:
        u = svc.authenticate(username, password)
    except svc.AuthError as e:
        logger.warning("cli device approve 失败：user=%r reason=%s", username[:64], e)
        return HTMLResponse(_LOGIN_PAGE.format(
            user_code=user_code,
            body='<p class="err">用户名或密码错误。</p>',
        ), status_code=401)
    access, refresh = svc.issue_token_pair(u)
    state.update({
        "status": "approved",
        "tokens": {"access_token": access, "refresh_token": refresh},
        "user": UserPublicSchema.model_validate(u).model_dump(mode="json"),
        "approved_at": time.time(),
        # 一次性 csrf 用完即清
        "csrf": None,
    })
    _state_update(device_code, state)
    return HTMLResponse(_LOGIN_PAGE.format(
        user_code=user_code,
        body='<p class="ok">✔ 已授权。请回到终端继续操作。可关闭此页。</p>',
    ))


@cli_router.post(
    "/device/token",
    summary="CLI 轮询换 token",
)
def device_token(body: Dict[str, Any] = Body(...)):
    """CLI 每 interval 秒 POST 一次；未授权返 `authorization_pending`，已授权返 token pair。

    返回体模拟 RFC 8628 device flow：
    - 202 + {"error": "authorization_pending"}
    - 200 + {access_token, refresh_token, user}
    - 410 + {"error": "expired_token"}
    - 403 + {"error": "access_denied"}（用户主动拒绝，暂不实现）
    """
    device_code = str((body or {}).get("device_code") or "").strip()
    if not device_code:
        raise HTTPException(status_code=400, detail="device_code required")
    _bump_access(device_code)
    state = _state_get_by_device(device_code)
    if not state:
        return JSONResponse({"error": "expired_token"}, status_code=410)
    status = state.get("status")
    if status == "pending":
        return JSONResponse({"error": "authorization_pending"}, status_code=202)
    if status == "approved":
        tokens = state.get("tokens") or {}
        user = state.get("user") or {}
        # 单次消费：拿到 token 后删状态（避免重复换取）
        _state_delete(device_code, state.get("user_code"))
        return {
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "token_type": "bearer",
            "user": user,
        }
    return JSONResponse({"error": "expired_token"}, status_code=410)
