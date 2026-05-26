from __future__ import annotations

from typing import Any, Dict, List

from chayuan.server.retrieval.query.refs import KnowledgeRef


def _hit_to_result(hit: Dict[str, Any], *, ref: KnowledgeRef, idx: int) -> Dict[str, Any]:
    metadata = dict(hit.get("metadata") or {})
    collection = str(metadata.get("collection") or metadata.get("collection_name") or metadata.get("index") or "")
    vector_id = metadata.get("id") or metadata.get("vector_id") or metadata.get("pk") or idx
    return {
        "hit_id": f"{ref.raw_id}:{vector_id}",
        "source_type": "vector",
        "score": hit.get("score", 0.0),
        "text": hit.get("content") or hit.get("text") or "",
        "retrieval_path": "vector",
        "metadata": metadata,
        "citation": {
            "kb_id": ref.kb_id,
            "source_id": int(ref.raw_id),
            "source_name": ref.name,
            "collection": collection,
            "vector_id": vector_id,
            "metadata": metadata,
        },
    }


def search_vector(ref: KnowledgeRef, query: str, options: Any) -> Dict[str, Any]:
    from chayuan.server.api_server.knowledge_universe_routes import _process_one_ku

    vector_scopes = dict(getattr(options, "vector_scopes", {}) or {})
    top_k = int(getattr(options, "effective_top_k", options.top_k) or 5)
    block = _process_one_ku(
        ref.kb_id,
        query,
        top_k,
        use_hybrid=getattr(options, "use_hybrid", None),
        use_rerank=getattr(options, "use_rerank", None),
        rewrite_strategy=getattr(options, "rewrite_strategy", "auto"),
        vector_scopes=vector_scopes,
    )
    hits = block.get("hits") if isinstance(block.get("hits"), list) else block.get("results")
    results: List[Dict[str, Any]] = []
    for idx, hit in enumerate(hits or []):
        if isinstance(hit, dict):
            results.append(_hit_to_result(hit, ref=ref, idx=idx))
    if not block.get("ok"):
        return {
            "ku_id": ref.kb_id,
            "kind": "vector",
            "ok": False,
            "results": [],
            "error": block.get("error") or "vector search failed",
            "diagnostic": {"route": "vector", "error": block.get("error") or ""},
        }
    diagnostic: Dict[str, Any] = {
        "route": "vector",
        "retrieval_path": "vector",
        "hit_count": len(results),
    }
    if isinstance(block.get("scope"), dict):
        diagnostic["scope"] = block.get("scope")
        diagnostic["collections"] = block.get("scope", {}).get("collections", [])
    return {
        **block,
        "ku_id": ref.kb_id,
        "kind": "vector",
        "ok": True,
        "results": results,
        "diagnostic": diagnostic,
    }
