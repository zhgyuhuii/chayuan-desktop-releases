"""MultiSource 模式：并行多源检索（向量 KB / SQL / Mongo / ES / 多 KB 聚合）。"""
from __future__ import annotations

import time
from typing import Any, Dict

from fastapi.concurrency import run_in_threadpool

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class MultiSourceHandler(BaseModeHandler):
    mode = "multi_source"
    needs_retrieval = True

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        from chayuan.server.db.repository.knowledge_source_repository import (
            list_accessible_source_ids, list_sources,
        )
        from chayuan.server.knowledge_source.orchestrator import multi_search_sync

        req = state["request"]
        t0 = time.time()
        all_rows = await run_in_threadpool(list_sources)

        if req.select_all_sources:
            if req.user_role == "admin" or req.user_id is None:
                sources = all_rows
            else:
                accessible = set(
                    await run_in_threadpool(list_accessible_source_ids, req.user_id)
                )
                sources = [
                    r for r in all_rows
                    if int(r["id"]) in accessible or r.get("visibility") == "public"
                ]
        elif req.source_ids:
            ids = {int(x) for x in req.source_ids}
            sources = [r for r in all_rows if int(r["id"]) in ids]
        elif req.kb_names:
            kb_set = set(req.kb_names)
            sources = [
                r for r in all_rows
                if r.get("kind") == "vector" and r.get("name") in kb_set
            ]
        else:
            sources = []

        agg_chunks, meta = await multi_search_sync(
            query=req.query, sources=sources, top_k=int(req.top_k or 5),
            per_source_timeout=30.0, llm_model=req.model or None,
            history=req.history,
            user_id=req.user_id, user_role=req.user_role,
        )
        return {
            "retrieved_chunks": [c.to_wire() for c in agg_chunks],
            "retrieved_sources_meta": meta,
            "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
        }


register_handler(MultiSourceHandler())
