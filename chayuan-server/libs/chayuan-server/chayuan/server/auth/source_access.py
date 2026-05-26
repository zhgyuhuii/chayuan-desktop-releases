"""知识源访问控制。与 kb_access 语义对齐：

- admin 通吃
- owner 可读可写可管（含删除、改白名单、授权）
- source_access_grants.role=editor → 可读可写可授权
- source_access_grants.role=reader → 仅可读
- visibility=public → 任何登录用户可读

AUTH_REQUIRED=false / 未登录 场景下允许放行，维持单机调试体验。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from chayuan.server.db.models.knowledge_source_model import (
    KnowledgeSourceModel,
    SourceAccessGrantModel,
)
from chayuan.server.db.session import session_scope

logger = logging.getLogger("chayuan.auth.source_access")


def _uid(user) -> Optional[int]:
    if user is None:
        return None
    if isinstance(user, dict):
        v = user.get("id")
    else:
        v = getattr(user, "id", None)
    try:
        return int(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def _role(user) -> str:
    if user is None:
        return ""
    if isinstance(user, dict):
        return str(user.get("role") or "")
    return str(getattr(user, "role", "") or "")


def source_access_for(user, source_id: int) -> Optional[str]:
    """返回 "owner" / "editor" / "reader" / None。"""
    with session_scope() as s:
        src = s.get(KnowledgeSourceModel, int(source_id))
        if src is None:
            return None
        if _role(user) == "admin":
            return "owner"
        uid = _uid(user)
        # legacy/共享源：与文档 KB 的 owner_id=NULL 语义对齐，登录用户可读。
        if uid is not None and src.owner_id is None:
            return "reader"
        if uid is not None and src.owner_id is not None and int(src.owner_id) == uid:
            return "owner"
        if (src.visibility or "private") == "public":
            base = "reader"
        else:
            base = None
        if uid is not None:
            g = s.query(SourceAccessGrantModel).filter(
                SourceAccessGrantModel.source_id == int(source_id),
                SourceAccessGrantModel.user_id == uid,
            ).one_or_none()
            if g is not None:
                if g.role == "editor":
                    return "editor"
                return "reader"
        return base


def can_read(user, source_id: int) -> bool:
    if user is None:
        return True  # AUTH_REQUIRED=false 兜底
    return source_access_for(user, source_id) is not None


def can_write(user, source_id: int) -> bool:
    if user is None:
        return True
    r = source_access_for(user, source_id)
    return r in ("owner", "editor")


def can_admin(user, source_id: int) -> bool:
    """owner / admin 专属：改可见性、删源、批量授权。"""
    if user is None:
        return True
    return source_access_for(user, source_id) == "owner"


def filter_accessible(user, source_ids: List[int]) -> List[int]:
    """批量过滤用户可读的 source_id 列表。"""
    if user is None:
        return list(source_ids)
    if _role(user) == "admin":
        return list(source_ids)
    out = []
    for sid in source_ids:
        if can_read(user, int(sid)):
            out.append(int(sid))
    return out
