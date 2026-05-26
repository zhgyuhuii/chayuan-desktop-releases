from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException


_STRUCTURED_KINDS = {"sql", "mongo", "es"}
_VECTOR_KINDS = {"vector", "vs"}


@dataclass(frozen=True)
class KnowledgeRef:
    kb_id: str
    kind: str
    raw_id: str
    name: str
    display_name: str = ""
    sub_kind: str = ""

    @property
    def is_source(self) -> bool:
        return self.kb_id.startswith("src:")


def normalize_kind(raw_kind: str) -> str:
    kind = str(raw_kind or "").lower()
    if kind in _STRUCTURED_KINDS:
        return "structured"
    if kind in _VECTOR_KINDS:
        return "vector"
    return kind


def _decode(ref: str) -> tuple[str, str]:
    text = str(ref or "").strip()
    if ":" not in text:
        return "legacy", text
    head, _, raw = text.partition(":")
    if head not in ("doc", "src"):
        raise HTTPException(400, f"unsupported knowledge ref {ref!r}")
    if not raw:
        raise HTTPException(400, "knowledge ref 缺少资源标识")
    return head, raw


def _source_ref_from_row(row: Dict[str, Any]) -> KnowledgeRef:
    raw_kind = str(row.get("kind") or "").lower()
    kind = normalize_kind(raw_kind)
    sid = str(row.get("id") or "")
    return KnowledgeRef(
        kb_id=f"src:{sid}",
        kind=kind,
        raw_id=sid,
        name=str(row.get("name") or ""),
        display_name=str(row.get("display_name") or row.get("name") or ""),
        sub_kind=raw_kind,
    )


def resolve_ref(ref: str) -> KnowledgeRef:
    storage, raw = _decode(ref)
    if storage in ("legacy", "doc"):
        from chayuan.server.db.repository.knowledge_base_repository import kb_exists

        if storage == "legacy":
            if kb_exists(raw):
                return KnowledgeRef(kb_id=f"doc:{raw}", kind="document", raw_id=raw, name=raw, display_name=raw)
            from chayuan.server.db.repository.knowledge_source_repository import get_source_by_name

            src = get_source_by_name(raw)
            if src is not None:
                return _source_ref_from_row(src)
            raise HTTPException(404, "knowledge base not found")

        if not kb_exists(raw):
            raise HTTPException(404, "knowledge base not found")
        return KnowledgeRef(kb_id=f"doc:{raw}", kind="document", raw_id=raw, name=raw, display_name=raw)

    from chayuan.server.db.repository.knowledge_source_repository import get_source

    src = get_source(int(raw))
    if src is None:
        raise HTTPException(404, "knowledge source not found")
    return _source_ref_from_row(src)


def resolve_refs(refs: Iterable[str], *, kb_id: Optional[str] = None, knowledge_base: Optional[str] = None) -> List[KnowledgeRef]:
    raw_list = list(refs or [])
    ordered = [str(x).strip() for x in raw_list if str(x or "").strip()]
    if not ordered and kb_id:
        ordered = [str(kb_id).strip()] if str(kb_id or "").strip() else []
    if not ordered and knowledge_base:
        ordered = [str(knowledge_base).strip()] if str(knowledge_base or "").strip() else []
    if not ordered:
        # 把实际看到的字段一起回给客户端,排查"前端为何送了空数组"
        received = {
            "ku_ids": raw_list,
            "kb_id": kb_id,
            "knowledge_base": knowledge_base,
        }
        raise HTTPException(
            400,
            f"ku_ids / kb_id / knowledge_base 至少提供一个 (received={received})",
        )

    out: List[KnowledgeRef] = []
    seen: set[str] = set()
    for item in ordered:
        ref = resolve_ref(item)
        if ref.kb_id in seen:
            continue
        seen.add(ref.kb_id)
        out.append(ref)
    return out
