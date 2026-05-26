from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from chayuan.server.kb_query.authz import Subject, document_permission, source_permission
from chayuan.server.retrieval.query.refs import KnowledgeRef


def permission_for(subject: Subject, ref: KnowledgeRef) -> Optional[str]:
    if ref.kind == "document":
        return document_permission(subject, ref.name)
    if ref.kb_id.startswith("src:"):
        return source_permission(subject, int(ref.raw_id))
    return None


def assert_readable(subject: Subject, ref: KnowledgeRef) -> str:
    perm = permission_for(subject, ref)
    if perm not in ("owner", "editor", "reader", "admin"):
        raise HTTPException(403, f"permission denied on {ref.kb_id}")
    return "reader" if perm == "admin" else perm
