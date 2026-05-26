"""跨多个 adapter 复用的小工具。"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, Iterator

from chayuan.server.data_mount.base import DocumentRecord


async def aiter_from_sync(it: Iterable[DocumentRecord]) -> AsyncIterator[DocumentRecord]:
    """把同步可迭代包装成 async — 主要给 langchain BaseLoader 类型用。"""
    for x in it:
        yield x


def langchain_doc_to_record(doc: Any, *, default_id: str | None = None) -> DocumentRecord:
    """langchain Document → 我们的 DocumentRecord."""
    text = getattr(doc, "page_content", None) or str(doc)
    md = dict(getattr(doc, "metadata", None) or {})
    return DocumentRecord(text=text, metadata=md, id=default_id or md.get("id") or md.get("doc_id"))


def truncate_text(s: str, n: int = 4000) -> str:
    if not isinstance(s, str):
        s = str(s)
    return s if len(s) <= n else s[:n] + "..."
