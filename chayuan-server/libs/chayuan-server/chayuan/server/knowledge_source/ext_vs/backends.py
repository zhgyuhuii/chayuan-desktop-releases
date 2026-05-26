"""外部向量库各方言的 VectorStore 构造 + collection 列举。

设计原则
--------
- **零状态、纯函数**：``build_vectorstore(spec, embeddings)`` 返回 langchain
  ``VectorStore`` 实例；``introspect_backend(spec)`` 返回 collection / index
  名称列表。Connector 内部自己缓存。
- **惰性导入**：每个方言的驱动包只在真正被选中时才 import，避免单机
  环境因缺少 pymilvus / pgvector 等驱动就启动失败。
- **复用 vs_config**：``test_connection`` 不走本模块，而是 dispatch 到
  ``config_panel.vs_config.validate_connection``（已经在那里维护了 6 种
  方言的 probe 逻辑）——避免行为分叉。

约定：``ConnectionSpec.options`` 的关键字段
-------------------------------------------
- ``collection`` / ``collection_name`` —— Milvus / Chroma / PG / Relyt / Zilliz
  的集合名（ES 读 ``index_name``）
- ``secure`` / ``token`` / ``db_name`` —— Milvus / Zilliz 专用
- ``scheme`` —— ES 的 http/https
- ``distance_strategy`` —— PG/Relyt 可选：EUCLIDEAN（默认）/ COSINE / MAX_INNER_PRODUCT
- ``embed_model`` —— 可覆盖默认 embedder；None/空串表示用系统默认

未来扩展：加新的向量库后端只需要在 ``_DISPATCH`` 里注册一项 + 实现两个私有函数。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

from chayuan.server.knowledge_source.base import ConnectionSpec, ConnectorError

logger = logging.getLogger("chayuan.knowledge_source.ext_vs.backends")


# UI / API 共用的权威方言清单（不含 faiss，因为 faiss 只能本机、无法作为外部 BYO）
SUPPORTED_DIALECTS: Dict[str, str] = {
    "milvus":    "Milvus（自托管）",
    "zilliz":    "Zilliz Cloud",
    "pg":        "PostgreSQL + pgvector",
    "relyt":     "Relyt（阿里云瑶池 / Greenplum）",
    "es":        "Elasticsearch（向量字段）",
    "chromadb":  "ChromaDB（持久化目录）",
}


# ---------------------------------------------------------------------------
# 公共入口：按 dialect 分发
# ---------------------------------------------------------------------------

def _opt(spec: ConnectionSpec, *keys: str, default: Any = "") -> Any:
    """按优先级从 options 里取字段；找不到返回 default。"""
    opts = spec.options or {}
    for k in keys:
        if k in opts and opts[k] not in (None, ""):
            return opts[k]
    return default


def _require_collection(spec: ConnectionSpec, *keys: str) -> str:
    v = _opt(spec, *keys, default="")
    if not str(v).strip():
        raise ConnectorError(
            f"缺少 options.{keys[0]}（collection / index / table 名）",
            code="bad_options",
            dialect=spec.dialect,
        )
    return str(v).strip()


def build_vectorstore(spec: ConnectionSpec, embeddings) -> Any:
    """按 spec.dialect 构造 langchain VectorStore。失败统一抛 ConnectorError。"""
    fn = _DISPATCH_BUILD.get(_norm(spec.dialect))
    if fn is None:
        raise ConnectorError(
            f"外部向量库不支持的方言：{spec.dialect!r}",
            code="dialect_unsupported",
            dialect=spec.dialect,
        )
    try:
        return fn(spec, embeddings)
    except ConnectorError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(
            f"构造 {spec.dialect} VectorStore 失败：{type(e).__name__}: {e}",
            code="vs_build_failed",
            dialect=spec.dialect,
        ) from e


def build_vectorstore_for_collection(
    spec: ConnectionSpec, embeddings, collection: str,
) -> Any:
    """多集合并发检索的工厂：把 spec.options.collection / index_name 覆盖成传入值，
    再走同一条 ``build_vectorstore`` 管线。

    这样做的好处：六个 backend 的连接参数、凭证、SSL 选项完全复用，**只换
    collection 名**。不克隆整个 ConnectionSpec，只 copy options dict 并改 key。
    """
    from copy import copy as _copy
    spec2 = _copy(spec)
    opts = dict(spec.options or {})
    d = _norm(spec.dialect)
    if d == "es":
        opts["index_name"] = collection
    else:
        opts["collection"] = collection
        opts["collection_name"] = collection
    spec2.options = opts
    return build_vectorstore(spec2, embeddings)


def introspect_backend(spec: ConnectionSpec) -> List[str]:
    """返回后端现有的 collection / index 名字列表（用于 UI 下拉 + schema cache）。"""
    fn = _DISPATCH_INTROSPECT.get(_norm(spec.dialect))
    if fn is None:
        return []
    try:
        return list(fn(spec) or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("introspect %s failed: %r", spec.dialect, e)
        return []


def _norm(dialect: str) -> str:
    d = (dialect or "").strip().lower()
    # 允许前端传 postgresql / postgres
    return {"postgresql": "pg", "postgres": "pg",
            "elasticsearch": "es", "mongo": "mongodb"}.get(d, d)


# ---------------------------------------------------------------------------
# Milvus / Zilliz（共用 pymilvus，Zilliz 只是默认 secure + 通过 token 认证）
# ---------------------------------------------------------------------------

def _milvus_connection_args(spec: ConnectionSpec) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "host": spec.host or "127.0.0.1",
        "port": str(spec.port or 19530),
    }
    if spec.username:
        args["user"] = spec.username
    if spec.password:
        args["password"] = spec.password
    secure = bool(_opt(spec, "secure", default=False))
    if secure:
        args["secure"] = True
    token = _opt(spec, "token", default="")
    if token:
        args["token"] = token
    db_name = _opt(spec, "db_name", default="")
    if db_name:
        args["db_name"] = db_name
    return args


def _build_milvus(spec: ConnectionSpec, embeddings) -> Any:
    # 同 milvus_kb_service:优先 langchain_milvus(新包),回退 langchain_community(已弃用)
    try:
        from langchain_milvus import Milvus  # type: ignore
    except ImportError:
        from langchain_community.vectorstores import Milvus  # type: ignore

    collection = _require_collection(spec, "collection", "collection_name")
    return Milvus(
        embedding_function=embeddings,
        collection_name=collection,
        connection_args=_milvus_connection_args(spec),
        auto_id=True,
    )


def _introspect_milvus(spec: ConnectionSpec) -> List[str]:
    import time
    from pymilvus import connections, utility  # type: ignore

    alias = f"_chayuan_ext_vs_probe_{int(time.time() * 1000)}"
    args = _milvus_connection_args(spec)
    args["alias"] = alias
    try:
        connections.connect(**args)
        return list(utility.list_collections(using=alias) or [])
    finally:
        try:
            connections.disconnect(alias)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# pgvector / Relyt
# ---------------------------------------------------------------------------

def _pg_connection_uri(spec: ConnectionSpec) -> str:
    """pg / relyt：优先用 options.connection_uri；否则按 host/port/user/pwd/db 拼 URI。"""
    uri = _opt(spec, "connection_uri", default="").strip()
    if uri:
        return uri
    # 用户也可能分别填 host 等字段；这里尽力而为
    from urllib.parse import quote_plus
    user = quote_plus(spec.username or "")
    pwd = quote_plus(spec.password or "")
    auth = f"{user}:{pwd}@" if user or pwd else ""
    port = spec.port or 5432
    host = spec.host or "127.0.0.1"
    db = spec.database or "postgres"
    scheme = "postgresql+psycopg2" if _norm(spec.dialect) == "relyt" else "postgresql"
    return f"{scheme}://{auth}{host}:{port}/{db}"


def _build_pg_like(spec: ConnectionSpec, embeddings) -> Any:
    from langchain_community.vectorstores.pgvector import (  # type: ignore
        DistanceStrategy, PGVector,
    )

    uri = _pg_connection_uri(spec)
    if not uri:
        raise ConnectorError(
            "缺少 connection_uri（options.connection_uri 或 host/port/user/pwd/database）",
            code="bad_options", dialect=spec.dialect,
        )
    collection = _require_collection(spec, "collection", "collection_name")
    ds_key = str(_opt(spec, "distance_strategy", default="EUCLIDEAN") or "").upper()
    ds = getattr(DistanceStrategy, ds_key, DistanceStrategy.EUCLIDEAN)
    return PGVector(
        embedding_function=embeddings,
        collection_name=collection,
        distance_strategy=ds,
        connection_string=uri,
    )


def _introspect_pg_like(spec: ConnectionSpec) -> List[str]:
    """从 langchain_pg_collection 读取现有 collection。"""
    from sqlalchemy import create_engine, text  # type: ignore

    uri = _pg_connection_uri(spec)
    eng = create_engine(uri, pool_pre_ping=True,
                        connect_args={"connect_timeout": 5})
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM langchain_pg_collection ORDER BY name")
            ).fetchall()
            return [str(r[0]) for r in rows if r and r[0]]
    finally:
        try:
            eng.dispose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Elasticsearch（向量字段，ApproxRetrievalStrategy）
# ---------------------------------------------------------------------------

def _build_es(spec: ConnectionSpec, embeddings) -> Any:
    from langchain_community.vectorstores.elasticsearch import (  # type: ignore
        ApproxRetrievalStrategy, ElasticsearchStore,
    )

    scheme = str(_opt(spec, "scheme", default="http") or "http")
    host = spec.host or "127.0.0.1"
    port = spec.port or 9200
    url = f"{scheme}://{host}:{port}"
    index_name = _require_collection(spec, "index_name", "collection", "collection_name")

    kwargs: Dict[str, Any] = {
        "es_url": url,
        "index_name": index_name,
        "embedding": embeddings,
        "strategy": ApproxRetrievalStrategy(),
    }
    if spec.username or spec.password:
        kwargs["es_user"] = spec.username or ""
        kwargs["es_password"] = spec.password or ""
    return ElasticsearchStore(**kwargs)


def _introspect_es(spec: ConnectionSpec) -> List[str]:
    from elasticsearch import Elasticsearch  # type: ignore

    scheme = str(_opt(spec, "scheme", default="http") or "http")
    url = f"{scheme}://{spec.host or '127.0.0.1'}:{spec.port or 9200}"
    kwargs: Dict[str, Any] = {"request_timeout": 5}
    if spec.username:
        kwargs["basic_auth"] = (spec.username, spec.password or "")
    client = Elasticsearch(url, **kwargs)
    try:
        # 只列 _all，排除 . 开头的系统索引
        info = client.indices.get(index="*") or {}
        return sorted([n for n in info.keys() if not n.startswith(".")])
    finally:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ChromaDB（持久化目录）
# ---------------------------------------------------------------------------

def _chroma_persist_dir(spec: ConnectionSpec) -> str:
    """Chroma 以本机目录为"连接"；优先 options.persist_directory，其次 database 字段。"""
    p = _opt(spec, "persist_directory", "persist_dir", default="")
    if not p:
        p = spec.database or ""
    if not p:
        raise ConnectorError(
            "Chroma 需要 options.persist_directory（或 database 字段）指向持久化目录",
            code="bad_options", dialect=spec.dialect,
        )
    return str(p)


def _build_chroma(spec: ConnectionSpec, embeddings) -> Any:
    from langchain_community.vectorstores import Chroma  # type: ignore

    collection = _require_collection(spec, "collection", "collection_name")
    return Chroma(
        collection_name=collection,
        persist_directory=_chroma_persist_dir(spec),
        embedding_function=embeddings,
    )


def _introspect_chroma(spec: ConnectionSpec) -> List[str]:
    import chromadb  # type: ignore

    client = chromadb.PersistentClient(path=_chroma_persist_dir(spec))
    try:
        return sorted([c.name for c in client.list_collections() or []])
    finally:
        pass  # PersistentClient 没有 close


# ---------------------------------------------------------------------------
# Dispatch 表
# ---------------------------------------------------------------------------

_DISPATCH_BUILD: Dict[str, Callable[[ConnectionSpec, Any], Any]] = {
    "milvus":   _build_milvus,
    "zilliz":   _build_milvus,  # 同协议
    "pg":       _build_pg_like,
    "relyt":    _build_pg_like,
    "es":       _build_es,
    "chromadb": _build_chroma,
}

_DISPATCH_INTROSPECT: Dict[str, Callable[[ConnectionSpec], List[str]]] = {
    "milvus":   _introspect_milvus,
    "zilliz":   _introspect_milvus,
    "pg":       _introspect_pg_like,
    "relyt":    _introspect_pg_like,
    "es":       _introspect_es,
    "chromadb": _introspect_chroma,
}


def is_vs_dialect(dialect: str) -> bool:
    """供 registry / 路由判别用。"""
    return _norm(dialect) in _DISPATCH_BUILD


def vs_dialect_choices() -> Tuple[Tuple[str, str], ...]:
    """UI 下拉选项顺序（稳定）。"""
    return tuple(SUPPORTED_DIALECTS.items())
