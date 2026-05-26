"""Elasticsearch Connector。

- test_connection: `info()`
- introspect: 取 index mapping 的 properties 作为字段；用 _search size=3 做采样
- search: Text2ES → 校验 → _search → 结果格式化
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

logger = logging.getLogger("chayuan.knowledge_source.es")


class EsConnector(BaseConnector):
    dialects = ("es", "elasticsearch")
    source_kind = SourceKind.ES.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._client = None

    # ----- client -----

    def _endpoint(self) -> str:
        scheme = (self.spec.options or {}).get("scheme") or "http"
        host = self.spec.host or "127.0.0.1"
        port = self.spec.port or 9200
        return f"{scheme}://{host}:{port}"

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from elasticsearch import Elasticsearch  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                "未安装 elasticsearch 客户端，请执行 `pip install elasticsearch`。",
                code="driver_missing",
                dialect="es",
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
            self._client = Elasticsearch(self._endpoint(), **kw)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建 Elasticsearch client 失败：{e}",
                code="engine_create_failed",
                dialect="es",
            ) from e
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    # ----- 接口 -----

    def test_connection(self) -> Tuple[bool, str]:
        try:
            info = self._get_client().info()
            ver = info.get("version", {}).get("number") if isinstance(info, dict) else ""
            # elasticsearch 8.x 可能返回 ObjectApiResponse
            if not ver:
                try:
                    ver = info.body.get("version", {}).get("number", "")  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            return True, f"Elasticsearch 连接成功（版本 {ver}）"
        except ConnectorError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{type(e).__name__}: {e}"
        finally:
            self.close()

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        client = self._get_client()
        try:
            # 白名单优先
            allowed = list(self.spec.allowed_indices or [])
            if allowed:
                patterns = ",".join(allowed)
            else:
                patterns = "*"
            try:
                mappings = client.indices.get_mapping(index=patterns)
                if hasattr(mappings, "body"):
                    mappings = mappings.body  # ES 8.x
            except Exception as e:  # noqa: BLE001
                raise ConnectorError(f"获取 mapping 失败：{e}", code="introspect_failed", dialect="es")
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
                        rs = client.search(
                            index=idx_name,
                            size=int(sample_rows),
                            body={"query": {"match_all": {}}},
                        )
                        hits = (rs.get("hits") or {}).get("hits") or []
                        for h in hits:
                            src = h.get("_source") or {}
                            samples.append({
                                k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in list(src.items())[:20]
                            })
                    except Exception as e:  # noqa: BLE001
                        logger.debug("es sample %s failed: %r", idx_name, e)
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
            self.close()

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: NLQuery) -> List[RetrievalChunk]:
        schema = self.introspect(sample_rows=2)
        allowed = list(self.spec.allowed_indices or [t.name for t in schema.tables])
        top_k = max(1, int(query.top_k or 50))

        gen = generate_dsl(query, schema, llm_model=query.llm_model)
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
        hits: List[Dict[str, Any]] = []
        try:
            rs = client.search(index=gen["index"], body=gen["body"])
            if hasattr(rs, "body"):
                rs = rs.body  # ES 8.x
            hits = (rs.get("hits") or {}).get("hits") or []
        except Exception as e:  # noqa: BLE001
            msg = f"ES 执行失败：{type(e).__name__}: {e}"
            logger.warning(msg)
            return [RetrievalChunk(
                content=msg + f"\n\n```json\n{json.dumps(gen, ensure_ascii=False, indent=2)}\n```",
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
            self.close()

        # 合成 markdown
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
