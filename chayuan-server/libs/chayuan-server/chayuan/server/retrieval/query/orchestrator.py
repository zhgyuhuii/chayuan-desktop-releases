from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from chayuan.server.kb_query.authz import Subject
from chayuan.server.kb_query.schemas import QueryContent, SearchRequest, reject_external_embedding_fields
from chayuan.server.retrieval.query.adapters import (
    search_document,
    search_structured,
    search_vector,
)
from chayuan.server.retrieval.query.authz import assert_readable
from chayuan.server.retrieval.query.refs import KnowledgeRef, resolve_refs
from chayuan.server.retrieval.query.results import block_diagnostic, block_to_results, effective_options
from chayuan.server.retrieval.query.router import infer_intent

logger = logging.getLogger("chayuan.retrieval.query")


def _max_workers(default: int = 8) -> int:
    try:
        from chayuan.settings import Settings

        return int(getattr(Settings.basic_settings, "KB_QUERY_MAX_CONCURRENCY_PER_REQUEST", default))
    except Exception:
        return default


def _search_ref(ref: KnowledgeRef, content: QueryContent, req: SearchRequest, intent: str) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        if ref.kind == "document":
            block = search_document(ref, content.text, req.options)
        elif ref.kind == "structured":
            block = search_structured(ref, content.text, req.options)
        elif ref.kind == "vector":
            block = search_vector(ref, content.text, req.options)
        else:
            raise ValueError(f"暂不支持 {ref.kind} 类型查询")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        adapter = str(block.get("kind") or ref.kind)
        diagnostic = block_diagnostic(block, intent=intent, adapter=adapter, elapsed_ms=elapsed_ms)
        return {
            "kb_id": ref.kb_id,
            "name": ref.name,
            "display_name": ref.display_name or ref.name,
            "kind": ref.kind,
            "sub_kind": ref.sub_kind,
            "ok": bool(block.get("ok", True)),
            "results": block_to_results(block),
            "diagnostic": diagnostic,
            **({"error": {"code": "adapter_error", "message": str(block.get("error"))}} if block.get("error") else {}),
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "kb_id": ref.kb_id,
            "name": ref.name,
            "display_name": ref.display_name or ref.name,
            "kind": ref.kind,
            "sub_kind": ref.sub_kind,
            "ok": False,
            "results": [],
            "error": {"code": type(e).__name__, "message": str(e)},
            "diagnostic": {"route": ref.kind, "adapter": ref.kind, "intent": intent, "elapsed_ms": elapsed_ms},
        }


def _search_content(refs: List[KnowledgeRef], content: QueryContent, req: SearchRequest, index: int) -> Dict[str, Any]:
    started = time.perf_counter()
    content_id = content.content_id or f"item_{index + 1}"
    if not content.text.strip():
        return {
            "content_id": content_id,
            "ok": False,
            "query": content.text,
            "blocks": [],
            "results": [],
            "error": {"code": "ValueError", "message": "text 不能为空"},
            "diagnostic": {},
        }

    intent = infer_intent(content.text, refs, req.intent)
    blocks = [_search_ref(ref, content, req, intent) for ref in refs]
    results: List[Dict[str, Any]] = []
    for block in blocks:
        for hit in block.get("results") or []:
            if isinstance(hit, dict):
                results.append({
                    **hit,
                    "kb_id": block.get("kb_id"),
                    "kb_name": block.get("name"),
                    "kind": block.get("kind"),
                    "sub_kind": block.get("sub_kind"),
                })
    ok = any(block.get("ok") for block in blocks)
    diagnostic = {
        "intent": intent,
        "routes": [block.get("kind") for block in blocks],
        "effective_options": effective_options(req.options),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    return {
        "content_id": content_id,
        "ok": ok,
        "query": content.text,
        "blocks": blocks,
        "results": results,
        "diagnostic": diagnostic if req.options.return_diagnostics else {},
    }


def search(subject: Subject, req: SearchRequest, *, request_id: str = "", job_id: Optional[str] = None) -> Dict[str, Any]:
    reject_external_embedding_fields(req.model_dump())
    refs = resolve_refs(req.ku_ids, kb_id=req.kb_id, knowledge_base=req.knowledge_base)
    permissions = {ref.kb_id: assert_readable(subject, ref) for ref in refs}
    started = time.perf_counter()
    workers = max(1, min(_max_workers(), len(req.contents)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_search_content, refs, content, req, idx): idx
            for idx, content in enumerate(req.contents)
        }
        by_idx: Dict[int, Dict[str, Any]] = {}
        for future in as_completed(future_map):
            idx = future_map[future]
            by_idx[idx] = future.result()
    items = [by_idx[i] for i in sorted(by_idx)]
    success_count = sum(1 for item in items if item.get("ok"))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "kb_query.unified request_id=%s job_id=%s user_id=%s ku_ids=%s content_count=%s success=%s failed=%s elapsed_ms=%s",
        request_id,
        job_id or "",
        subject.user_id,
        [ref.kb_id for ref in refs],
        len(items),
        success_count,
        len(items) - success_count,
        elapsed_ms,
    )
    primary = refs[0]
    # 与 /knowledge_universe/ask/stream 接口对齐:跨 KB 综合一段答案 + [N] 引用。
    # 这样 chayuan-client(走 ask/stream)和 WPS 加载项(走 /api/v1/kb-query/search)
    # 拿到一致的展示形态:summary 在顶,源面板默认折叠。
    summary_text = ""
    summary_evidence_count = 0
    if items:
        try:
            from chayuan.server.retrieval.query.synthesis import (
                build_evidence_from_kb_query_items,
                synthesize_answer,
            )
            evidence = build_evidence_from_kb_query_items(items)
            summary_evidence_count = len(evidence)
            # 用第一个 content 的 query 做综合;同请求多 content 时综合答案聚焦首条问题
            primary_query = (req.contents[0].text if req.contents else "") or ""
            summary_text = synthesize_answer(primary_query, evidence) if evidence else ""
        except Exception as e:  # noqa: BLE001
            logger.info("kb_query.unified synthesize failed: %r", e)
            summary_text = ""

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "request_id": request_id,
            "job_id": job_id or "",
            "kb_id": primary.kb_id,
            "ku_ids": [ref.kb_id for ref in refs],
            "name": primary.name,
            "display_name": primary.display_name or primary.name,
            "kind": primary.kind if len(refs) == 1 else "multi_source",
            "sub_kind": primary.sub_kind if len(refs) == 1 else "",
            "permission": permissions.get(primary.kb_id, "reader"),
            "permissions": permissions,
            "items": items,
            "summary": {
                "text": summary_text,
                "evidence_count": summary_evidence_count,
                "ok": bool(summary_text),
            },
            "usage": {
                "content_count": len(items),
                "success_count": success_count,
                "failed_count": len(items) - success_count,
                "elapsed_ms": elapsed_ms,
            },
        },
    }
