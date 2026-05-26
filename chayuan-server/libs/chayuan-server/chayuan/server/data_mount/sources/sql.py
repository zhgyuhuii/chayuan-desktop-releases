"""SQL 数据源 —— 走 langchain SQLDatabaseLoader (支持 postgres / mysql / sqlite)。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.sql")


class SqlSource:
    type_id = "sql"
    label = "SQL 数据库"
    description = "Postgres / MySQL / SQLite — 自定义 query 拉行,每行作一条 record"
    icon = "database"
    capabilities = ["corpus", "context", "fewshot"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "url", "label": "SQLAlchemy URL", "type": "password", "required": True,
             "help": "如 postgresql://user:pass@host:5432/db"},
            {"name": "query", "label": "SELECT 语句", "type": "string", "required": True,
             "help": "返回的字段会进 metadata;指定 ``text_column`` 字段名以选定文本主体"},
            {"name": "text_column", "label": "文本字段名", "type": "string", "default": ""},
            {"name": "id_column", "label": "ID 字段名", "type": "string", "default": ""},
        ]}

    def _engine(self, url: str):
        from sqlalchemy import create_engine

        return create_engine(url, pool_pre_ping=True, pool_recycle=600)

    def probe(self, spec: SourceSpec) -> ProbeResult:
        url = (spec.options.get("url") or "").strip()
        if not url:
            return ProbeResult(status="error", message="缺 URL")
        try:
            from sqlalchemy import text  # type: ignore

            eng = self._engine(url)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return ProbeResult(status="ok", message="数据库连接 OK")
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"连接失败: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 1000)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        url = (spec.options.get("url") or "").strip()
        query = (spec.options.get("query") or "").strip()
        text_col = (spec.options.get("text_column") or "").strip()
        id_col = (spec.options.get("id_column") or "").strip()
        if not url or not query:
            return []
        try:
            from sqlalchemy import text  # type: ignore

            eng = self._engine(url)
            # 简单 LIMIT 注入: 若 query 里没有 LIMIT 自动加
            q = query
            if "limit" not in query.lower():
                q = f"{query.rstrip(';')} LIMIT {int(limit)}"
            out: List[DocumentRecord] = []
            with eng.connect() as conn:
                rs = conn.execute(text(q))
                for row in rs.mappings():
                    md = {k: v for k, v in row.items()}
                    if text_col and text_col in md:
                        body = str(md.get(text_col) or "")
                    else:
                        # 兜底: 把所有列拼成 "k: v\n..."
                        body = "\n".join(f"{k}: {v}" for k, v in md.items())
                    rid = None
                    if id_col and id_col in md:
                        rid = str(md.get(id_col) or "") or None
                    out.append(DocumentRecord(text=body, metadata=md, id=rid))
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("SqlSource fetch 失败: %s", e)
            return []


ADAPTER = SqlSource
