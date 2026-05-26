"""开放平台 App → 知识库 ACL（plan v1.3 §4.3）。

与 ``auth/access.py``（user→kb）平行：本模块管 App→KB。

入口：
- ``app_can_read_kb(app_id_or_spec, kb_name) -> bool``
- ``app_can_write_kb(app_id_or_spec, kb_name) -> bool``
- ``list_app_accessible_kbs(app_id_or_spec) -> List[str]``
- ``app_kb_role(app_id_or_spec, kb_name) -> Optional[str]``  # 给前端徽章
- ``add_grant(app_id, kb_name, *, role, granted_by=None, expires_at=None)``
- ``remove_grant(app_id, kb_name) -> bool``
- ``list_grants_for_app(app_id) -> List[dict]``
- ``purge_app_grants(app_id) -> int``  # apps_store.delete_app 后调

关键安全决策（详见 plan §4.3.1）：
* App 默认 **不可见 public KB**；除非 AppSpec.scopes 含 ``kb:public-read``
  → 这是一个新 scope，意义是"明确授权该 App 看任何 visibility=public 的 KB"
  → 没这个 scope 的 App 只能看显式 grant 的 KB
* App 可读 KB 必须 ① AppSpec.scopes 覆盖 ``kb:read`` 且 ② grant/public 任一通过
* App 可写 KB 必须 ① AppSpec.scopes 覆盖 ``kb:write`` 且 ② grant.role=editor
* App **永不**是 owner（不能创建/删除 KB）
* grant 过期（expires_at < now）自动失效，调用方不需要先 cron 清理也安全

性能：
* ``_load_app_id`` 接受 AppSpec 或 str，避免重复解析；
* DB 命中后会被外层 lru/redis 缓存（caller 决定）；本模块本身不缓存避免污染读视图。
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Iterable, List, Optional, Set, Union

from sqlalchemy import and_, or_

from chayuan.server.db.models.app_kb_grant_model import AppKBGrantModel
from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
from chayuan.server.db.session import session_scope
from chayuan.server.shared.scopes import covers as _scopes_cover, sanitize_scope_list

logger = logging.getLogger("chayuan.auth.app_acl")

# ---------------------------------------------------------------------------
# 类型工具
# ---------------------------------------------------------------------------

# AppSpec 不强依赖（避免循环 import），用 duck typing
def _load_app(app_id_or_spec: Union[str, Any]) -> Optional[Any]:
    if app_id_or_spec is None:
        return None
    if isinstance(app_id_or_spec, str):
        try:
            from chayuan.server.config_panel.apps_store import get_app
        except Exception:  # noqa: BLE001
            return None
        return get_app(app_id_or_spec)
    # 已经是 AppSpec
    return app_id_or_spec


def _app_id(app_id_or_spec: Union[str, Any]) -> str:
    if isinstance(app_id_or_spec, str):
        return app_id_or_spec
    return str(getattr(app_id_or_spec, "app_id", "") or "")


def _scopes(spec: Any) -> List[str]:
    return sanitize_scope_list(list(getattr(spec, "scopes", None) or []))


def _has_scope(spec: Any, required: str) -> bool:
    have = _scopes(spec)
    return any(_scopes_cover([s], required) for s in have)


# ---------------------------------------------------------------------------
# KB 加载
# ---------------------------------------------------------------------------

def _get_kb_by_name(name: str) -> Optional[KnowledgeBaseModel]:
    if not name:
        return None
    with session_scope() as s:
        kb = (
            s.query(KnowledgeBaseModel)
            .filter(KnowledgeBaseModel.kb_name == name)
            .one_or_none()
        )
        if kb is not None:
            s.expunge(kb)
        return kb


def _get_kb_by_id(kb_id: int) -> Optional[KnowledgeBaseModel]:
    with session_scope() as s:
        kb = s.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == int(kb_id)).one_or_none()
        if kb is not None:
            s.expunge(kb)
        return kb


# ---------------------------------------------------------------------------
# Grant 查询（带过期过滤）
# ---------------------------------------------------------------------------

def _grant_role(app_id: str, kb_id: int) -> Optional[str]:
    """返回当前 App 对 kb_id 的有效 grant role（已过滤过期）。"""
    if not app_id or not kb_id:
        return None
    now = _dt.datetime.utcnow()
    with session_scope() as s:
        g = (
            s.query(AppKBGrantModel)
            .filter(
                AppKBGrantModel.app_id == app_id,
                AppKBGrantModel.kb_id == int(kb_id),
                or_(AppKBGrantModel.expires_at.is_(None), AppKBGrantModel.expires_at > now),
            )
            .one_or_none()
        )
        return g.role if g else None


def _list_granted_kb_ids(app_id: str) -> Set[int]:
    if not app_id:
        return set()
    now = _dt.datetime.utcnow()
    with session_scope() as s:
        rows = (
            s.query(AppKBGrantModel.kb_id)
            .filter(
                AppKBGrantModel.app_id == app_id,
                or_(AppKBGrantModel.expires_at.is_(None), AppKBGrantModel.expires_at > now),
            )
            .all()
        )
        return {int(r[0]) for r in rows}


# ---------------------------------------------------------------------------
# 公开 API：ACL 决策
# ---------------------------------------------------------------------------

def app_kb_role(app_id_or_spec: Union[str, Any], kb_name: str) -> Optional[str]:
    """返回 App 对 KB 的有效 role：'editor' | 'reader' | None。

    优先级：grant.editor > grant.reader > public(若 App 有 ``kb:public-read``)。
    """
    spec = _load_app(app_id_or_spec)
    if spec is None or not getattr(spec, "enabled", True):
        return None
    kb = _get_kb_by_name(kb_name)
    if kb is None:
        return None

    # grant 优先（即便 public 也尊重 editor 的 grant，便于审计/写入）
    role = _grant_role(_app_id(spec), int(kb.id))
    if role:
        # plan v1.3 §6.5 决策 3：APP 永不是 owner。
        # 防御 DB 被外部篡改：clamp 到合法集合，未知 role 视为无效。
        if role in _ALLOWED_GRANT_ROLES:
            return role
        logger.warning(
            "app_kb_role: 拒绝非法 grant role %r for app=%s kb=%s（DB 可能被篡改）",
            role, _app_id(spec), kb_name,
        )
        return None

    if (kb.visibility or "private") == "public" and _has_scope(spec, "kb:public-read"):
        return "reader"

    return None


def app_can_read_kb(app_id_or_spec: Union[str, Any], kb_name: str) -> bool:
    spec = _load_app(app_id_or_spec)
    if spec is None or not getattr(spec, "enabled", True):
        return False
    if not _has_scope(spec, "kb:read"):
        return False
    return app_kb_role(spec, kb_name) in ("reader", "editor")


def app_can_write_kb(app_id_or_spec: Union[str, Any], kb_name: str) -> bool:
    spec = _load_app(app_id_or_spec)
    if spec is None or not getattr(spec, "enabled", True):
        return False
    if not _has_scope(spec, "kb:write"):
        return False
    return app_kb_role(spec, kb_name) == "editor"


def list_app_accessible_kbs(app_id_or_spec: Union[str, Any]) -> List[str]:
    """所有 App 至少 reader 可见的 KB name 列表（含 public 路径）。"""
    spec = _load_app(app_id_or_spec)
    if spec is None or not getattr(spec, "enabled", True):
        return []
    if not _has_scope(spec, "kb:read"):
        return []
    aid = _app_id(spec)
    granted_ids = _list_granted_kb_ids(aid)
    public_ok = _has_scope(spec, "kb:public-read")

    out: List[str] = []
    with session_scope() as s:
        q = s.query(KnowledgeBaseModel)
        for kb in q.all():
            if int(kb.id) in granted_ids:
                out.append(kb.kb_name)
                continue
            if public_ok and (kb.visibility or "private") == "public":
                out.append(kb.kb_name)
                continue
    return out


# ---------------------------------------------------------------------------
# 公开 API：管理（admin 端点用）
# ---------------------------------------------------------------------------

_ALLOWED_GRANT_ROLES = ("reader", "editor")


def add_grant(
    app_id: str,
    kb_name: str,
    *,
    role: str = "reader",
    granted_by: Optional[int] = None,
    expires_at: Optional[_dt.datetime] = None,
) -> dict:
    """新增/覆盖一条 grant。(app_id, kb_id) 唯一，重复调用 = upsert。"""
    if not app_id or not kb_name:
        raise ValueError("app_id and kb_name are required")
    if role not in _ALLOWED_GRANT_ROLES:
        raise ValueError(f"role must be one of {_ALLOWED_GRANT_ROLES}, got {role!r}")
    kb = _get_kb_by_name(kb_name)
    if kb is None:
        raise LookupError(f"unknown kb {kb_name!r}")

    with session_scope() as s:
        existing = (
            s.query(AppKBGrantModel)
            .filter(
                AppKBGrantModel.app_id == app_id,
                AppKBGrantModel.kb_id == int(kb.id),
            )
            .one_or_none()
        )
        if existing is None:
            row = AppKBGrantModel(
                app_id=app_id,
                kb_id=int(kb.id),
                role=role,
                granted_by=granted_by,
                expires_at=expires_at,
            )
            s.add(row)
            s.flush()
            out = _row_to_dict(row, kb_name)
        else:
            existing.role = role
            existing.granted_by = granted_by if granted_by is not None else existing.granted_by
            existing.expires_at = expires_at
            s.flush()
            out = _row_to_dict(existing, kb_name)
    return out


def remove_grant(app_id: str, kb_name: str) -> bool:
    if not app_id or not kb_name:
        return False
    kb = _get_kb_by_name(kb_name)
    if kb is None:
        return False
    with session_scope() as s:
        n = (
            s.query(AppKBGrantModel)
            .filter(
                AppKBGrantModel.app_id == app_id,
                AppKBGrantModel.kb_id == int(kb.id),
            )
            .delete(synchronize_session=False)
        )
    return n > 0


def list_grants_for_app(app_id: str) -> List[dict]:
    if not app_id:
        return []
    with session_scope() as s:
        rows = (
            s.query(AppKBGrantModel, KnowledgeBaseModel.kb_name)
            .join(KnowledgeBaseModel, KnowledgeBaseModel.id == AppKBGrantModel.kb_id)
            .filter(AppKBGrantModel.app_id == app_id)
            .all()
        )
        return [_row_to_dict(g, name) for g, name in rows]


def purge_app_grants(app_id: str) -> int:
    """删除某 App 的全部 grant；apps_store.delete_app/disable 时调用。"""
    if not app_id:
        return 0
    with session_scope() as s:
        n = (
            s.query(AppKBGrantModel)
            .filter(AppKBGrantModel.app_id == app_id)
            .delete(synchronize_session=False)
        )
    return n


def purge_expired_grants(now: Optional[_dt.datetime] = None) -> int:
    """日常 cron 调用；删除已过期的 grant。"""
    n = 0
    cutoff = now or _dt.datetime.utcnow()
    with session_scope() as s:
        n = (
            s.query(AppKBGrantModel)
            .filter(AppKBGrantModel.expires_at.isnot(None))
            .filter(AppKBGrantModel.expires_at <= cutoff)
            .delete(synchronize_session=False)
        )
    if n:
        logger.info("purge_expired_grants removed %s rows", n)
    return n


def _row_to_dict(row: AppKBGrantModel, kb_name: str) -> dict:
    return {
        "id": int(row.id),
        "app_id": str(row.app_id),
        "kb_id": int(row.kb_id),
        "kb_name": kb_name,
        "role": str(row.role),
        "granted_by": int(row.granted_by) if row.granted_by is not None else None,
        "granted_at": row.granted_at.isoformat() if row.granted_at else "",
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


# ---------------------------------------------------------------------------
# 与 access.py 的桥接
# ---------------------------------------------------------------------------

def is_app_user(user: Any) -> bool:
    """``access.py`` 用：判断 ``request.state.user`` 是否是合成的应用账号。

    形如 ``user.id == "app:<app_id>"`` 即视为应用账号。
    """
    if user is None:
        return False
    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    return isinstance(uid, str) and uid.startswith("app:")


def extract_app_id(user: Any) -> Optional[str]:
    if not is_app_user(user):
        return None
    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    return str(uid)[4:] if isinstance(uid, str) else None
