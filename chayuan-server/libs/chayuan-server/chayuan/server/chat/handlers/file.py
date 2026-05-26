"""File 模式：临时知识库（用户上传后向量化）检索。"""
from __future__ import annotations

import time
from typing import Any, Dict

from fastapi.concurrency import run_in_threadpool

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class FileHandler(BaseModeHandler):
    mode = "file"
    needs_retrieval = True

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        from chayuan.server.chat.graph.nodes import _doc_to_chunk
        from chayuan.server.knowledge_base.kb_doc_api import search_temp_docs

        req = state["request"]
        if not req.file_chat_id:
            return {"retrieved_chunks": [], "retrieved_sources_meta": [],
                    "retrieval_elapsed_ms": 0}

        t0 = time.time()
        docs = await run_in_threadpool(
            search_temp_docs,
            knowledge_base_name=req.file_chat_id,
            query=req.query,
            top_k=int(req.top_k or 5),
            score_threshold=float(req.score_threshold),
        )
        chunks = [_doc_to_chunk(d, source=f"file:{req.file_chat_id}") for d in (docs or [])]
        return {
            "retrieved_chunks": chunks,
            "retrieved_sources_meta": [],
            "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
        }


register_handler(FileHandler())
