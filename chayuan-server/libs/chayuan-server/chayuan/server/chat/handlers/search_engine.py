"""SearchEngine 模式：通过工具栏的 search_engine 子工具调用搜索引擎。"""
from __future__ import annotations

import time
from typing import Any, Dict

from fastapi.concurrency import run_in_threadpool

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class SearchEngineHandler(BaseModeHandler):
    mode = "search_engine"
    needs_retrieval = True

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        from chayuan.server.agent.tools_factory.search_internet import search_engine
        from chayuan.server.chat.graph.nodes import _doc_to_chunk

        req = state["request"]
        if not req.search_engine:
            return {"retrieved_chunks": [], "retrieved_sources_meta": [],
                    "retrieval_elapsed_ms": 0}

        t0 = time.time()
        result = await run_in_threadpool(
            search_engine, req.query, int(req.top_k or 3), req.search_engine,
        )
        raw_docs = [x.dict() for x in result.get("docs", [])] if result else []
        chunks = [_doc_to_chunk(d, source=f"search:{req.search_engine}") for d in raw_docs]
        return {
            "retrieved_chunks": chunks,
            "retrieved_sources_meta": [],
            "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
        }


register_handler(SearchEngineHandler())
