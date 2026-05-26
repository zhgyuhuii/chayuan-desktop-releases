"""审计日志落库 helper。

- 设计为**绝不抛异常**：日志失败不能影响主流程。
- 大 payload 自动截断（4KB 上限）。
- 读密码 / token 等敏感字段走固定黑名单脱敏。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from chayuan.server.db.models.audit_log_model import AuditLogModel
from chayuan.server.db.session import session_scope

logger = logging.getLogger("chayuan.observability.audit")

SENSITIVE_KEYS = ("password", "password_enc", "token", "api_key", "secret", "authorization")


def _scrub(obj: Any, _depth: int = 0) -> Any:
    if _depth > 4 or obj is None:
        return obj
    if isinstance(obj, dict):
        return {
            k: ("***" if str(k).lower() in SENSITIVE_KEYS else _scrub(v, _depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(x, _depth + 1) for x in obj][:40]
    if isinstance(obj, (str, bytes)):
        s = obj.decode("utf-8", errors="replace") if isinstance(obj, bytes) else obj
        return s[:2000]
    return obj


def _dumps(obj: Any) -> str:
    try:
        return json.dumps(_scrub(obj), ensure_ascii=False, default=str)[:4000]
    except Exception:  # noqa: BLE001
        return ""


def audit(
    action: str,
    *,
    user: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    target_type: str = "",
    target_id: Any = "",
    status: str = "ok",
    payload: Any = None,
    result: Any = None,
    error_msg: str = "",
    elapsed_ms: int = 0,
) -> None:
    try:
        uid = None
        uname = ""
        if isinstance(user, dict):
            uid = user.get("id")
            uname = str(user.get("username") or "")
        with session_scope() as s:
            s.add(AuditLogModel(
                user_id=int(uid) if uid is not None else None,
                username=uname[:128],
                request_id=(request_id or "")[:64],
                action=action[:64],
                target_type=(target_type or "")[:32],
                target_id=str(target_id)[:64],
                status=(status or "ok")[:16],
                payload=_dumps(payload),
                result=_dumps(result),
                error_msg=(error_msg or "")[:4000],
                elapsed_ms=int(elapsed_ms or 0),
            ))
    except Exception as e:  # noqa: BLE001
        logger.warning("audit 落库失败（忽略）：%r", e)


class AuditStopwatch:
    """上下文：自动填 elapsed_ms；结合 audit() 使用。

    用法::

        with AuditStopwatch() as w:
            ...
        audit("source.search", elapsed_ms=w.elapsed_ms, ...)
    """

    def __enter__(self):
        self._t = time.time()
        self.elapsed_ms = 0
        return self

    def __exit__(self, exc_type, exc, tb):
        self.elapsed_ms = int((time.time() - self._t) * 1000)
        return False
