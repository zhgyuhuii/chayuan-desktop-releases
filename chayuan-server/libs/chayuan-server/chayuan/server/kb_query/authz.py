from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from chayuan.server.kb_query.registry import KnowledgeRef


@dataclass(frozen=True)
class Subject:
    user: Optional[Dict[str, Any]]
    mode: str

    @property
    def user_id(self) -> Optional[int]:
        if not self.user:
            return None
        v = self.user.get("id")
        return int(v) if v is not None else None

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.get("role") == "admin")


def auth_required() -> bool:
    try:
        from chayuan.settings import Settings

        return bool(getattr(Settings.basic_settings, "AUTH_REQUIRED", False))
    except Exception:
        return False


def subject_from_user(user: Optional[Dict[str, Any]]) -> Subject:
    if auth_required() and user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Subject(user=user, mode="authenticated" if user else "guest")


def _legacy_guest_visible() -> bool:
    try:
        from chayuan.settings import Settings

        return bool(getattr(Settings.basic_settings, "KB_QUERY_LEGACY_GUEST_VISIBLE", False))
    except Exception:
        return False


def document_permission(subject: Subject, kb_name: str) -> Optional[str]:
    from chayuan.server.auth.access import kb_access_for
    from chayuan.server.db.repository.knowledge_base_repository import list_kbs_from_db

    if subject.user is not None:
        return kb_access_for(subject.user, kb_name)

    rows = list_kbs_from_db(min_file_count=-1)
    row = next((r for r in rows if (r.kb_name or "").lower() == kb_name.lower()), None)
    if row is None:
        return None
    if (row.visibility or "private") == "public":
        return "reader"
    if row.owner_id is None and _legacy_guest_visible():
        return "reader"
    return None


def source_permission(subject: Subject, source_id: int) -> Optional[str]:
    from chayuan.server.auth.source_access import source_access_for

    return source_access_for(subject.user, int(source_id))


def permission_for(subject: Subject, ref: KnowledgeRef) -> Optional[str]:
    if ref.kind == "document":
        return document_permission(subject, ref.name)
    if ref.kb_id.startswith("src:"):
        return source_permission(subject, int(ref.raw_id))
    return None


def assert_readable(subject: Subject, ref: KnowledgeRef) -> str:
    perm = permission_for(subject, ref)
    if perm not in ("owner", "editor", "reader"):
        raise HTTPException(403, "permission denied")
    return perm


def list_readable_items(
    subject: Subject,
    *,
    kind: Optional[str] = None,
    include_vector: bool = False,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    if kind in (None, "", "document"):
        from chayuan.server.db.repository.knowledge_base_repository import list_kbs_from_db
        from chayuan.server.kb_query.index_status import document_index_status

        for kb in list_kbs_from_db(min_file_count=-1):
            perm = document_permission(subject, kb.kb_name)
            if perm not in ("owner", "editor", "reader"):
                continue
            idx = document_index_status(kb.kb_name)
            items.append(
                {
                    "kb_id": f"doc:{kb.kb_name}",
                    "name": kb.kb_name,
                    "display_name": kb.kb_name,
                    "kind": "document",
                    "visibility": kb.visibility or "private",
                    "permission": perm,
                    "owner_id": kb.owner_id,
                    "file_count": kb.file_count or 0,
                    "index_status": idx["status"],
                    "index_status_detail": idx,
                    "retrieval_capabilities": ["vector", "hybrid", "rerank", "citation"],
                    "created_at": kb.create_time,
                }
            )

    if kind in (None, "", "structured", "image", "vector"):
        from chayuan.server.db.repository.knowledge_source_repository import list_sources

        for src in list_sources():
            raw_kind = str(src.get("kind") or "").lower()
            item_kind = "structured" if raw_kind in ("sql", "mongo", "es") else raw_kind
            if item_kind == "vector" and not include_vector:
                continue
            if kind not in (None, "", item_kind):
                continue
            perm = source_permission(subject, int(src["id"]))
            if perm not in ("owner", "editor", "reader"):
                continue
            items.append(
                {
                    "kb_id": f"src:{src['id']}",
                    "name": src.get("name"),
                    "display_name": src.get("display_name") or src.get("name"),
                    "kind": item_kind,
                    "sub_kind": raw_kind,
                    "visibility": src.get("visibility") or "private",
                    "permission": perm,
                    "owner_id": src.get("owner_id"),
                    "index_status": "ready",
                    "retrieval_capabilities": ["text2sql", "table_rows", "citation"]
                    if item_kind == "structured"
                    else ["vector", "citation"],
                    "created_at": src.get("create_time"),
                }
            )

    return items

