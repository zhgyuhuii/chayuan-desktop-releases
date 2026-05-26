from __future__ import annotations

from typing import Any, Dict, Optional

from chayuan.server.kb_query.authz import Subject, list_readable_items
from chayuan.server.kb_query.schemas import SearchRequest
from chayuan.server.retrieval.query import search as unified_search


def list_knowledge_bases(
    subject: Subject,
    *,
    kind: str | None = None,
    include_vector: bool = False,
) -> Dict[str, Any]:
    items = list_readable_items(subject, kind=kind, include_vector=include_vector)
    return {"code": 0, "msg": "ok", "data": items}


def search(subject: Subject, req: SearchRequest, *, request_id: str = "", job_id: Optional[str] = None) -> Dict[str, Any]:
    return unified_search(subject, req, request_id=request_id, job_id=job_id)

