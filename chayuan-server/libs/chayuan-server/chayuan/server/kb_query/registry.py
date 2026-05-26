from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException


@dataclass(frozen=True)
class KnowledgeRef:
    kb_id: str
    kind: str
    raw_id: str
    name: str
    display_name: str = ""
    sub_kind: str = ""


def _decode_kb_id(kb_id: str) -> tuple[str, str]:
    if ":" not in kb_id:
        raise HTTPException(400, "kb_id 必须是 doc:<name> / src:<id> 格式")
    head, _, raw = kb_id.partition(":")
    if head not in ("doc", "src"):
        raise HTTPException(400, "当前查询接口仅支持 doc:* / src:* 知识库")
    if not raw:
        raise HTTPException(400, "kb_id 缺少资源标识")
    return head, raw


def _source_ref_from_row(row: Dict[str, Any]) -> KnowledgeRef:
    raw_kind = str(row.get("kind") or "").lower()
    kind = "structured" if raw_kind in ("sql", "mongo", "es") else raw_kind
    return KnowledgeRef(
        kb_id=f"src:{row.get('id')}",
        kind=kind,
        raw_id=str(row.get("id")),
        name=str(row.get("name") or ""),
        display_name=str(row.get("display_name") or row.get("name") or ""),
        sub_kind=raw_kind,
    )


def resolve_ref(*, kb_id: Optional[str] = None, knowledge_base: Optional[str] = None) -> KnowledgeRef:
    """Resolve external kb_id / legacy knowledge_base name to a typed reference."""
    if kb_id:
        storage, raw = _decode_kb_id(kb_id.strip())
        if storage == "doc":
            from chayuan.server.db.repository.knowledge_base_repository import kb_exists

            if not kb_exists(raw):
                raise HTTPException(404, "knowledge base not found")
            return KnowledgeRef(kb_id=f"doc:{raw}", kind="document", raw_id=raw, name=raw, display_name=raw)

        from chayuan.server.db.repository.knowledge_source_repository import get_source

        src = get_source(int(raw))
        if src is None:
            raise HTTPException(404, "knowledge source not found")
        return _source_ref_from_row(src)

    name = (knowledge_base or "").strip()
    if not name:
        raise HTTPException(400, "knowledge_base 与 kb_id 至少提供一个")

    from chayuan.server.db.repository.knowledge_base_repository import kb_exists

    if kb_exists(name):
        return KnowledgeRef(kb_id=f"doc:{name}", kind="document", raw_id=name, name=name, display_name=name)

    from chayuan.server.db.repository.knowledge_source_repository import get_source_by_name

    src = get_source_by_name(name)
    if src is not None:
        return _source_ref_from_row(src)
    raise HTTPException(404, "knowledge base not found")

