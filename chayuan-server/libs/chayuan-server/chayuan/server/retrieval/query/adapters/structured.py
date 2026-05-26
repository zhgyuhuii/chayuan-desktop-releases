from __future__ import annotations

from typing import Any, Dict

from chayuan.server.kb_query.citations import structured_block_to_results
from chayuan.server.retrieval.query.adapters.structured_tools import (
    aggregate_hint,
    link_tables,
    sql_matches_intent,
    validate_readonly_sql,
)
from chayuan.server.retrieval.query.refs import KnowledgeRef


def _structured_error_message(raw: Any) -> str:
    text = str(raw or "").strip()
    if ("qwen" in text and ("not found" in text or "404" in text or "NotFound" in text)) or (
        "model" in text and "not found" in text
    ):
        return "结构化检索模型不可用，请检查 Ollama 模型配置。"
    return text or "structured search failed"


def search_structured(ref: KnowledgeRef, query: str, options: Any) -> Dict[str, Any]:
    from chayuan.server.api_server.knowledge_universe_routes import _process_one_ku

    structured_scopes = dict(getattr(options, "structured_scopes", {}) or {})
    hint = aggregate_hint(query)
    scope_tables = list(structured_scopes.get(ref.kb_id) or structured_scopes.get(ref.raw_id) or [])
    linked_tables = link_tables(query, scope_tables)
    top_k = int(getattr(options, "effective_top_k", options.top_k) or 5)
    block = _process_one_ku(
        ref.kb_id,
        query,
        top_k,
        use_hybrid=getattr(options, "use_hybrid", None),
        use_rerank=getattr(options, "use_rerank", None),
        rewrite_strategy=getattr(options, "rewrite_strategy", "auto"),
        model=getattr(options, "model", None),
        structured_scopes=structured_scopes,
    )
    if not block.get("ok"):
        message = _structured_error_message(block.get("error") or block.get("diagnostic"))
        # 把错误也以 hit 形式回出去 — 否则 chat 流的 LLM 拿不到任何上下文,只能凭空回答,
        # 用户既看不到结果也看不到原因。这条 hit 不计分(score=0),但 text 会进 merged 块。
        return {
            "ku_id": ref.kb_id,
            "kind": "structured",
            "ok": False,
            "results": [{
                "hit_id": f"{ref.kb_id}::error",
                "source_type": "structured_error",
                "score": 0.0,
                "text": f"[结构化数据源 {ref.display_name or ref.name or ref.kb_id} 查询失败] {message}",
                "retrieval_path": "error",
                "metadata": {"error": message, "kind": "structured"},
                "citation": {
                    "kb_id": ref.kb_id,
                    "source_name": ref.name,
                    "error": message,
                },
            }],
            "error": message,
            "diagnostic": {
                "route": "structured",
                "error": message,
                "schema_linked_tables": linked_tables,
                **hint,
            },
        }

    results = structured_block_to_results(
        block,
        kb_id=ref.kb_id,
        source_id=int(ref.raw_id),
        source_name=ref.name,
    )
    sql = block.get("sql") or ""
    validation = validate_readonly_sql(sql) if sql else {"ok": False, "reason": "no_sql_captured"}
    diagnostic = {
        "route": "structured",
        "retrieval_path": "text2sql",
        "index_status": "ready",
        "sql": sql,
        "sql_validation": validation,
        "sql_matches_intent": sql_matches_intent(sql, hint),
        "row_count": len(block.get("rows") or []),
        "schema_linked_tables": linked_tables,
        **hint,
    }
    if isinstance(block.get("scope"), dict):
        diagnostic["scope"] = block.get("scope")
    return {
        **block,
        "ku_id": ref.kb_id,
        "kind": "structured",
        "ok": True,
        "results": results,
        "diagnostic": diagnostic,
    }
