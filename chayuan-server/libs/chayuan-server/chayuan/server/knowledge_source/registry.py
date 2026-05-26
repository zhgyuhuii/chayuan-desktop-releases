"""Connector 注册表。按 dialect 字符串选 Connector 类。

注册采用**惰性导入**：Connector 对应的驱动包（cx_Oracle / pyodbc 等）可能
未安装，把 import 放到 _factory 里，找不到对象时返回友好错误，主服务启动不受影响。
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable, Dict, Optional

from chayuan.server.knowledge_source.base import (
    BaseConnector,
    ConnectionSpec,
    ConnectorError,
)

logger = logging.getLogger("chayuan.knowledge_source.registry")


# dialect（小写）→ "模块路径:类名"
# 注意：AsyncSqlConnector / AsyncMongoConnector 要求装了异步驱动；本模块运行时探测，
# 若未装则自动回退到同步实现，上层无感。
_SYNC_DIALECT_MAP: Dict[str, str] = {
    "mysql": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "postgres": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "postgresql": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "sqlite": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "mssql": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "sqlserver": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "oracle": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "clickhouse": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "doris": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "hive": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "impala": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    # 国产信创
    "kingbase": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "dm": "chayuan.server.knowledge_source.sql.connector:SqlConnector",
    "mongo": "chayuan.server.knowledge_source.mongo.connector:MongoConnector",
    "mongodb": "chayuan.server.knowledge_source.mongo.connector:MongoConnector",
    "es": "chayuan.server.knowledge_source.es.connector:EsConnector",
    "elasticsearch": "chayuan.server.knowledge_source.es.connector:EsConnector",
    # 图像（多模态）- kind=image 走 ImageConnector
    "image": "chayuan.server.image_source.connector:ImageConnector",
}

# 存在异步实现的方言；只有在对应异步驱动安装后，build_connector 才会选择
_ASYNC_DIALECT_MAP: Dict[str, str] = {
    # SQL 全家族：AsyncSqlConnector 覆盖 7 个方言
    "mysql": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "postgres": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "postgresql": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "sqlite": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "oracle": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "mssql": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "sqlserver": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "clickhouse": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    "doris": "chayuan.server.knowledge_source.sql.async_connector:AsyncSqlConnector",
    # NoSQL
    "mongo": "chayuan.server.knowledge_source.mongo.async_connector:AsyncMongoConnector",
    "mongodb": "chayuan.server.knowledge_source.mongo.async_connector:AsyncMongoConnector",
    # 搜索引擎
    "es": "chayuan.server.knowledge_source.es.async_connector:AsyncEsConnector",
    "elasticsearch": "chayuan.server.knowledge_source.es.async_connector:AsyncEsConnector",
}

# 运行时可切换（测试时可 force disable async）
_ASYNC_ENABLED = True


def set_async_enabled(enabled: bool) -> None:
    global _ASYNC_ENABLED
    _ASYNC_ENABLED = bool(enabled)


def _async_driver_available(dialect: str) -> bool:
    """每个方言检查对应异步驱动是否安装。

    ES 走 ``elasticsearch.AsyncElasticsearch``——同一 elasticsearch 包内置，
    只要 elasticsearch 可导入就算可用。
    """
    import importlib
    checks = {
        "mysql": "asyncmy",
        "postgres": "asyncpg",
        "postgresql": "asyncpg",
        "sqlite": "aiosqlite",
        "oracle": "oracledb",
        "mssql": "aioodbc",
        "sqlserver": "aioodbc",
        "clickhouse": "asynch",
        "doris": "asyncmy",  # Doris 共用 asyncmy
        "mongo": "motor",
        "mongodb": "motor",
        "es": "elasticsearch",
        "elasticsearch": "elasticsearch",
    }
    pkg = checks.get(dialect)
    if not pkg:
        return False
    try:
        importlib.import_module(pkg)
        return True
    except Exception:  # noqa: BLE001
        return False


# 兼容旧引用
_DIALECT_MAP = _SYNC_DIALECT_MAP


#: 方言正规化：UI 和 API 入口都走这里，避免大小写 / 别名扩散
_DIALECT_ALIAS = {
    "postgresql": "postgres",
    "sqlserver": "mssql",
    "mongodb": "mongo",
    "elasticsearch": "es",
    "hive2": "hive",
    "hiveserver2": "hive",
    "apache-hive": "hive",
    # 人大金仓常见写法
    "kingbaseES": "kingbase",
    "kingbase_es": "kingbase",
    "kingbasees": "kingbase",
    "kb": "kingbase",
    # 达梦常见写法
    "dameng": "dm",
    "da-meng": "dm",
    "dm7": "dm",
    "dm8": "dm",
    "dmdb": "dm",
}


#: 外部向量库（kind="vs"）的方言白名单。注册它们是为了在 CRUD / 连接测试 /
#: orchestrator 构造 Connector 时统一走 ``ExternalVsConnector``，而不会和
#: 文本侧的 es / pg SQL 语义混淆。
_VS_DIALECTS: frozenset = frozenset({
    "milvus", "zilliz", "pg", "relyt", "es", "chromadb",
})


def normalize_dialect(dialect: str) -> str:
    d = (dialect or "").strip().lower()
    return _DIALECT_ALIAS.get(d, d)


def vs_supported_dialects() -> frozenset:
    """供 UI / API 层白名单校验用。"""
    return _VS_DIALECTS


def all_supported_dialects() -> Dict[str, str]:
    """供 WebUI 下拉用：返回 {dialect: 友好中文名}。"""
    return {
        "mysql": "MySQL",
        "postgres": "PostgreSQL",
        "sqlite": "SQLite",
        "mssql": "SQL Server",
        "oracle": "Oracle",
        "clickhouse": "ClickHouse",
        "doris": "Apache Doris / StarRocks",
        "hive": "Apache Hive (HiveServer2 / Impala 兼容)",
        "kingbase": "人大金仓 KingbaseES（PG 协议兼容）",
        "dm": "达梦 DM（Oracle 协议兼容）",
        "mongo": "MongoDB",
        "es": "Elasticsearch",
        "image": "图像（CLIP / SigLIP / Chinese-CLIP）",
    }


def all_supported_vs_dialects() -> Dict[str, str]:
    """外部向量库（kind="vs"）的 UI 下拉选项。"""
    from chayuan.server.knowledge_source.ext_vs.backends import (
        SUPPORTED_DIALECTS,
    )
    return dict(SUPPORTED_DIALECTS)


def get_connector_class(dialect: str, *, kind: str = "") -> type:
    """优先返回异步实现（驱动已装 + 全局开关打开）；否则返回同步版。

    ``kind`` 参数用于消歧：对 ``es`` / ``pg`` 等同名方言，
    - ``kind="vs"`` → ExternalVsConnector（外部向量库）
    - 其它 / 空   → 原有 Connector（SQL / Mongo / ES 文本检索 / 向量受管 KB）
    """
    dialect = normalize_dialect(dialect)

    # kind=vs：外部向量库独立通道，不走 _SYNC_DIALECT_MAP
    if (kind or "").strip().lower() == "vs":
        if dialect not in _VS_DIALECTS:
            raise ConnectorError(
                f"外部向量库不支持的方言：{dialect!r}",
                code="dialect_unsupported",
                dialect=dialect,
            )
        from chayuan.server.knowledge_source.ext_vs.connector import (
            ExternalVsConnector,
        )
        return ExternalVsConnector

    if dialect not in _SYNC_DIALECT_MAP:
        raise ConnectorError(
            f"不支持的数据源方言：{dialect!r}",
            code="dialect_unsupported",
            dialect=dialect,
        )

    # 选 async 版本（可选）
    if _ASYNC_ENABLED and dialect in _ASYNC_DIALECT_MAP and _async_driver_available(dialect):
        target = _ASYNC_DIALECT_MAP[dialect]
    else:
        target = _SYNC_DIALECT_MAP[dialect]

    mod_path, cls_name = target.split(":", 1)
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:  # noqa: BLE001
        # 异步实现加载失败 → 回退到同步
        if target == _ASYNC_DIALECT_MAP.get(dialect):
            logger.warning("async connector 加载失败，回退同步：%r", e)
            mod_path, cls_name = _SYNC_DIALECT_MAP[dialect].split(":", 1)
            mod = importlib.import_module(mod_path)
        else:
            raise ConnectorError(
                f"Connector 加载失败（{dialect}）：{e}",
                code="connector_load_failed",
                dialect=dialect,
            ) from e
    cls = getattr(mod, cls_name, None)
    if cls is None:
        raise ConnectorError(
            f"Connector 类 {cls_name} 在 {mod_path} 中不存在",
            code="connector_load_failed",
            dialect=dialect,
        )
    return cls


def build_connector(
    spec: ConnectionSpec, source_id: int = 0, *, kind: str = "",
) -> BaseConnector:
    cls = get_connector_class(spec.dialect, kind=kind)
    return cls(spec=spec, source_id=source_id)
