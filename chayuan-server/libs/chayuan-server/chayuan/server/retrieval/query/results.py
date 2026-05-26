from __future__ import annotations

from typing import Any, Dict, List


def effective_options(options: Any) -> Dict[str, Any]:
    return {
        "top_k": int(getattr(options, "effective_top_k", getattr(options, "top_k", 5)) or 5),
        "score_threshold": getattr(options, "score_threshold", None),
        "use_hybrid": getattr(options, "use_hybrid", None),
        "use_rerank": getattr(options, "use_rerank", None),
        "rewrite_strategy": getattr(options, "rewrite_strategy", "auto"),
        "fusion": getattr(options, "effective_fusion", "rrf"),
        "structured_scopes": dict(getattr(options, "structured_scopes", {}) or {}),
        "vector_scopes": dict(getattr(options, "vector_scopes", {}) or {}),
        "timeout_ms": int(getattr(options, "timeout_ms", 30000) or 30000),
    }


def block_to_results(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    hits = block.get("results") if isinstance(block.get("results"), list) else block.get("hits")
    if isinstance(hits, list):
        return [h for h in hits if isinstance(h, dict)]
    return []


def block_diagnostic(block: Dict[str, Any], *, intent: str, adapter: str, elapsed_ms: int) -> Dict[str, Any]:
    diagnostic = block.get("diagnostic") if isinstance(block.get("diagnostic"), dict) else {}
    raw_error = block.get("error") or (block.get("diagnostic") if isinstance(block.get("diagnostic"), str) else "")
    out = {
        **diagnostic,
        "route": adapter,
        "adapter": adapter,
        "intent": intent,
        "retrieval_path": block.get("retrieval_path") or diagnostic.get("retrieval_path") or adapter,
        "sql": block.get("sql") or diagnostic.get("sql") or "",
        "tables": block.get("scope", {}).get("tables", []) if isinstance(block.get("scope"), dict) else [],
        "collections": block.get("scope", {}).get("collections", []) if isinstance(block.get("scope"), dict) else [],
        "row_count": len(block.get("rows") or []),
        "error": str(raw_error or ""),
        "elapsed_ms": elapsed_ms,
    }
    return out
