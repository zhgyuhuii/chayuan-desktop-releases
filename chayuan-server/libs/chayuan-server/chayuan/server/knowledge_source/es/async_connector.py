"""异步 Elasticsearch Connector。

使用 ``elasticsearch.AsyncElasticsearch``（elasticsearch-py 官方内置，无需额外装包）。
与同步版功能一致；多源并行场景下真正释放事件循环。
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
from chayuan.server.knowledge_source.es.text2es import generate_dsl, validate_dsl
from chayuan.server.knowledge_source.types import (
    Citation,
    ColumnInfo,
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
    TableInfo,
)

logger = logging.getLogger("chayuan.knowledge_source.es.async")


class AsyncEsConnector(BaseConnector):
    dialects = ("es", "elasticsearch")
    source_kind = SourceKind.ES.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._client = None

    # ---------------- client ----------------

    def _endpoint(self) -> str:
        scheme = (self.spec.options or {}).get("scheme") or "http"
        host = self.spec.host or "127.0.0.1"
        port = self.spec.port or 9200
        return f"{scheme}://{host}:{port}"

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from elasticsearch import AsyncElasticsearch  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                "未安装 elasticsearch 客户端：`pip install elasticsearch`",
                code="driver_missing", dialect="es",
            ) from e
        kw: Dict[str, Any] = {"request_timeout": int(self.spec.connect_timeout)}
        if self.spec.username:
            kw["basic_auth"] = (self.spec.username, self.spec.password or "")
        api_key = (self.spec.options or {}).get("api_key")
        if api_key:
            kw["api_key"] = api_key
        verify = (self.spec.options or {}).get("verify_certs")
        if verify is not None:
            kw["verify_certs"] = bool(verify)
        try:
            self._client = AsyncElasticsearch(self._endpoint(), **kw)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建 AsyncElasticsearch 失败：{e}",
                code="engine_create_failed", dialect="es",
            ) from e
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def close(self) -> None:
        if self._client is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.aclose())
                else:
                    loop.run_until_complete(self.aclose())
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._client = None

    # ---------------- 接口 ----------------

    def test_connection(self) -> Tuple[bool, str]:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._atest())
        finally:
            loop.close()

    async def _atest(self) -> Tuple[bool, str]:
        try:
            info = await self._get_client().info()
            ver = None
            if isinstance(info, dict):
                ver = (info.get("version") or {}).get("number")
            else:
                body = getattr(info, "body", None) or {}
                ver = (body.get("version") or {}).get("number")
            return True, f"Elasticsearch 连接成功（版本 {ver or 'unknown'}）"
        except ConnectorError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{type(e).__name__}: {e}"
        finally:
            await self.aclose()

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._aintrospect(sample_rows))
        finally:
            loop.close()

    async def _aintrospect(self, sample_rows: int = 3) -> SchemaSnapshot:
        client = self._get_client()
        try:
            allowed = list(self.spec.allowed_indices or [])
            patterns = ",".join(allowed) if allowed else "*"
            try:
                mappings = await client.indices.get_mapping(index=patterns)
                mappings = getattr(mappings, "body", mappings)
            except Exception as e:  # noqa: BLE001
                raise ConnectorError(
                    f"获取 mapping 失败：{e}", code="introspect_failed", dialect="es",
                )
            tables: List[TableInfo] = []
            for idx_name, idx_meta in list(mappings.items())[:50]:
                try:
                    props = ((idx_meta or {}).get("mappings") or {}).get("properties") or {}
                    cols = [
                        ColumnInfo(name=n, type=str((p or {}).get("type") or "object"))
                        for n, p in list(props.items())[:40]
                    ]
                    samples: List[Dict[str, Any]] = []
                    try:
                        rs = await client.search(
                            index=idx_name, size=int(sample_rows),
                            body={"query": {"match_all": {}}},
                        )
                        rs = getattr(rs, "body", rs)
                        hits = (rs.get("hits") or {}).get("hits") or []
                        for h in hits:
                            src = h.get("_source") or {}
                            samples.append({
                                k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in list(src.items())[:20]
                            })
                    except Exception as e:  # noqa: BLE001
                        logger.debug("async es sample %s failed: %r", idx_name, e)
                    tables.append(TableInfo(name=idx_name, columns=cols, sample_rows=samples))
                except Exception as e:  # noqa: BLE001
                    logger.warning("introspect index %s failed: %r", idx_name, e)
                    continue
            return SchemaSnapshot(
                source_id=self.source_id,
                source_kind=self.source_kind,
                dialect="es",
                tables=tables,
            )
        finally:
            await self.aclose()

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        schema = await self._aintrospect(sample_rows=2)
        allowed = list(self.spec.allowed_indices or [t.name for t in schema.tables])
        top_k = max(1, int(query.top_k or 50))

        # LLM 生成 DSL 是同步调用，丢线程池
        loop = asyncio.get_event_loop()
        gen = await loop.run_in_executor(
            None, lambda: generate_dsl(query, schema, llm_model=query.llm_model),
        )
        err = validate_dsl(gen, allowed, top_k=top_k)
        if err:
            return [RetrievalChunk(
                content=f"生成的 DSL 未通过校验：{err}\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
                citation=Citation(
                    title="ES DSL 拦截",
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    generated_query=json.dumps(gen, ensure_ascii=False),
                    meta={"error": err},
                ),
                score=0.0,
                source_id=self.source_id,
                source_kind=self.source_kind,
            )]

        client = self._get_client()
        try:
            rs = await client.search(index=gen["index"], body=gen["body"])
            rs = getattr(rs, "body", rs)
            hits = (rs.get("hits") or {}).get("hits") or []
        except Exception as e:  # noqa: BLE001
            return [RetrievalChunk(
                content=f"ES 执行失败：{type(e).__name__}: {e}\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
                citation=Citation(
                    title="ES 执行错误",
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    generated_query=json.dumps(gen, ensure_ascii=False),
                ),
                score=0.0,
                source_id=self.source_id,
                source_kind=self.source_kind,
            )]
        finally:
            await self.aclose()

        content_lines = [
            f"**Index：** `{gen['index']}`",
            f"**Reason：** {gen.get('reason') or ''}",
            "",
            "**生成的 DSL：**",
            "```json",
            json.dumps(gen.get("body") or {}, ensure_ascii=False, indent=2),
            "```",
            "",
            f"**命中（{len(hits)} 条）：**",
        ]
        for i, h in enumerate(hits):
            src = h.get("_source") or {}
            content_lines.append(
                f"- #{i+1} _id={h.get('_id')} _score={h.get('_score')}:\n  "
                + json.dumps(src, ensure_ascii=False, default=str)[:400]
            )
        chunk = RetrievalChunk(
            content=self._trunc("\n".join(content_lines), 6000),
            citation=Citation(
                title=f"es:{gen['index']}",
                source_id=self.source_id,
                source_kind=self.source_kind,
                generated_query=json.dumps(gen, ensure_ascii=False),
                meta={"hits": len(hits), "index": gen["index"]},
            ),
            score=1.0,
            source_id=self.source_id,
            source_kind=self.source_kind,
        )
        return [chunk]
