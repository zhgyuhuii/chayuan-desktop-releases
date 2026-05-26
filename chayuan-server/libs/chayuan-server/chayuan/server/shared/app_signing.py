"""App 签名 / 校验（对齐微信 & 飞书的 HMAC-SHA256 套路）。

约定：
- 请求头里带 ``X-App-Id``、``X-Timestamp``（秒级 Unix 时间戳）、``X-Sign``；
- 被签材料：``timestamp + "\\n" + raw_body``，body 空时视为 ``""``；
- HMAC-SHA256 over (``app_secret.encode()``)，输出 hex 小写。

校验规则：
- 时钟漂移容忍 ±5 min（可配置），超出拒绝；
- app_id 必须在 ``apps_store`` 中存在且 enabled=true；
- 签名必须 byte-level 匹配（secret 对错不会泄漏字符差异，走 hmac.compare_digest）。

回调（Chayuan → App）同样用这套材料，方向反过来而已：
- POST 到 ``app.callback_url``；
- 发送方带 ``X-App-Id / X-Timestamp / X-Sign``；
- 接收方用自己持有的 ``app_secret`` 复算验证。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid


DEFAULT_SKEW_SECONDS = 300  # ±5 分钟时钟漂移


def new_app_id() -> str:
    """32 位 hex；和阿里云 AccessKey 风格一致，便于日志肉眼识别。"""
    return uuid.uuid4().hex


def new_app_secret() -> str:
    """48 字节随机；转 url-safe base64；实际长度约 64 字符。"""
    return secrets.token_urlsafe(48)


def sign(app_secret: str, timestamp: str, raw_body: bytes | str | None) -> str:
    """生成 hex 小写签名。"""
    if raw_body is None:
        body_bytes = b""
    elif isinstance(raw_body, str):
        body_bytes = raw_body.encode("utf-8")
    else:
        body_bytes = raw_body
    material = str(timestamp).encode("utf-8") + b"\n" + body_bytes
    return hmac.new(app_secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def verify(
    app_secret: str,
    timestamp: str,
    raw_body: bytes | str | None,
    given_sign: str,
    *,
    skew_seconds: int = DEFAULT_SKEW_SECONDS,
) -> tuple[bool, str]:
    """返回 ``(ok, reason)``；失败带可读原因供日志。"""
    if not given_sign:
        return False, "missing X-Sign"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "invalid X-Timestamp"
    now = int(time.time())
    if abs(now - ts) > int(skew_seconds):
        return False, (
            f"timestamp drift too large: |now-ts|={abs(now-ts)}s > skew={skew_seconds}s"
        )
    expected = sign(app_secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, given_sign):
        return False, "signature mismatch"
    return True, ""


def make_signed_headers(
    app_id: str,
    app_secret: str,
    raw_body: bytes | str | None,
    *,
    extra: dict | None = None,
) -> dict:
    """给「主动发 HTTP 请求」方生成带签名的头。"""
    ts = str(int(time.time()))
    hdrs = {
        "X-App-Id": app_id,
        "X-Timestamp": ts,
        "X-Sign": sign(app_secret, ts, raw_body),
        "Content-Type": "application/json; charset=utf-8",
    }
    if extra:
        hdrs.update(extra)
    return hdrs
