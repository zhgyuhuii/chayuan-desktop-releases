"""LangChain 1.x: BaseRetriever uses invoke() instead of get_relevant_documents()."""

from __future__ import annotations

from typing import Any, List

from langchain_core.documents import Document


def retriever_get_documents(retriever: Any, query: str) -> List[Document]:
    get_fn = getattr(retriever, "get_relevant_documents", None)
    if callable(get_fn):
        return get_fn(query)
    invoke = getattr(retriever, "invoke", None)
    if callable(invoke):
        return invoke(query)
    raise TypeError(
        f"Retriever {type(retriever)!r} has neither get_relevant_documents nor invoke"
    )
