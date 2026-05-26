"""KB 数据源 —— 直接从知识库切片做挂载。

适合场景:
* 把"法务库"作为 corpus_pending 复制到另一个 KB(跨库扩展)
* 把"FAQ 库"前 100 条作 fewshot 注入
* 把"安全规则库"作 safety 注入

不做的事:
* 不真的"读 KB 全部 chunk":用 hybrid_search_docs 拿 top_k 模拟
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.kb")


class KbSource:
    type_id = "kb"
    label = "知识库"
    description = "从已建好的本地知识库取切片"
    icon = "library"
    capabilities = ["corpus", "context", "fewshot"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "kb_name", "label": "知识库名称", "type": "string", "required": True,
             "help": "选 chayuan-server 内已建好的 KB"},
            {"name": "query", "label": "过滤检索词", "type": "string", "required": False,
             "default": "", "help": "留空 = 拉前 N 条文档;非空 = hybrid_search 取 top_k"},
            {"name": "top_k", "label": "条数上限", "type": "int", "required": False, "default": 200},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        kb_name = (spec.options.get("kb_name") or "").strip()
        if not kb_name:
            return ProbeResult(status="error", message="缺 kb_name")
        try:
            from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory  # type: ignore

            svc = KBServiceFactory.get_service_by_name(kb_name)
            if svc is None:
                return ProbeResult(status="error", message=f"KB '{kb_name}' 不存在")
            count = 0
            try:
                count = int(svc.count_files() or 0) if hasattr(svc, "count_files") else 0
            except Exception:  # noqa: BLE001
                pass
            return ProbeResult(status="ok", message=f"KB '{kb_name}' 可访问",
                               counted=count, extra={"kb_name": kb_name})
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"KB 探活失败: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = list(self._fetch(spec, limit=n))
        return SampleResult(
            items=items,
            total_estimate=None,
            fields=analyze_schema(items),
        )

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        limit = int(spec.options.get("top_k") or spec.max_items or 200)
        for rec in self._fetch(spec, limit=limit):
            yield rec

    # ---- internal ------------------------------------------------------

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        kb_name = (spec.options.get("kb_name") or "").strip()
        query = (spec.options.get("query") or "").strip()
        if not kb_name:
            return []
        try:
            from chayuan.server.file_rag.hybrid_service import hybrid_search_docs

            results = hybrid_search_docs(
                query=query or "*",
                knowledge_base_name=kb_name,
                top_k=limit,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("hybrid_search_docs failed for %s: %s", kb_name, e)
            return []
        out: List[DocumentRecord] = []
        for r in results or []:
            text = ""
            md: Dict[str, Any] = {"kb_name": kb_name}
            doc_id = ""
            if isinstance(r, dict):
                text = str(r.get("page_content") or r.get("text") or "")
                md.update(r.get("metadata") or {})
                doc_id = md.get("doc_id") or md.get("id") or ""
            else:
                text = str(getattr(r, "page_content", "") or r)
                md.update(dict(getattr(r, "metadata", None) or {}))
                doc_id = md.get("doc_id") or md.get("id") or ""
            md["source"] = f"kb:{kb_name}"
            out.append(DocumentRecord(text=text, metadata=md, id=str(doc_id) or None))
        return out


ADAPTER = KbSource
