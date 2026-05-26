"""Notion 数据源 —— 走 langchain NotionDBLoader / NotionDirectoryLoader。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record

logger = logging.getLogger("chayuan.data_mount.sources.notion")


class NotionSource:
    type_id = "notion"
    label = "Notion"
    description = "Notion 数据库或目录导出 ZIP"
    icon = "file-text"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "mode", "label": "模式", "type": "select",
             "options": [
                 {"value": "database", "label": "Notion DB(API token)"},
                 {"value": "directory", "label": "本地导出目录"},
             ], "default": "database"},
            {"name": "integration_token", "label": "Integration Token",
             "type": "password", "default": ""},
            {"name": "database_id", "label": "Database ID", "type": "string", "default": ""},
            {"name": "directory_path", "label": "目录路径(模式=directory 时)",
             "type": "string", "default": ""},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        opts = spec.options
        mode = opts.get("mode") or "database"
        if mode == "database":
            if not (opts.get("integration_token") and opts.get("database_id")):
                return ProbeResult(status="error", message="缺 integration_token / database_id")
            return ProbeResult(status="ok", message="配置完整(实际连接在 load 时验证)")
        if not opts.get("directory_path"):
            return ProbeResult(status="error", message="缺 directory_path")
        from pathlib import Path
        if not Path(opts["directory_path"]).exists():
            return ProbeResult(status="error", message="目录不存在")
        return ProbeResult(status="ok", message="目录存在")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 500)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        opts = spec.options
        mode = opts.get("mode") or "database"
        try:
            if mode == "database":
                from langchain_community.document_loaders import NotionDBLoader
                loader = NotionDBLoader(
                    integration_token=opts["integration_token"],
                    database_id=opts["database_id"],
                )
            else:
                from langchain_community.document_loaders import NotionDirectoryLoader
                loader = NotionDirectoryLoader(opts["directory_path"])
            docs = loader.load() or []
        except ImportError:
            logger.warning("langchain_community.document_loaders Notion 不可用")
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("Notion load 失败: %s", e)
            return []
        return [langchain_doc_to_record(d) for d in docs[:limit]]


ADAPTER = NotionSource
