"""异步 SQL Connector。

与同步版本 ``connector.SqlConnector`` 的差异：
- 使用 SQLAlchemy 2.x 的 ``create_async_engine``
- 驱动 dialect 优先异步包（asyncpg / asyncmy / aiosqlite）；缺失时回退同步版本
- ``search()`` 不再 ``run_in_executor``；直接 async 链路贯通
- LangGraph 的 Text2SQL 节点仍是 LLM 同步调用，但执行 SQL 的部分真正释放事件循环

为什么不替换同步版：
- 同步版仍然是**可用的兜底**（无需装额外异步驱动即可工作）
- Registry 在启动时探测异步驱动可用性，优先选异步；用户无感升级
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

from chayuan.server.knowledge_source.base import (
    BaseConnector,
    ConnectionSpec,
    ConnectorError,
)
from chayuan.server.knowledge_source.sql.dialects import get_profile
from chayuan.server.knowledge_source.sql.graph_text2sql import run_text2sql_pipeline
from chayuan.server.knowledge_source.sql.safety import intercept_readonly
from chayuan.server.knowledge_source.sql.text2sql import rows_to_markdown
from chayuan.server.knowledge_source.types import (
    Citation,
    ColumnInfo,
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
    TableInfo,
)

logger = logging.getLogger("chayuan.knowledge_source.sql.async")


# ---------------------------------------------------------------------------
# 异步驱动选择：方言 → async dialect prefix + driver 包
# ---------------------------------------------------------------------------

# dialect → (SQLAlchemy URL 前缀, 探测 import 模块)
ASYNC_DRIVER_MAP = {
    "mysql": ("mysql+asyncmy", "asyncmy"),
    "postgres": ("postgresql+asyncpg", "asyncpg"),
    "sqlite": ("sqlite+aiosqlite", "aiosqlite"),
    # Oracle：python-oracledb 2.x 原生支持 asyncio（SQLAlchemy 2.0.32+ 兼容 oracledb_async）
    "oracle": ("oracle+oracledb", "oracledb"),
    # SQL Server：aioodbc（需系统 ODBC Driver 18）
    "mssql": ("mssql+aioodbc", "aioodbc"),
    # ClickHouse：clickhouse-sqlalchemy 0.3+ 提供 asynch 驱动
    "clickhouse": ("clickhouse+asynch", "asynch"),
    # Doris 走 MySQL 协议：直接复用 asyncmy
    "doris": ("mysql+asyncmy", "asyncmy"),
}


# 默认端口表（当 spec.port=0 时使用）
_DEFAULT_PORTS = {
    "mysql": 3306, "postgres": 5432, "oracle": 1521,
    "mssql": 1433, "clickhouse": 8123, "doris": 9030,
}


def _async_driver_available(dialect: str) -> bool:
    mapping = ASYNC_DRIVER_MAP.get((dialect or "").lower())
    if not mapping:
        return False
    import importlib
    try:
        importlib.import_module(mapping[1])
        return True
    except Exception:  # noqa: BLE001
        return False


def _build_async_url(spec: ConnectionSpec) -> str:
    dialect = (spec.dialect or "").lower()
    if dialect not in ASYNC_DRIVER_MAP:
        raise ConnectorError(
            f"方言 {dialect!r} 无异步驱动映射", code="dialect_unsupported", dialect=dialect,
        )
    prefix, _pkg = ASYNC_DRIVER_MAP[dialect]
    from urllib.parse import quote_plus
    auth = ""
    if spec.username:
        pwd = f":{quote_plus(spec.password)}" if spec.password else ""
        auth = f"{quote_plus(spec.username)}{pwd}@"

    if dialect == "sqlite":
        db = spec.database or ":memory:"
        if db == ":memory:":
            return "sqlite+aiosqlite:///:memory:"
        return f"sqlite+aiosqlite:///{db}"

    host = spec.host or "127.0.0.1"
    port = spec.port or _DEFAULT_PORTS.get(dialect, 0)
    db = spec.database or ""

    if dialect == "oracle":
        # SQLAlchemy 对 oracle+oracledb 推荐用 service_name 参数形式
        service = (spec.options or {}).get("service_name") or spec.database or "XE"
        return f"{prefix}://{auth}{host}:{port}/?service_name={service}"

    if dialect == "mssql":
        # aioodbc + ODBC Driver 18；把 connection 细节都塞进 odbc_connect
        odbc_driver = (spec.options or {}).get("odbc_driver") or "ODBC Driver 18 for SQL Server"
        extra = (spec.options or {}).get("odbc_extra") or "Encrypt=no;TrustServerCertificate=yes"
        odbc_str = (
            f"DRIVER={{{odbc_driver}}};SERVER={host},{port};DATABASE={db};"
            f"UID={spec.username};PWD={spec.password};{extra}"
        )
        params = quote_plus(odbc_str)
        return f"mssql+aioodbc:///?odbc_connect={params}"

    return f"{prefix}://{auth}{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# AsyncSqlConnector
# ---------------------------------------------------------------------------

class AsyncSqlConnector(BaseConnector):
    """覆盖 7 种 SQL 方言的异步 Connector。

    驱动可用性在 Registry 层做运行时探测：任一驱动未装 → 自动回退同步版。
    这样你可以部分启用（只给 MySQL / PG 装 asyncmy / asyncpg，Oracle 继续走同步）。
    """

    dialects = (
        "mysql", "postgres", "sqlite",
        "oracle", "mssql", "clickhouse", "doris",
    )
    source_kind = SourceKind.SQL.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._engine = None
        self._profile = get_profile(spec.dialect)

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import create_async_engine

        url = _build_async_url(self.spec)
        kw: Dict[str, Any] = {"pool_pre_ping": True}
        if self._profile.name == "sqlite":
            kw["connect_args"] = {"check_same_thread": False}
        else:
            kw["pool_size"] = 4
            kw["max_overflow"] = 8
            kw["pool_recycle"] = 1800
        try:
            engine = create_async_engine(url, **kw)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建异步引擎失败：{e}", code="engine_create_failed",
                dialect=self._profile.name,
            ) from e

        if self.spec.read_only:
            # sync_engine 属性用于挂同步事件钩子（before_cursor_execute 只作用于同步层，
            # AsyncEngine 内部的 cursor 最终也是同步游标，钩子同样生效）
            try:
                event.listen(engine.sync_engine, "before_cursor_execute", intercept_readonly)
            except Exception:  # noqa: BLE001
                pass
        self._engine = engine
        return engine

    async def aclose(self) -> None:
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None

    def close(self) -> None:
        # 同步 close：在无法 await 的上下文（比如 orchestrator 异常路径）时的兜底
        if self._engine is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.aclose())
                else:
                    loop.run_until_complete(self.aclose())
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._engine = None

    # ---------------- 接口 ----------------

    def test_connection(self) -> Tuple[bool, str]:
        # 提供同步入口供"测试连接"按钮；内部 run_until_complete
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._atest())
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    async def _atest(self) -> Tuple[bool, str]:
        from sqlalchemy import text
        try:
            engine = self._get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True, f"{self._profile.name} 连接成功（async）"
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
        from sqlalchemy import text

        engine = self._get_engine()
        try:
            # inspect() 不支持 async；用 run_sync 包一层（官方推荐做法）
            def _pick(sync_conn):
                from sqlalchemy import inspect
                insp = inspect(sync_conn)
                all_tables = insp.get_table_names()
                allowed = set(self.spec.allowed_tables or [])
                tables = [t for t in all_tables if (not allowed or t in allowed)][:50]
                meta: List[Tuple[str, Any, str]] = []
                for t in tables:
                    cols = insp.get_columns(t)
                    try:
                        pks = set((insp.get_pk_constraint(t) or {}).get("constrained_columns") or [])
                    except Exception:  # noqa: BLE001
                        pks = set()
                    try:
                        cmt = (insp.get_table_comment(t) or {}).get("text") or ""
                    except Exception:  # noqa: BLE001
                        cmt = ""
                    meta.append((t, [
                        ColumnInfo(
                            name=c["name"], type=str(c.get("type", "")),
                            nullable=bool(c.get("nullable", True)),
                            primary_key=(c["name"] in pks),
                            comment=(c.get("comment") or "") or "",
                        ) for c in cols
                    ], cmt))
                return meta

            async with engine.connect() as conn:
                tables_meta = await conn.run_sync(_pick)
                snapshot_tables: List[TableInfo] = []
                for name, col_infos, cmt in tables_meta:
                    samples: List[Dict[str, Any]] = []
                    try:
                        rs = await conn.execute(
                            text(f"SELECT * FROM {name} LIMIT {int(sample_rows)}")
                        )
                        col_names = list(rs.keys())
                        for row in rs.fetchall():
                            samples.append(dict(zip(col_names, [str(v)[:120] for v in row])))
                    except Exception as e:  # noqa: BLE001
                        logger.debug("async sample %s failed: %r", name, e)
                    snapshot_tables.append(TableInfo(
                        name=name, comment=cmt, columns=col_infos, sample_rows=samples,
                    ))
            return SchemaSnapshot(
                source_id=self.source_id,
                source_kind=self.source_kind,
                dialect=self._profile.name,
                tables=snapshot_tables,
            )
        finally:
            await self.aclose()

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        # schema 优先从缓存读
        from chayuan.server.db.repository.knowledge_source_repository import (
            load_schema_cache,
        )
        schema = None
        try:
            schema = load_schema_cache(self.source_id)
        except Exception:  # noqa: BLE001
            schema = None
        if schema is None or not schema.tables:
            schema = await self._aintrospect(sample_rows=3)

        # run_sql 闭包：LangGraph execute 节点是同步的，这里用事件循环桥接
        loop = asyncio.get_event_loop()

        def _run_sql(sql: str):
            # 防御:剥 LLM 偶尔加上的 markdown 围栏(```sql ... ```);见
            # text2sql.strip_sql_fences 的注释。
            from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences
            sql = strip_sql_fences(sql or "")
            async def _do():
                from sqlalchemy import text
                try:
                    engine = self._get_engine()
                    async with engine.connect() as conn:
                        rs = await conn.execute(text(sql))
                        columns = list(rs.keys())
                        rows = [list(r) for r in rs.fetchmany(max(1, int(query.top_k or 50)))]
                except Exception as e:  # noqa: BLE001
                    return [], [], f"{type(e).__name__}: {e}"
                # P1-9：行集脱敏（与同步版一致）
                try:
                    from chayuan.server.governance.masking import (
                        level_for_role, mask_row_values,
                    )
                    lvl = (query.masking_level_override or "").strip() or \
                          level_for_role(query.user_role or "")
                    if lvl != "off":
                        rows = mask_row_values(rows, columns, override_level=lvl)
                except Exception as e:  # noqa: BLE001
                    logger.debug("async rows PII 脱敏失败（忽略）：%r", e)
                return columns, rows, ""
            return loop.run_until_complete(_do()) if not loop.is_running() else _sync_bridge(_do())

        try:
            # LangGraph 本身是同步 API，跑在默认线程池即可，不阻塞主事件循环
            result = await loop.run_in_executor(
                None,
                lambda: run_text2sql_pipeline(
                    source_id=self.source_id,
                    dialect=self._profile.name,
                    sqlglot_dialect=self._profile.sqlglot_dialect,
                    query=query,
                    schema=schema,
                    run_sql=_run_sql,
                    max_retries=2,
                    allowed_tables=list(self.spec.allowed_tables or []),
                ),
            )
        finally:
            await self.aclose()

        content = result.get("content") or ""
        meta = result.get("meta") or {}
        sql = result.get("generated_sql") or meta.get("generated_sql") or ""
        title = f"{self._profile.name}:{self.spec.database or '-'}"
        chunk = RetrievalChunk(
            content=self._trunc(content, 6000),
            citation=Citation(
                title=title,
                source_id=self.source_id,
                source_kind=self.source_kind,
                generated_query=sql,
                meta=meta,
            ),
            score=0.0 if meta.get("error") else 1.0,
            source_id=self.source_id,
            source_kind=self.source_kind,
        )
        return [chunk]


def _sync_bridge(coro):
    """如果我们已经在事件循环里，但 LangGraph 节点在 executor 线程里调用我们的 _run_sql，
    那里没有事件循环：此时开一个临时 loop 执行一次。
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
