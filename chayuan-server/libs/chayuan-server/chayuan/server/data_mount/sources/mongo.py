"""MongoDB 数据源 —— 走 langchain MongodbLoader / pymongo。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.mongo")


class MongoSource:
    type_id = "mongo"
    label = "MongoDB"
    description = "MongoDB 集合;给定 query 过滤 + text_field 选文本主体"
    icon = "database"
    capabilities = ["corpus", "context", "fewshot"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "uri", "label": "Mongo URI", "type": "password", "required": True,
             "help": "如 mongodb://user:pass@host:27017"},
            {"name": "db", "label": "Database", "type": "string", "required": True},
            {"name": "collection", "label": "Collection", "type": "string", "required": True},
            {"name": "filter_json", "label": "Filter (JSON)", "type": "string", "default": "{}"},
            {"name": "text_field", "label": "文本字段名", "type": "string", "default": "content"},
            {"name": "id_field", "label": "ID 字段名", "type": "string", "default": "_id"},
        ]}

    def _client(self, uri: str):
        from pymongo import MongoClient  # type: ignore

        return MongoClient(uri, serverSelectionTimeoutMS=4000)

    def probe(self, spec: SourceSpec) -> ProbeResult:
        opts = spec.options
        uri = (opts.get("uri") or "").strip()
        db = (opts.get("db") or "").strip()
        coll = (opts.get("collection") or "").strip()
        if not (uri and db and coll):
            return ProbeResult(status="error", message="缺 uri / db / collection")
        try:
            cli = self._client(uri)
            count = cli[db][coll].estimated_document_count()
            return ProbeResult(status="ok", message=f"集合 {db}.{coll} 估算 {count} 条",
                               counted=int(count))
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"Mongo 连接失败: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 1000)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        import json
        opts = spec.options
        try:
            cli = self._client(opts["uri"])
            coll = cli[opts["db"]][opts["collection"]]
            try:
                f = json.loads(opts.get("filter_json") or "{}")
            except json.JSONDecodeError:
                f = {}
            text_field = opts.get("text_field") or "content"
            id_field = opts.get("id_field") or "_id"
            cursor = coll.find(f).limit(limit)
            out: List[DocumentRecord] = []
            for doc in cursor:
                rid = str(doc.get(id_field, "")) if doc.get(id_field) else None
                # 把 ObjectId / datetime 这类不可 JSON 化的转成 str
                md = {k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v)
                      for k, v in doc.items() if k != text_field}
                out.append(DocumentRecord(
                    text=str(doc.get(text_field) or ""),
                    metadata=md,
                    id=rid,
                ))
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("Mongo fetch 失败: %s", e)
            return []


ADAPTER = MongoSource
