"""知识源 (Knowledge Source) 数据源 —— ES / Mongo / SQL / 外部向量库已有 connector。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.knowledge_source")


class KnowledgeSourceAdapter:
    type_id = "knowledge_source"
    label = "知识源"
    description = "已配置的知识源(ES / Mongo / SQL / 外部向量库 / 图像);复用 connector"
    icon = "boxes"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "source_id", "label": "知识源 ID", "type": "int", "required": True},
            {"name": "query", "label": "过滤查询(可选)", "type": "string", "default": ""},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        sid = int(spec.options.get("source_id") or 0)
        if not sid:
            return ProbeResult(status="error", message="缺 source_id")
        try:
            from chayuan.server.db.repository.knowledge_source_repository import (
                connection_spec_for_source, get_source,
            )
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"知识源模块不可用: {e}")
        src = get_source(sid)
        if src is None:
            return ProbeResult(status="error", message=f"知识源 {sid} 不存在")
        kind = src.get("kind")
        return ProbeResult(
            status="ok",
            message=f"知识源 {sid} ({kind}) OK",
            extra={"kind": kind, "name": src.get("name")},
        )

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 500)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        sid = int(spec.options.get("source_id") or 0)
        query = (spec.options.get("query") or "").strip()
        if not sid:
            return []
        try:
            from chayuan.server.knowledge_source.orchestrator import (
                build_connector_for_source,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("orchestrator import failed: %s", e)
            return []
        try:
            conn = build_connector_for_source(sid)
            if conn is None:
                return []
            results = conn.search(query or "*", top_k=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("ks connector search failed: %s", e)
            return []
        out: List[DocumentRecord] = []
        for r in results or []:
            if isinstance(r, dict):
                out.append(DocumentRecord(
                    text=str(r.get("page_content") or r.get("text") or ""),
                    metadata={**(r.get("metadata") or {}), "source_id": sid},
                    id=str(r.get("id") or "") or None,
                ))
            else:
                out.append(DocumentRecord(
                    text=str(getattr(r, "page_content", "")),
                    metadata={**dict(getattr(r, "metadata", None) or {}), "source_id": sid},
                ))
        return out


ADAPTER = KnowledgeSourceAdapter
