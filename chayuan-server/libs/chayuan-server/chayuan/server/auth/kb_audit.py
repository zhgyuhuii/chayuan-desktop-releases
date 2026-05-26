"""KB 业务审计 7 类 hook（plan v1.3 §6.5 audit decisions）。

复用 ``observability/audit.audit()`` 落 ``chayuan_audit_log`` 表，提供 7 个语义化
helper，避免散落字符串：

  kb.app.login    App 鉴权成功（hit 任意需 HMAC 的接口时记一次/会话）
  kb.search       用户/App 调 search_batch / search_batch_stream（含成功条数）
  kb.download     用户/App 下载文件（含 dl_token 是否使用）
  kb.denied       任何 ACL 拒绝（403 / 401 unauth-context-required）
  kb.grant.add    admin 给 App 加 KB grant
  kb.grant.revoke admin 给 App 撤销 KB grant
  kb.app.rotate   admin rotate App secret（在 apps_store.rotate_secret 内部触发）

设计要点：
* 所有函数都 fail-open（``audit()`` 已经吞异常）；不记审计绝不阻断业务。
* user 形参既支持人类用户 dict（``{id, username, role}``）也支持应用账号 dict
  （``{id: "app:xxx", username: "app:foo", role: "app", app_id: ...}``），落库时
  ``user_id`` 仅对人类用户 int(id) 写入，App 留 NULL 但 ``username`` 写 ``app:xxx``。
* `target_type` 一律 ``"kb"`` 或 ``"kb_grant"`` 或 ``"app"``。
* 保留期 ≥ 90 天由表生命周期管理（cron 在外部）；本模块不负责清理。

使用示例（在 endpoint 里）::

    from chayuan.server.auth import kb_audit
    from chayuan.server.observability.audit import AuditStopwatch
    with AuditStopwatch() as w:
        out = await run_batch(...)
    kb_audit.search_ok(user, kbs=body.knowledge_base_names, queries=len(body.queries),
                       chunks=len(out.merged), elapsed_ms=w.elapsed_ms,
                       request_id=request.headers.get("x-request-id", ""))
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from chayuan.server.observability.audit import audit as _audit


# ---------------------------------------------------------------------------
# 内部归一化
# ---------------------------------------------------------------------------

def _norm_user(user: Any) -> Dict[str, Any]:
    """归一为 {id, username, role}；兼容 dict / AppSpec / None。

    重要：``audit()`` 内部对 id 做 int() 转换，所以对 App 主体（id 形如 "app:xxx"）
    必须返回 id=None，把"app:xxx"信息放进 username 字段（落库时 user_id=NULL）。
    """
    if user is None:
        return {"id": None, "username": "", "role": ""}
    if isinstance(user, dict):
        raw_id = user.get("id")
        # str 形态的 id（"app:xxx" 或 "dl_token" 这类伪 id）一律 id=None，避免 int() 抛异常
        if isinstance(raw_id, str) and not raw_id.isdigit():
            return {
                "id": None,
                "username": str(user.get("username") or raw_id),
                "role": str(user.get("role") or ""),
            }
        return {
            "id": raw_id,
            "username": str(user.get("username") or ""),
            "role": str(user.get("role") or ""),
        }
    # AppSpec
    aid = getattr(user, "app_id", None)
    return {
        "id": None,  # App 主体永远不写 user_id
        "username": f"app:{getattr(user, 'name', None) or aid or ''}",
        "role": "app",
    }


def subject_kind_of(user: Any) -> str:
    """归一 metrics 用的 subject 类型：user / app / dl_token / unknown。"""
    if user is None:
        return "unknown"
    norm = _norm_user(user)
    role = (norm.get("role") or "").lower()
    if role == "app":
        return "app"
    if role == "dl_token":
        return "dl_token"
    if isinstance(user, dict) and isinstance(user.get("id"), str) and str(user.get("id")).startswith("app:"):
        return "app"
    if not isinstance(user, dict) and getattr(user, "app_id", None):
        return "app"
    return "user"


def _kbs_str(kbs: Iterable[str]) -> str:
    return ",".join(sorted(set(map(str, kbs or []))))[:64]


# ---------------------------------------------------------------------------
# 7 类 action
# ---------------------------------------------------------------------------

ACTION_LOGIN  = "kb.app.login"
ACTION_SEARCH = "kb.search"
ACTION_DL     = "kb.download"
ACTION_DENIED = "kb.denied"
ACTION_GRANT  = "kb.grant.add"
ACTION_REVOKE = "kb.grant.revoke"
ACTION_ROTATE = "kb.app.rotate"

ALL_ACTIONS = (
    ACTION_LOGIN, ACTION_SEARCH, ACTION_DL, ACTION_DENIED,
    ACTION_GRANT, ACTION_REVOKE, ACTION_ROTATE,
)


def app_login(app_or_user: Any, *, request_id: str = "", endpoint: str = "") -> None:
    u = _norm_user(app_or_user)
    _audit(
        ACTION_LOGIN,
        user=u, request_id=request_id,
        target_type="app", target_id=u["id"] or "",
        status="ok", payload={"endpoint": endpoint},
    )


def search_ok(
    user: Any, *,
    kbs: Iterable[str], queries: int, chunks: int,
    elapsed_ms: int = 0, request_id: str = "",
) -> None:
    u = _norm_user(user)
    _audit(
        ACTION_SEARCH,
        user=u, request_id=request_id,
        target_type="kb", target_id=_kbs_str(kbs),
        status="ok",
        payload={"queries": int(queries), "kbs": list(sorted(set(kbs or [])))},
        result={"chunks": int(chunks)},
        elapsed_ms=elapsed_ms,
    )


def download_ok(
    user: Any, *,
    kb: str, file_name: str, via_dl_token: bool = False,
    request_id: str = "", elapsed_ms: int = 0,
) -> None:
    u = _norm_user(user)
    _audit(
        ACTION_DL,
        user=u, request_id=request_id,
        target_type="kb", target_id=str(kb)[:64],
        status="ok",
        payload={"file": str(file_name)[:256], "via_dl_token": bool(via_dl_token)},
        elapsed_ms=elapsed_ms,
    )


def denied(
    user: Any, *,
    kb: str = "", action: str = "read",
    code: int = 4031, reason: str = "", request_id: str = "",
) -> None:
    """任何 ACL/鉴权拒绝都进这里。``code`` 与 contract §4 错误码对齐。"""
    u = _norm_user(user)
    _audit(
        ACTION_DENIED,
        user=u, request_id=request_id,
        target_type="kb", target_id=str(kb)[:64],
        status="denied",
        payload={"want_action": action, "code": int(code)},
        error_msg=str(reason or "")[:4000],
    )


def grant_added(
    admin_user: Any, *,
    app_id: str, kb_name: str, role: str,
    expires_at: Optional[str] = None, request_id: str = "",
) -> None:
    u = _norm_user(admin_user)
    _audit(
        ACTION_GRANT,
        user=u, request_id=request_id,
        target_type="kb_grant", target_id=f"{app_id}:{kb_name}"[:64],
        status="ok",
        payload={"app_id": app_id, "kb_name": kb_name, "role": role,
                 "expires_at": expires_at},
    )


def grant_revoked(
    admin_user: Any, *,
    app_id: str, kb_name: str,
    request_id: str = "",
) -> None:
    u = _norm_user(admin_user)
    _audit(
        ACTION_REVOKE,
        user=u, request_id=request_id,
        target_type="kb_grant", target_id=f"{app_id}:{kb_name}"[:64],
        status="ok",
        payload={"app_id": app_id, "kb_name": kb_name},
    )


def app_rotated(
    admin_user: Any, *,
    app_id: str, app_name: str = "", request_id: str = "",
) -> None:
    u = _norm_user(admin_user)
    _audit(
        ACTION_ROTATE,
        user=u, request_id=request_id,
        target_type="app", target_id=str(app_id)[:64],
        status="ok",
        payload={"app_id": app_id, "app_name": app_name},
    )
