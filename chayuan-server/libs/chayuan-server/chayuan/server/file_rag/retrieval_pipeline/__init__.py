"""检索流水线(retrieval pipeline)。

由若干可独立开关的 stage 串成,每个 stage 是上一段类型化输出的纯函数:

    QueryRewriter → Embedder(隐式,在 KB.search_docs 内) →
    Retriever(per-KB) → Fuser(RRF + dedup + sandwich) → Reranker → Snippet

所有公共契约:
- 入参:RetrievalRequest
- 出参:RetrievalResponse(envelope 包好的 hits + 改写 trace + stage timings)

任何 stage 失败都不阻塞整体:fuser 没拿到候选 → 返空命中;rewriter 超时 →
退回 passthrough;reranker 模型缺失 → 透传不重排。
"""
from chayuan.server.file_rag.retrieval_pipeline.fuser import (
    rrf_fuse,
    sandwich_reorder,
    dedup_by_chunk_key,
)

__all__ = ["rrf_fuse", "sandwich_reorder", "dedup_by_chunk_key"]
