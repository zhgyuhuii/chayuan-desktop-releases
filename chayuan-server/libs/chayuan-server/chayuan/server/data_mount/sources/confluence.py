"""Confluence 数据源 —— 走 langchain ConfluenceLoader。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record

logger = logging.getLogger("chayuan.data_mount.sources.confluence")


class ConfluenceSource:
    type_id = "confluence"
    label = "Confluence"
    description = "Atlassian Confluence space (api_key / token)"
    icon = "book-open"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "url", "label": "Confluence URL", "type": "string", "required": True,
             "help": "如 https://your-domain.atlassian.net/wiki"},
            {"name": "username", "label": "用户名 / 邮箱", "type": "string", "default": ""},
            {"name": "api_key", "label": "API Key / Token", "type": "password", "required": True},
            {"name": "space_key", "label": "Space Key", "type": "string", "required": True},
            {"name": "include_attachments", "label": "包含附件文本", "type": "bool", "default": False},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        opts = spec.options
        if not (opts.get("url") and opts.get("api_key") and opts.get("space_key")):
            return ProbeResult(status="error", message="缺 url / api_key / space_key")
        return ProbeResult(status="ok", message="配置完整(实际连接在 load 时验证)")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 500)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        try:
            from langchain_community.document_loaders import ConfluenceLoader
        except ImportError:
            logger.warning("ConfluenceLoader 不可用 (pip install atlassian-python-api)")
            return []
        opts = spec.options
        try:
            loader = ConfluenceLoader(
                url=opts["url"],
                username=opts.get("username") or None,
                api_key=opts["api_key"],
            )
            docs = loader.load(
                space_key=opts["space_key"],
                limit=limit,
                include_attachments=bool(opts.get("include_attachments")),
            ) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("Confluence load 失败: %s", e)
            return []
        return [langchain_doc_to_record(d) for d in docs[:limit]]


ADAPTER = ConfluenceSource
