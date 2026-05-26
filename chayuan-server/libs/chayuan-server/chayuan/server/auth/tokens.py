"""JWT 签发 / 校验。

自带极简 HS256 / HS384 / HS512 实现，不引入 PyJWT 依赖。特性：
- header + payload + 签名都用 base64url 无 padding 编码；
- 支持 `exp` / `iat` / `nbf` 三个时间 claim 的默认校验；
- 其它业务 claim（`sub`、`type`、`role` 等）由调用方自由填。

token type：
- "access"：短 TTL，给接口直接带；
- "refresh"：长 TTL，只能换新 access，不能直接访问业务接口。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("chayuan.auth.tokens")

_ALG_TO_HASH = {
    "HS256": hashlib.sha256,
    "HS384": hashlib.sha384,
    "HS512": hashlib.sha512,
}


class TokenError(Exception):
    pass


# --------------------------------------------------------------------------- 
# base64url
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------

# Redis 里持久化 fallback JWT secret 的 key。所有 chayuan-server 实例共享。
# 用 SETNX 写入,首个启动的进程定调,后续进程读出来用同一份 → token 跨重启 / 跨实例稳定。
_REDIS_SECRET_KEY = "chayuan:auth:jwt-fallback-secret"

_FALLBACK_SECRET: Optional[str] = None
_FALLBACK_SOURCE: str = ""  # 日志用:'redis' / 'memory' / 'env'
_WARNED_RANDOM = False


def _load_secret_from_redis() -> Optional[str]:
    """同步从 Redis 读 / 写 fallback secret,跨进程跨重启稳定。

    流程:
      1. GET → 命中即返。
      2. miss → 本地 token_urlsafe(48) + SETNX(NX 防并发覆盖)→ 再 GET 一次拿
         真正生效的值(即使本进程 SETNX 输了竞争,也能读到赢家的值)。

    任何 Redis 异常 / 包未装 / URL 未配 → 返 None,由调用方退回内存随机。
    用 sync 客户端;``shared.get_redis`` 是 async 的,这里 token 签发链路是
    sync 的,不便引入 await。一次性短超时,失败立即降级。
    """
    try:
        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        import redis as _redis  # type: ignore
    except ImportError:
        return None
    try:
        client = _redis.from_url(
            url,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            decode_responses=True,
        )
        existing = client.get(_REDIS_SECRET_KEY)
        if existing:
            return str(existing)
        # miss:写一份候选;NX 让赢家定调
        candidate = secrets.token_urlsafe(48)
        try:
            client.set(_REDIS_SECRET_KEY, candidate, nx=True)
        except Exception:  # noqa: BLE001
            pass
        # 再读一次:本进程 SETNX 输了也能拿到赢家值
        final = client.get(_REDIS_SECRET_KEY)
        return str(final) if final else candidate
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "无法从 Redis 读取 JWT fallback secret(%s);本进程退回内存随机,"
            "重启后 token 会失效。建议:① 设 JWT_SECRET 显式固定;② 修复 Redis。",
            type(e).__name__,
        )
        return None


def _get_secret() -> Tuple[str, str]:
    global _FALLBACK_SECRET, _FALLBACK_SOURCE, _WARNED_RANDOM
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        alg = (getattr(bs, "JWT_ALGORITHM", "HS256") or "HS256").upper()
        secret = (getattr(bs, "JWT_SECRET", "") or "").strip()
    except Exception:
        alg = "HS256"
        secret = ""

    # 优先级 1:basic_settings.JWT_SECRET 显式配置 → 直接用,稳定可控,生产首选。
    if secret:
        if alg not in _ALG_TO_HASH:
            logger.warning("unknown JWT_ALGORITHM=%s, fallback to HS256", alg)
            alg = "HS256"
        return secret, alg

    # 优先级 2:已经在本进程缓存过 fallback → 直接用(无论 redis / memory 来源)
    if _FALLBACK_SECRET:
        if alg not in _ALG_TO_HASH:
            alg = "HS256"
        return _FALLBACK_SECRET, alg

    # 优先级 3:Redis 持久化 fallback(跨重启 / 跨实例稳定)
    persisted = _load_secret_from_redis()
    if persisted:
        _FALLBACK_SECRET = persisted
        _FALLBACK_SOURCE = "redis"
        logger.info(
            "JWT_SECRET 未配置;从 Redis 加载 fallback secret(跨重启稳定,key=%s)",
            _REDIS_SECRET_KEY,
        )
        if alg not in _ALG_TO_HASH:
            alg = "HS256"
        return _FALLBACK_SECRET, alg

    # 优先级 4:进程内一次性随机(纯内存,重启失效)— 最后兜底,只在
    # JWT_SECRET 没配 + Redis 不可用时触发。
    _FALLBACK_SECRET = secrets.token_urlsafe(48)
    _FALLBACK_SOURCE = "memory"
    if not _WARNED_RANDOM:
        logger.warning(
            "JWT_SECRET 未配置 + Redis 不可用 → 使用进程内一次性随机密钥,"
            "所有 token 在本进程重启后失效。建议:① 在 basic_settings.yaml 配 JWT_SECRET;"
            "② 或配 REDIS_URL 让 fallback 持久化(已自动启用 chayuan:auth:jwt-fallback-secret)。"
        )
        _WARNED_RANDOM = True
    if alg not in _ALG_TO_HASH:
        alg = "HS256"
    return _FALLBACK_SECRET, alg


# ---------------------------------------------------------------------------
# 签 / 验
# ---------------------------------------------------------------------------

def _sign(alg: str, key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, _ALG_TO_HASH[alg]).digest()


def create_token(
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
    token_type: str = "access",
) -> str:
    secret, alg = _get_secret()
    now = int(time.time())
    body = dict(payload)
    body.setdefault("iat", now)
    body.setdefault("nbf", now)
    body["exp"] = now + int(ttl_seconds)
    body["type"] = token_type

    header = {"alg": alg, "typ": "JWT"}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url_encode(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _b64url_encode(_sign(alg, secret.encode("utf-8"), signing_input))
    return f"{h}.{p}.{sig}"


@dataclass
class DecodedToken:
    payload: Dict[str, Any]
    token_type: str
    user_id: Optional[int]
    username: Optional[str]


def decode_token(token: str, *, expected_type: Optional[str] = None) -> DecodedToken:
    if not token or token.count(".") != 2:
        raise TokenError("malformed token")

    secret, alg_default = _get_secret()
    h, p, s = token.split(".")
    try:
        header = json.loads(_b64url_decode(h))
        payload = json.loads(_b64url_decode(p))
    except Exception as e:
        raise TokenError(f"bad b64/json: {e!r}")

    alg = header.get("alg", alg_default)
    if alg not in _ALG_TO_HASH:
        raise TokenError(f"unsupported alg {alg}")

    signing_input = f"{h}.{p}".encode("ascii")
    expected_sig = _sign(alg, secret.encode("utf-8"), signing_input)
    try:
        got_sig = _b64url_decode(s)
    except Exception:
        raise TokenError("bad signature")
    if not hmac.compare_digest(expected_sig, got_sig):
        raise TokenError("signature mismatch")

    now = int(time.time())
    if "exp" in payload and int(payload["exp"]) < now:
        raise TokenError("token expired")
    if "nbf" in payload and int(payload["nbf"]) > now:
        raise TokenError("token not yet valid")

    ttype = str(payload.get("type") or "")
    if expected_type and ttype != expected_type:
        raise TokenError(f"wrong token type: got {ttype!r}, expect {expected_type!r}")

    sub = payload.get("sub")
    try:
        user_id = int(sub) if sub is not None else None
    except (TypeError, ValueError):
        user_id = None

    return DecodedToken(
        payload=payload,
        token_type=ttype,
        user_id=user_id,
        username=payload.get("username"),
    )


def create_access_token(user_id: int, username: str, role: str = "user") -> str:
    from chayuan.settings import Settings
    ttl = int(getattr(Settings.basic_settings, "JWT_ACCESS_TTL_SECONDS", 3600))
    return create_token(
        {"sub": str(user_id), "username": username, "role": role},
        ttl_seconds=ttl,
        token_type="access",
    )


def create_refresh_token(user_id: int, username: str) -> str:
    from chayuan.settings import Settings
    ttl = int(getattr(Settings.basic_settings, "JWT_REFRESH_TTL_SECONDS", 7 * 24 * 3600))
    return create_token(
        {"sub": str(user_id), "username": username},
        ttl_seconds=ttl,
        token_type="refresh",
    )
