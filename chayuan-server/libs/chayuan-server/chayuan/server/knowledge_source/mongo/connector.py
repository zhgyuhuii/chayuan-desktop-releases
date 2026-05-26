"""MongoDB Connector。基于 pymongo。

- test_connection: 走 `admin.command('ping')`，默认 5 秒超时。
- introspect: 枚举 collection，用 aggregate + $sample 做字段探测；3 条采样。
- search: Text2Mongo → 校验 → 只读执行 find/aggregate，结果序列化为 markdown。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.knowledge_source.base import (
    BaseConnector,
    ConnectionSpec,
    ConnectorError,
)
from chayuan.server.knowledge_source.mongo.text2mongo import (
    generate_query,
    validate_query,
)
from chayuan.server.knowledge_source.types import (
    Citation,
    ColumnInfo,
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
    TableInfo,
)

logger = logging.getLogger("chayuan.knowledge_source.mongo")


class MongoConnector(BaseConnector):
    dialects = ("mongo", "mongodb")
    source_kind = SourceKind.MONGO.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._client = None

    # ----- client -----

    def _build_uri(self) -> str:
        # 允许用户在 options.uri 里直接塞完整 URI（SRV / 复杂选项）
        uri = (self.spec.options or {}).get("uri")
        if uri:
            return str(uri)
        from urllib.parse import quote_plus
        host = self.spec.host or "127.0.0.1"
        port = self.spec.port or 27017
        auth = ""
        if self.spec.username:
            pwd = f":{quote_plus(self.spec.password)}" if self.spec.password else ""
            auth = f"{quote_plus(self.spec.username)}{pwd}@"
        db = self.spec.database or ""
        auth_db = (self.spec.options or {}).get("authSource") or "admin"
        qs = f"?authSource={auth_db}" if auth else ""
        return f"mongodb://{auth}{host}:{port}/{db}{qs}"

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                "未安装 pymongo，请执行 `pip install pymongo`。",
                code="driver_missing",
                dialect="mongo",
            ) from e
        try:
            self._client = MongoClient(
                self._build_uri(),
                serverSelectionTimeoutMS=int(self.spec.connect_timeout * 1000),
            )
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建 MongoClient 失败：{e}", code="engine_create_failed", dialect="mongo",
            ) from e
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def _db(self):
        client = self._get_client()
        return client[self.spec.database or "admin"]

    # ----- 接口 -----

    def test_connection(self) -> Tuple[bool, str]:
        try:
            self._get_client().admin.command("ping")
            return True, "MongoDB 连接成功"
        except ConnectorError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{type(e).__name__}: {e}"
        finally:
            self.close()

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        client = self._get_client()
        try:
            db = self._db()
            all_cols = db.list_collection_names()
            allowed = set(self.spec.allowed_collections or [])
            cols = [c for c in all_cols if (not allowed or c in allowed)][:50]

            tables: List[TableInfo] = []
            for c in cols:
                try:
                    coll = db[c]
                    # 字段探测：取 1 条文档的 top-level 键
                    one = coll.find_one({}, {"_id": 1}) or {}
                    fields = set(one.keys())
                    try:
                        # 再多探一条带更多字段的
                        more = coll.find_one({}, {}) or {}
                        fields.update(more.keys())
                    except Exception:  # noqa: BLE001
                        pass

                    col_infos = [
                        ColumnInfo(name=n, type="any") for n in sorted(fields)[:40]
                    ]
                    samples: List[Dict[str, Any]] = []
                    try:
                        cur = coll.aggregate(
                            [{"$sample": {"size": int(sample_rows)}},
                             {"$limit": int(sample_rows)}],
                            allowDiskUse=False,
                        )
                        for d in cur:
                            samples.append({
                                k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in d.items()
                            })
                    except Exception as e:  # noqa: BLE001
                        logger.debug("sample %s failed: %r", c, e)
                    tables.append(TableInfo(
                        name=c, comment="", columns=col_infos, sample_rows=samples,
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.warning("introspect collection %s failed: %r", c, e)
                    continue
            return SchemaSnapshot(
                source_id=self.source_id,
                source_kind=self.source_kind,
                dialect="mongo",
                tables=tables,
            )
        finally:
            self.close()

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: NLQuery) -> List[RetrievalChunk]:
        schema = self.introspect(sample_rows=3)
        allowed = list(self.spec.allowed_collections or [t.name for t in schema.tables])

        gen = generate_query(query, schema, llm_model=query.llm_model)
        err = validate_query(gen, allowed)
        if err:
            return [RetrievalChunk(
                content=f"生成的查询未通过校验：{err}\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
                citation=Citation(
                    title="Mongo 查询拦截",
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    generated_query=json.dumps(gen, ensure_ascii=False),
                    meta={"error": err},
                ),
                score=0.0,
                source_id=self.source_id,
                source_kind=self.source_kind,
            )]

        col_name = gen["collection"]
        op = (gen.get("op") or "find").lower()
        limit = max(1, int(gen.get("limit") or query.top_k or 50))

        docs: List[Dict[str, Any]] = []
        try:
            db = self._db()
            coll = db[col_name]
            if op == "find":
                cur = coll.find(
                    gen.get("filter") or {},
                    gen.get("projection") or None,
                )
                sort = gen.get("sort") or {}
                if sort:
                    cur = cur.sort(list(sort.items()))
                cur = cur.limit(limit)
                docs = list(cur)
            else:
                pipeline = list(gen.get("pipeline") or [])
                # 强制最后一 stage limit
                if pipeline and not any("$limit" in s for s in pipeline):
                    pipeline.append({"$limit": limit})
                docs = list(coll.aggregate(pipeline, allowDiskUse=False))
        except Exception as e:  # noqa: BLE001
            msg = f"Mongo 执行失败：{type(e).__name__}: {e}"
            logger.warning(msg)
            return [RetrievalChunk(
                content=msg + f"\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
                citation=Citation(
                    title=f"Mongo 执行错误",
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    generated_query=json.dumps(gen, ensure_ascii=False),
                ),
                score=0.0,
                source_id=self.source_id,
                source_kind=self.source_kind,
            )]
        finally:
            self.close()

        preview = docs[:limit]
        content_lines = [
            f"**Collection：** `{col_name}`",
            f"**Op：** {op}",
            f"**Reason：** {gen.get('reason') or ''}",
            "",
            "**生成的查询：**",
            "```json",
            json.dumps({k: v for k, v in gen.items() if k not in ("raw",)}, ensure_ascii=False, indent=2),
            "```",
            "",
            f"**结果（{len(preview)} 条）：**",
            "```json",
            json.dumps([{k: _safe(v) for k, v in d.items()} for d in preview],
                       ensure_ascii=False, indent=2, default=str)[:5000],
            "```",
        ]
        chunk = RetrievalChunk(
            content=self._trunc("\n".join(content_lines), 6000),
            citation=Citation(
                title=f"mongo:{self.spec.database or '-'}/{col_name}",
                source_id=self.source_id,
                source_kind=self.source_kind,
                generated_query=json.dumps(gen, ensure_ascii=False),
                meta={"op": op, "count": len(preview), "collection": col_name},
            ),
            score=1.0,
            source_id=self.source_id,
            source_kind=self.source_kind,
        )
        return [chunk]


def _safe(v: Any) -> Any:
    """ObjectId / datetime 等序列化兜底。"""
    try:
        json.dumps(v)
        return v
    except Exception:  # noqa: BLE001
        return str(v)[:200]
