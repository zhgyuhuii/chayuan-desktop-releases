from __future__ import annotations

from typing import Any, Dict

from chayuan.server.kb_query.citations import document_hit_to_result
from chayuan.server.retrieval.query.refs import KnowledgeRef


def search_document(ref: KnowledgeRef, query: str, options: Any) -> Dict[str, Any]:
    from chayuan.settings import Settings
    from chayuan.server.file_rag.result_envelope import to_wire_list
    from chayuan.server.knowledge_base.kb_doc_api import search_docs
    from chayuan.server.kb_query.index_status import document_index_status

    idx = document_index_status(ref.name)
    if idx["status"] in ("empty", "indexing", "failed"):
        return {
            "ku_id": ref.kb_id,
            "kind": "document",
            "ok": True,
            "results": [],
            "diagnostic": {
                "retrieval_model_policy": "kb_bound_embedding",
                "index_status": idx["status"],
                "index_status_detail": idx,
                "warning": "knowledge base index is not ready",
            },
        }

    score_threshold = (
        options.score_threshold
        if getattr(options, "score_threshold", None) is not None
        else Settings.kb_settings.SCORE_THRESHOLD
    )
    top_k = int(getattr(options, "effective_top_k", options.top_k) or 5)
    docs = search_docs(
        query=query,
        knowledge_base_name=ref.name,
        top_k=top_k,
        score_threshold=score_threshold,
        use_hybrid=getattr(options, "use_hybrid", None),
        use_rerank=getattr(options, "use_rerank", None),
    )
    wire_hits = to_wire_list(
        docs or [],
        query=query,
        kb_name=ref.name,
        embed_model_used="",
        retrieval_path="vector",
    )
    return {
        "ku_id": ref.kb_id,
        "kind": "document",
        "ok": True,
        "results": [document_hit_to_result(hit, kb_id=ref.kb_id, kb_name=ref.name) for hit in wire_hits],
        "diagnostic": {
            "retrieval_model_policy": "kb_bound_embedding",
            "index_status": idx["status"],
            "index_status_detail": idx,
            "warning": "knowledge base index may be stale" if idx["status"] == "stale" else "",
        },
    }
