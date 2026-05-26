"""ExternalVsConnector —— 把"外部向量库 collection"适配成 BaseConnector。

与 ``vector_adapter.VectorKbConnector`` 的区别
--------------------------------------------
- VectorKbConnector：封装**平台受管知识库**（对应 ``knowledge_base`` 表、
  ``KB_ROOT_PATH/<kb_name>/`` 目录），搜索走 ``search_docs``。
- ExternalVsConnector：封装**外部已存在的 Milvus/pg/ES/Chroma collection**，
  没有本地磁盘文件，直接用 langchain VectorStore + similarity_search。

生命周期
--------
- ``__init__`` **不**建任何连接（遵循 BaseConnector 约定：避免启动期 I/O）。
- ``search`` 首次调用时懒加载 VectorStore，后续缓存实例复用；
- ``test_connection`` 复用 ``vs_config.validate_connection`` 的 probe；
- ``close`` 尽量释放 VectorStore（各后端行为不同，尽力而为）。

和既有知识库管线的对接
----------------------
- Connector 返回 ``RetrievalChunk``，orchestrator 自动合并到并行检索流；
- ``source_kind = "vs"``，UI 在过滤、rerank 分组上会自然识别。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple


def _record_collection(dialect: str, collection: str, status: str, duration_s: float) -> None:
    """Prometheus 埋点；缺 ks_metrics 模块时 no-op。"""
    try:
        from chayuan.server.observability.ks_metrics import VS_COLLECTION_SEARCH
    except Exception:  # noqa: BLE001
        return
    try:
        if VS_COLLECTION_SEARCH is not None:
            VS_COLLECTION_SEARCH.labels(
                dialect=dialect or "-", collection=collection or "-", status=status,
            ).observe(max(0.0, float(duration_s)))
    except Exception:  # noqa: BLE001
        pass


def _timeout_of_exc(exc: BaseException) -> float:
    """asyncio.TimeoutError → 负值标识；其它异常按 0 记。给 metrics 用。"""
    return 0.0

from chayuan.server.knowledge_source.base import (
    BaseConnector,
    ConnectionSpec,
    ConnectorError,
)
from chayuan.server.knowledge_source.ext_vs.backends import (
    SUPPORTED_DIALECTS,
    build_vectorstore,
    build_vectorstore_for_collection,
    introspect_backend,
    is_vs_dialect,
)
from chayuan.server.knowledge_source.types import (
    Citation,
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
    TableInfo,
)

logger = logging.getLogger("chayuan.knowledge_source.ext_vs.connector")


class ExternalVsConnector(BaseConnector):
    """外部向量库 Connector。dialect ∈ SUPPORTED_DIALECTS.keys()。"""

    dialects = tuple(SUPPORTED_DIALECTS.keys())
    source_kind = SourceKind.VS.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        if not is_vs_dialect(spec.dialect):
            raise ConnectorError(
                f"外部向量库不支持的方言：{spec.dialect!r}",
                code="dialect_unsupported",
                dialect=spec.dialect,
            )
        self._vs = None  # 单 collection 模式的懒加载缓存（向后兼容路径）
        # 多 collection 并发检索：每 collection 一个 VectorStore，按 collection 名 memo。
        # 避免同一次 request 内重复构造；单 Connector 生命周期 ≈ 单请求。
        self._vs_by_collection: Dict[str, Any] = {}
        self._embeddings = None

    # ---------- embeddings ----------

    def _get_embeddings(self):
        """从 options.embed_model 读取；空则用系统默认。"""
        if self._embeddings is not None:
            return self._embeddings
        from chayuan.server.utils import get_Embeddings

        embed_model = str((self.spec.options or {}).get("embed_model") or "").strip() or None
        self._embeddings = get_Embeddings(embed_model=embed_model)
        return self._embeddings

    # ---------- test_connection ----------

    def test_connection(self) -> Tuple[bool, str]:
        """复用 vs_config.validate_connection（延迟 import 避免循环依赖）。"""
        try:
            # 延迟 import：config_panel 不应在启动期被 knowledge_source 强引用
            from chayuan.server.config_panel import vs_config
        except Exception as e:  # noqa: BLE001
            return False, f"vs_config 不可用：{e}"

        key = self._dialect_key_for_vs_config()
        vt = vs_config.vs_type_by_key(key)
        if vt is None:
            return False, f"vs_config 未登记方言 {key!r}"

        values = self._values_for_vs_config(key)
        res = vs_config.validate_connection(vt, values, timeout=5.0)
        ok = bool(res.get("ok"))
        msg = str(res.get("message") or "") or ("ok" if ok else "")
        # 带上可选的 detail 供 UI 展示
        det = str(res.get("detail") or "")
        if det:
            msg = f"{msg}  ·  {det}" if msg else det
        return ok, msg

    def _dialect_key_for_vs_config(self) -> str:
        d = (self.spec.dialect or "").lower()
        return {"postgresql": "pg", "postgres": "pg",
                "elasticsearch": "es"}.get(d, d)

    def _values_for_vs_config(self, key: str) -> Dict[str, Any]:
        """把 ConnectionSpec 翻译成 vs_config 的 values dict（和 VS_TYPES 字段对齐）。"""
        opts = self.spec.options or {}
        if key in ("milvus", "zilliz"):
            return {
                "host": self.spec.host or "",
                "port": str(self.spec.port or 19530),
                "user": self.spec.username or "",
                "password": self.spec.password or "",
                "secure": bool(opts.get("secure", key == "zilliz")),
                "token": str(opts.get("token") or ""),
                "db_name": str(opts.get("db_name") or ""),
            }
        if key in ("pg", "relyt"):
            # vs_config 的 pg 只有 connection_uri 一个字段
            from chayuan.server.knowledge_source.ext_vs.backends import _pg_connection_uri
            return {"connection_uri": _pg_connection_uri(self.spec)}
        if key == "es":
            return {
                "scheme": str(opts.get("scheme") or "http"),
                "host": self.spec.host or "",
                "port": str(self.spec.port or 9200),
                "user": self.spec.username or "",
                "password": self.spec.password or "",
                "index_name": str(opts.get("index_name") or opts.get("collection") or ""),
            }
        if key == "chromadb":
            return {}  # vs_config 的 chromadb 仅做依赖检查
        return {}

    # ---------- introspect ----------

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        """返回 collection 名字列表，塞到 SchemaSnapshot.tables.name 里。"""
        names = introspect_backend(self.spec)
        tables = [TableInfo(name=n, comment="", columns=[], sample_rows=[])
                  for n in names]
        return SchemaSnapshot(
            source_id=self.source_id,
            source_kind=self.source_kind,
            dialect=self.spec.dialect,
            tables=tables,
        )

    # ---------- search ----------

    def _get_vs(self):
        """单集合 VectorStore（历史路径，保留给未迁到 allowed_collections 的老调用方）。"""
        if self._vs is not None:
            return self._vs
        embeddings = self._get_embeddings()
        self._vs = build_vectorstore(self.spec, embeddings)
        return self._vs

    def _target_collections(self) -> List[str]:
        """决定本次检索要打哪几个 collection：

        1. ``spec.allowed_collections`` 非空 → 以白名单为准（"固定范围"语义）；
        2. 空 + ``options.collection`` 有值 → 退化到单集合（向后兼容旧配置）；
        3. 两者都空 → **现采**后端全部 collection，作为"默认全部"语义。

        现采代价在 VS 后端（Milvus/Chroma 的 list_collections）是 O(ms) 级，
        且结果会被 Connector 内存缓存（本次 request 一次），不会对性能敏感。
        单次 Connector 生命周期里这个值稳定，调用侧可安全遍历。
        """
        allowed = [str(c).strip() for c in (self.spec.allowed_collections or []) if str(c).strip()]
        if allowed:
            seen = set(); uniq = []
            for c in allowed:
                if c not in seen:
                    seen.add(c); uniq.append(c)
            return uniq
        legacy = (self.spec.options or {}).get("collection") or \
                 (self.spec.options or {}).get("collection_name") or \
                 (self.spec.options or {}).get("index_name") or ""
        legacy = str(legacy).strip()
        if legacy:
            return [legacy]
        # 两者都空：现采。失败则抛 ConnectorError，让上层转"友好失败 chunk"。
        names = [str(n).strip() for n in (introspect_backend(self.spec) or []) if str(n).strip()]
        if not names:
            raise ConnectorError(
                "外部向量源无可用集合（introspect 结果为空）",
                code="no_collection_available",
                dialect=self.spec.dialect,
            )
        # 现采场景下对总数做上限兜底，避免大集群一次扫爆
        max_auto = 20
        try:
            from chayuan.settings import Settings
            max_auto = int(
                getattr(Settings.basic_settings, "VS_AUTO_FANOUT_MAX_COLLECTIONS", 20) or 20
            )
        except Exception:  # noqa: BLE001
            pass
        return names[: max(1, max_auto)]

    def _get_vs_for(self, collection: str):
        """按 collection 懒建 VectorStore；单次 Connector 生命周期内 memo 复用。"""
        cached = self._vs_by_collection.get(collection)
        if cached is not None:
            return cached
        vs = build_vectorstore_for_collection(
            self.spec, self._get_embeddings(), collection,
        )
        self._vs_by_collection[collection] = vs
        return vs

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        """并发扇出到每一个被授权的 collection，合并后按 score 取 Top-K。

        - 每 collection 单独 ``run_in_executor``（底层 langchain VectorStore 基本是
          阻塞 I/O；线程池并发比 ``asyncio`` naive wrap 稳）；
        - 单 collection 失败 swallow 并记 metrics，不影响其它命中；
        - ``query.top_k`` 是**合并后**的上限；每 collection 先拉 ``k'=top_k`` 条，
          合并排序后截到 top_k，经典 fan-out/reduce；
        - 默认单 collection 超时 10s，可 spec.options["collection_timeout_sec"] 覆盖。
        """
        collections = self._target_collections()
        top_k = max(1, int(query.top_k or 5))
        timeout = self._per_collection_timeout()

        tasks = [
            self._search_one(col, query.query, top_k, timeout)
            for col in collections
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[RetrievalChunk] = []
        for col, res in zip(collections, results):
            if isinstance(res, Exception):
                logger.warning(
                    "ext_vs search collection %s 失败 dialect=%s source_id=%s: %r",
                    col, self.spec.dialect, self.source_id, res,
                )
                _record_collection(
                    self.spec.dialect, col, "error",
                    _timeout_of_exc(res),
                )
                continue
            merged.extend(res)

        # 合并按 score 降序（越大越相关，已归一），取 top_k
        merged.sort(key=lambda c: (c.score or 0.0), reverse=True)
        return merged[:top_k]

    async def _search_one(
        self, collection: str, query_text: str, top_k: int, timeout: float,
    ) -> List[RetrievalChunk]:
        import time as _t
        loop = asyncio.get_event_loop()
        t0 = _t.perf_counter()
        try:
            coro = loop.run_in_executor(
                None, self._search_sync_single, collection, query_text, top_k,
            )
            chunks = await asyncio.wait_for(coro, timeout=timeout) if timeout > 0 else await coro
            _record_collection(
                self.spec.dialect, collection, "ok",
                _t.perf_counter() - t0,
            )
            return chunks
        except Exception:
            # 调用方已 gather return_exceptions；这里不再二次 warning
            raise

    def _search_sync_single(
        self, collection: str, query_text: str, top_k: int,
    ) -> List[RetrievalChunk]:
        vs = self._get_vs_for(collection)
        pairs = vs.similarity_search_with_score(query_text, k=top_k)
        chunks: List[RetrievalChunk] = []
        for doc, score in pairs or []:
            content = getattr(doc, "page_content", "") or ""
            meta = dict(getattr(doc, "metadata", {}) or {})
            meta.pop("vector", None)
            score_f = _normalize_score(score, self.spec.dialect)
            chunks.append(RetrievalChunk(
                content=self._trunc(content, 2000),
                citation=Citation(
                    title=str(meta.get("source") or meta.get("file_name") or collection),
                    source_id=self.source_id,
                    source_kind=self.source_kind,
                    meta={
                        "dialect": self.spec.dialect,
                        "collection": collection,
                        **{k: str(v)[:200] for k, v in meta.items()},
                    },
                ),
                score=score_f,
                source_id=self.source_id,
                source_kind=self.source_kind,
            ))
        return chunks

    def _per_collection_timeout(self) -> float:
        try:
            v = float((self.spec.options or {}).get("collection_timeout_sec") or 10.0)
        except Exception:  # noqa: BLE001
            v = 10.0
        return max(0.0, v)

    def _collection_label(self) -> str:
        """UI / audit 用的"主集合"标签：首个 allowed_collections / options 里的 collection。"""
        try:
            cols = self._target_collections()
            return cols[0] if cols else ""
        except ConnectorError:
            return ""

    # ---------- cleanup ----------

    def close(self) -> None:
        targets = []
        if self._vs is not None:
            targets.append(self._vs)
            self._vs = None
        for vs in self._vs_by_collection.values():
            if vs is not None:
                targets.append(vs)
        self._vs_by_collection = {}
        self._embeddings = None
        for vs in targets:
            for attr in ("close", "persist"):
                try:
                    fn = getattr(vs, attr, None)
                    if callable(fn):
                        fn()
                except Exception:  # noqa: BLE001
                    pass


def _normalize_score(score: float, dialect: str) -> float:
    """把 langchain 各后端返回的 score 归一到 "越大越好" 的 [0, 1] 区间。

    约定（粗粒度）：
    - Milvus / Zilliz 默认 L2 距离 → 越小越相关；归一化为 1/(1+d)。
    - PG pgvector 默认 COSINE/L2 → Chroma similarity_search_with_score 返回的是 distance；
    - ES ApproxRetrievalStrategy 返回的是相似度分数（越大越好），保持不动。

    这里保守：若值在 [0,1]，认作相似度直通；否则按距离处理。
    """
    try:
        s = float(score)
    except Exception:
        return 1.0
    if 0.0 <= s <= 1.0:
        # 相似度
        return s
    if s < 0:
        return 0.0
    # 距离 → 1/(1+d)
    return 1.0 / (1.0 + s)
