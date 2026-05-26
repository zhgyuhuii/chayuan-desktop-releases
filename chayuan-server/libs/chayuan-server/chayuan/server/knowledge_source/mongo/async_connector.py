"""异步 MongoDB Connector（基于 motor）。

和同步版功能一致；主要价值在 **真正释放事件循环**：
- motor 的 find/aggregate 游标支持 ``async for``
- 并行多源场景下，多个 Mongo 查询之间不争用 pymongo 同步线程池
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Tuple

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

logger = logging.getLogger("chayuan.knowledge_source.mongo.async")


class AsyncMongoConnector(BaseConnector):
    dialects = ("mongo", "mongodb")
    source_kind = SourceKind.MONGO.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._client = None

    def _build_uri(self) -> str:
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
            from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                "未安装 motor，请执行 `pip install motor`。",
                code="driver_missing", dialect="mongo",
            ) from e
        try:
            self._client = AsyncIOMotorClient(
                self._build_uri(),
                serverSelectionTimeoutMS=int(self.spec.connect_timeout * 1000),
            )
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建 AsyncIOMotorClient 失败：{e}",
                code="engine_create_failed", dialect="mongo",
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
        return self._get_client()[self.spec.database or "admin"]

    # ---------------- 接口 ----------------

    def test_connection(self) -> Tuple[bool, str]:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._atest())
        finally:
            loop.close()

    async def _atest(self) -> Tuple[bool, str]:
        try:
            await self._get_client().admin.command("ping")
            return True, "MongoDB 连接成功（motor）"
        except ConnectorError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{type(e).__name__}: {e}"
        finally:
            self.close()

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._aintrospect(sample_rows))
        finally:
            loop.close()

    async def _aintrospect(self, sample_rows: int = 3) -> SchemaSnapshot:
        try:
            db = self._db()
            all_cols = await db.list_collection_names()
            allowed = set(self.spec.allowed_collections or [])
            cols = [c for c in all_cols if (not allowed or c in allowed)][:50]
            tables: List[TableInfo] = []
            for c in cols:
                try:
                    coll = db[c]
                    one = await coll.find_one({}, {"_id": 1}) or {}
                    fields = set(one.keys())
                    more = await coll.find_one({}, {}) or {}
                    fields.update(more.keys())
                    col_infos = [ColumnInfo(name=n, type="any") for n in sorted(fields)[:40]]
                    samples: List[Dict[str, Any]] = []
                    try:
                        cur = coll.aggregate(
                            [{"$sample": {"size": int(sample_rows)}},
                             {"$limit": int(sample_rows)}],
                            allowDiskUse=False,
                        )
                        async for d in cur:
                            samples.append({
                                k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in d.items()
                            })
                    except Exception as e:  # noqa: BLE001
                        logger.debug("async sample %s failed: %r", c, e)
                    tables.append(TableInfo(
                        name=c, comment="", columns=col_infos, sample_rows=samples,
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.warning("introspect async mongo col %s failed: %r", c, e)
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
        schema = await self._aintrospect(sample_rows=3)
        allowed = list(self.spec.allowed_collections or [t.name for t in schema.tables])

        # text2mongo 生成是 LLM 同步调用：丢线程池
        loop = asyncio.get_event_loop()
        gen = await loop.run_in_executor(
            None, lambda: generate_query(query, schema, llm_model=query.llm_model),
        )
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
                async for d in cur:
                    docs.append(d)
            else:
                pipeline = list(gen.get("pipeline") or [])
                if pipeline and not any("$limit" in s for s in pipeline):
                    pipeline.append({"$limit": limit})
                async for d in coll.aggregate(pipeline, allowDiskUse=False):
                    docs.append(d)
        except Exception as e:  # noqa: BLE001
            msg = f"Mongo 执行失败：{type(e).__name__}: {e}"
            return [RetrievalChunk(
                content=msg + f"\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
                citation=Citation(
                    title="Mongo 执行错误",
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
            f"**结果（{len(docs)} 条）：**",
            "```json",
            json.dumps(
                [{k: _safe(v) for k, v in d.items()} for d in docs],
                ensure_ascii=False, indent=2, default=str,
            )[:5000],
            "```",
        ]
        chunk = RetrievalChunk(
            content=self._trunc("\n".join(content_lines), 6000),
            citation=Citation(
                title=f"mongo:{self.spec.database or '-'}/{col_name}",
                source_id=self.source_id,
                source_kind=self.source_kind,
                generated_query=json.dumps(gen, ensure_ascii=False),
                meta={"op": op, "count": len(docs), "collection": col_name},
            ),
            score=1.0,
            source_id=self.source_id,
            source_kind=self.source_kind,
        )
        return [chunk]


def _safe(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except Exception:  # noqa: BLE001
        return str(v)[:200]
