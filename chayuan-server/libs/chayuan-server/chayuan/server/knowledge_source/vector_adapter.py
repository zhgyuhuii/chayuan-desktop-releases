"""把现有向量库 KB 适配为 BaseConnector 的一层薄壳。

这样 orchestrator 在并行检索时可以对 vector / sql / mongo / es 一视同仁。
不重新实现 embedding + 相似度，内部直接调 `search_docs`。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Tuple
from urllib.parse import urlencode

from chayuan.server.knowledge_source.base import BaseConnector, ConnectionSpec
from chayuan.server.knowledge_source.types import (
    Citation,
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
)

logger = logging.getLogger("chayuan.knowledge_source.vector")


class VectorKbConnector(BaseConnector):
    """spec.dialect=='vector'；spec.database 承载 kb_name。"""

    dialects = ("vector",)
    source_kind = SourceKind.VECTOR.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self.kb_name = spec.database

    def test_connection(self) -> Tuple[bool, str]:
        try:
            from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
            kb = KBServiceFactory.get_service_by_name(self.kb_name)
            if kb is None:
                return False, f"知识库 {self.kb_name!r} 不存在"
            ok, msg = kb.check_embed_model()
            return bool(ok), msg or "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"向量库不可用：{type(e).__name__}: {e}"

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        # 向量源没有 schema 概念；返回空快照（tables 留空）
        return SchemaSnapshot(
            source_id=self.source_id,
            source_kind=self.source_kind,
            dialect="vector",
            tables=[],
        )

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: NLQuery) -> List[RetrievalChunk]:
        from chayuan.server.knowledge_base.kb_doc_api import search_docs
        from chayuan.settings import Settings

        try:
            docs = search_docs(
                query=query.query,
                knowledge_base_name=self.kb_name,
                top_k=int(query.top_k or Settings.kb_settings.VECTOR_SEARCH_TOP_K),
                score_threshold=float(Settings.kb_settings.SCORE_THRESHOLD),
                file_name="",
                metadata={},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vector search 失败 kb=%s: %r", self.kb_name, e)
            return [RetrievalChunk(
                content=f"向量库 {self.kb_name!r} 检索失败：{e}",
                citation=Citation(
                    title=self.kb_name or "vector",
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                ),
                score=0.0,
                source_id=self.source_id,
                source_kind=self.source_kind,
            )]

        chunks: List[RetrievalChunk] = []
        for i, d in enumerate(docs or []):
            if hasattr(d, "page_content"):
                content = d.page_content or ""
                meta = getattr(d, "metadata", {}) or {}
                score = float(getattr(d, "score", 1.0) or 1.0)
            elif isinstance(d, dict):
                content = d.get("page_content") or ""
                meta = d.get("metadata") or {}
                score = float(d.get("score", 1.0) or 1.0)
            else:
                continue
            file_name = str(meta.get("source") or meta.get("file_name") or "unknown")
            # 下载链接：沿用现有 /knowledge_base/download_doc 端点
            qs = urlencode({
                "knowledge_base_name": self.kb_name,
                "file_name": os.path.basename(file_name),
                "preview": "false",
            })
            download_url = f"/knowledge_base/download_doc?{qs}"
            preview_qs = urlencode({
                "knowledge_base_name": self.kb_name,
                "file_name": os.path.basename(file_name),
                "preview": "true",
            })
            preview_url = f"/knowledge_base/download_doc?{preview_qs}"
            chunks.append(RetrievalChunk(
                content=self._trunc(content, 2000),
                citation=Citation(
                    title=os.path.basename(file_name) or self.kb_name,
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    download_url=download_url,
                    preview_url=preview_url,
                    meta={"kb_name": self.kb_name, **{k: str(v)[:200] for k, v in meta.items()}},
                ),
                score=score,
                source_id=self.source_id,
                source_kind=self.source_kind,
            ))
        return chunks
