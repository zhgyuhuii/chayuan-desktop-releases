"""统一 SQL Connector。

覆盖 MySQL / PostgreSQL / SQLite / SQL Server / Oracle / ClickHouse / Doris 七种方言。
方言差异都收敛到 `dialects.py`（URL 构造 + 驱动探测）+ `safety.py` + sqlglot 方言名。

生命周期：
- 构造时不连数据库
- test_connection() 打开一次临时连接做 SELECT 1
- introspect() 用 SQLAlchemy Inspector 枚举表、列、注释、并取 3 行采样
- search() → 生成 SQL（Text2SQL）→ 校验 → 执行 → 行集转 markdown RetrievalChunk
- close() 释放 engine（可选，单 request 一般用完即弃；长生命周期 Connector 可惜建池）
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
from chayuan.server.knowledge_source.sql.dialects import (
    build_sqlalchemy_url,
    get_profile,
)
from chayuan.server.knowledge_source.sql.safety import (
    ensure_readonly,
    intercept_readonly,
)
from chayuan.server.knowledge_source.sql.graph_text2sql import (
    run_text2sql_pipeline,
)
from chayuan.server.knowledge_source.sql.text2sql import (
    generate_sql,  # 保留供降级/测试
    rows_to_markdown,
    strip_sql_fences,
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

logger = logging.getLogger("chayuan.knowledge_source.sql")


def _snapshot_to_dict(snap: SchemaSnapshot) -> Dict[str, Any]:
    return {
        "source_id": snap.source_id,
        "source_kind": snap.source_kind,
        "dialect": snap.dialect,
        "tables": [
            {
                "name": t.name, "schema": t.schema, "comment": t.comment,
                "columns": [c.__dict__ for c in (t.columns or [])],
                "sample_rows": t.sample_rows,
                "row_count_estimate": t.row_count_estimate,
            }
            for t in (snap.tables or [])
        ],
    }


def _masking_cache_user_key(query: "NLQuery") -> Optional[int]:
    """为结果缓存生成按"脱敏视图"分桶的 key。

    仅当 user_role / masking_level_override 的组合会改变结果内容时才分桶；
    否则直接 None（所有用户共享同一缓存项，节省空间）。

    实现：把"(user_role, masking_level_override)"hash 成一个伪 user_id。
    """
    role = (query.user_role or "").lower()
    level = (query.masking_level_override or "").lower()
    if not role and not level:
        return None
    try:
        from chayuan.server.governance.masking import level_for_role
        effective_level = level or level_for_role(role) or "loose"
    except Exception:  # noqa: BLE001
        effective_level = level or "loose"
    if effective_level == "off":
        # admin 全走同一桶
        return -1
    import hashlib
    h = hashlib.sha1(f"{effective_level}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 10_000_000


def _chunk_from_cached(cached: Dict[str, Any], source_id: int, source_kind: str) -> RetrievalChunk:
    return RetrievalChunk(
        content=cached.get("content") or "",
        citation=Citation(
            title=cached.get("title") or "cached",
            source_id=source_id,
            source_kind=source_kind,
            generated_query=cached.get("generated_query") or "",
            meta={**(cached.get("meta") or {}), "from_cache": True},
        ),
        score=1.0,
        source_id=source_id,
        source_kind=source_kind,
    )


def _snapshot_from_dict(d: Dict[str, Any], source_id: int, source_kind: str, dialect: str) -> SchemaSnapshot:
    tables = []
    for t in d.get("tables") or []:
        cols = [ColumnInfo(**c) for c in (t.get("columns") or []) if isinstance(c, dict)]
        tables.append(TableInfo(
            name=t.get("name") or "",
            schema=t.get("schema") or "",
            comment=t.get("comment") or "",
            columns=cols,
            sample_rows=t.get("sample_rows") or [],
            row_count_estimate=t.get("row_count_estimate"),
        ))
    return SchemaSnapshot(
        source_id=source_id, source_kind=source_kind,
        dialect=dialect or d.get("dialect") or "", tables=tables,
    )


class SqlConnector(BaseConnector):
    dialects = (
        "mysql", "postgres", "sqlite", "mssql", "oracle", "clickhouse", "doris",
        "hive", "kingbase", "dm",
    )
    source_kind = SourceKind.SQL.value

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._engine = None
        self._profile = get_profile(spec.dialect)

    # ----------------------------- engine ----------------------------------

    def _query_timeout_sec(self) -> int:
        """P2-14-a：读 per-source 查询超时（秒）。Hive 默认 300s 兜底，其它方言默认 0（不限）。"""
        try:
            raw = (self.spec.options or {}).get("query_timeout_sec")
            if raw is None or str(raw).strip() == "":
                return 300 if self._profile.name == "hive" else 0
            return max(0, int(raw))
        except Exception:  # noqa: BLE001
            return 300 if self._profile.name == "hive" else 0

    def _kinit_if_needed(self) -> None:
        """P2-14-e：Hive + Kerberos 环境下，ticket 剩余时间不足 5 分钟触发 kinit。

        判定条件：spec.options.auth in (KERBEROS, GSSAPI) 且 `klist` 可用。
        kinit 命令通过 ``KRB5_KINIT_CMD`` 环境变量覆盖（默认 "kinit"），
        keytab/principal 分别从 ``KRB5_KEYTAB`` / ``KRB5_PRINCIPAL`` 读取。
        任何异常 swallow —— 不希望 ticket 抖动把业务流量打挂。
        """
        if self._profile.name != "hive":
            return
        auth = str((self.spec.options or {}).get("auth") or "").upper()
        if auth not in ("KERBEROS", "GSSAPI"):
            return
        try:
            import os
            import shutil
            import subprocess
            if not shutil.which("klist"):
                return
            # `klist -s` 退出码 0 表示 ticket 有效；非 0 缺 ticket
            st = subprocess.run(["klist", "-s"], timeout=3)
            need_renew = st.returncode != 0
            if not need_renew:
                # 进一步检查剩余时间：klist 第一行 "Valid starting...Expires"，
                # 粗粒度：我们直接按 "expires" 文本判断是否 <5min
                try:
                    out = subprocess.run(["klist"], capture_output=True, timeout=3, text=True)
                    text_out = (out.stdout or "") + (out.stderr or "")
                    # 简单启发：发现 "expired" 或 "不到 5" 则刷新
                    if "expired" in text_out.lower() or "invalid" in text_out.lower():
                        need_renew = True
                except Exception:  # noqa: BLE001
                    pass
            if not need_renew:
                return
            kinit = os.environ.get("KRB5_KINIT_CMD", "kinit")
            keytab = os.environ.get("KRB5_KEYTAB", "")
            princ = os.environ.get("KRB5_PRINCIPAL", "")
            if keytab and princ:
                subprocess.run([kinit, "-kt", keytab, princ], timeout=10)
            else:
                logger.debug("kinit 条件不足（KRB5_KEYTAB/PRINCIPAL 未配置），跳过")
        except Exception as e:  # noqa: BLE001
            logger.debug("kinit 执行失败（忽略，后续按已有 ticket 尝试）：%r", e)

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        # P2-14-e：必要时先刷新 Kerberos ticket
        self._kinit_if_needed()
        from sqlalchemy import create_engine, event

        url = build_sqlalchemy_url(self.spec)
        kw: Dict[str, Any] = {"pool_pre_ping": True}
        # SQLite 需要 check_same_thread=False
        if self._profile.name == "sqlite":
            kw["connect_args"] = {"check_same_thread": False}
        elif self._profile.name == "hive":
            # Hive：长查询场景多，维持较小连接池 + 更长 recycle
            kw["pool_size"] = 2
            kw["max_overflow"] = 4
            kw["pool_recycle"] = 3600
            # PyHive thrift socket 超时（秒）对齐 query_timeout_sec；0 表示不限
            timeout = self._query_timeout_sec()
            if timeout > 0:
                # pyhive/sqlalchemy-hive 支持 connect_args.socket_timeout（秒）
                kw["connect_args"] = {"socket_timeout": float(timeout)}
        else:
            kw["pool_size"] = 2
            kw["max_overflow"] = 4
            kw["pool_recycle"] = 1800
        try:
            engine = create_engine(url, **kw)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(
                f"创建引擎失败：{e}", code="engine_create_failed",
                dialect=self._profile.name,
            ) from e

        if self.spec.read_only:
            event.listen(engine, "before_cursor_execute", intercept_readonly)

        self._engine = engine
        return engine

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None

    # ----------------------------- 接口 -------------------------------------

    def test_connection(self) -> Tuple[bool, str]:
        try:
            from sqlalchemy import text
            engine = self._get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True, f"{self._profile.name} 连接成功"
        except ConnectorError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{type(e).__name__}: {e}"
        finally:
            # test 完释放，避免 UI 频繁测试时池化连接堆积
            self.close()

    def _hive_introspect_databases(self, engine) -> List[str]:
        """P2-14-c：Hive 多库发现。读 ``HIVE_INTROSPECT_MAX_DBS`` 上限（默认 10）。"""
        import os
        from sqlalchemy import text as _text

        max_dbs = 10
        try:
            from chayuan.settings import Settings
            max_dbs = int(
                getattr(Settings.basic_settings, "HIVE_INTROSPECT_MAX_DBS", 10) or 10
            )
        except Exception:  # noqa: BLE001
            max_dbs = int(os.environ.get("HIVE_INTROSPECT_MAX_DBS", "10") or 10)
        try:
            with engine.connect() as conn:
                rs = conn.execute(_text("SHOW DATABASES"))
                dbs = [str(r[0]) for r in rs.fetchall()]
        except Exception as e:  # noqa: BLE001
            logger.debug("hive SHOW DATABASES 失败，退化单库：%r", e)
            return []
        return dbs[: max(1, int(max_dbs))]

    def _introspect_from_qualified(
        self, engine, insp, per_db_tables: "List[tuple[str, str]]", sample_rows: int,
    ) -> SchemaSnapshot:
        """P2-14-c：从 (db, table) 清单构造 SchemaSnapshot。Hive 专用路径。"""
        from sqlalchemy import text as _text

        snapshot_tables: List[TableInfo] = []
        for db, t in per_db_tables:
            try:
                try:
                    cols = insp.get_columns(t, schema=db or None)
                except Exception:  # noqa: BLE001
                    cols = []
                col_infos = [
                    ColumnInfo(
                        name=c.get("name") or "",
                        type=str(c.get("type", "")),
                        nullable=bool(c.get("nullable", True)),
                        primary_key=False,
                        comment=(c.get("comment") or "") or "",
                    )
                    for c in cols
                ]
                t_comment = ""
                try:
                    t_comment = (insp.get_table_comment(t, schema=db or None) or {}).get("text") or ""
                except Exception:  # noqa: BLE001
                    pass
                samples: List[Dict[str, Any]] = []
                try:
                    with engine.connect() as conn:
                        qualified = f"{db}.{t}" if db and db != "default" else t
                        rs = conn.execute(_text(f"SELECT * FROM {qualified} LIMIT {int(sample_rows)}"))
                        col_names = list(rs.keys())
                        for row in rs.fetchall():
                            samples.append(dict(zip(col_names, [str(v)[:120] for v in row])))
                except Exception as e:  # noqa: BLE001
                    logger.debug("hive sample %s.%s failed: %r", db, t, e)
                snapshot_tables.append(TableInfo(
                    name=(f"{db}.{t}" if db and db != "default" else t),
                    schema=db or "",
                    comment=t_comment,
                    columns=col_infos,
                    sample_rows=samples,
                ))
            except Exception as e:  # noqa: BLE001
                logger.warning("hive introspect %s.%s 失败：%r", db, t, e)
                continue
        return SchemaSnapshot(
            source_id=self.source_id,
            source_kind=self.source_kind,
            dialect=self._profile.name,
            tables=snapshot_tables,
        )

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        from sqlalchemy import inspect, text

        engine = self._get_engine()
        try:
            insp = inspect(engine)

            # P2-14-c：Hive 多库元数据枚举；其他方言保留原有单库路径
            if self._profile.name == "hive":
                dbs = self._hive_introspect_databases(engine)
                allowed = set(self.spec.allowed_tables or [])
                per_db_tables: List[tuple[str, str]] = []  # (db, table)
                for db in dbs:
                    try:
                        names = insp.get_table_names(schema=db)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("hive SHOW TABLES in %s 失败：%r", db, e)
                        continue
                    for t in names:
                        qname = f"{db}.{t}" if db and db != "default" else t
                        if allowed and qname not in allowed and t not in allowed:
                            continue
                        per_db_tables.append((db, t))
                        if len(per_db_tables) >= 200:  # 总表数上限兜底
                            break
                    if len(per_db_tables) >= 200:
                        break
                # 没拿到任何库（权限不足 / Thrift 方言问题）时，退化到默认路径
                if per_db_tables:
                    return self._introspect_from_qualified(
                        engine, insp, per_db_tables, sample_rows,
                    )

            all_tables = insp.get_table_names()
            allowed = set(self.spec.allowed_tables or [])
            if allowed:
                tables = [t for t in all_tables if t in allowed]
            else:
                tables = all_tables[:50]  # 无白名单时只取前 50 张，防 token 爆

            snapshot_tables: List[TableInfo] = []
            for t in tables:
                try:
                    cols = insp.get_columns(t)
                    pks = set()
                    try:
                        pks = set((insp.get_pk_constraint(t) or {}).get("constrained_columns") or [])
                    except Exception:  # noqa: BLE001
                        pass
                    col_infos = [
                        ColumnInfo(
                            name=c["name"],
                            type=str(c.get("type", "")),
                            nullable=bool(c.get("nullable", True)),
                            primary_key=(c["name"] in pks),
                            comment=(c.get("comment") or "") or "",
                        )
                        for c in cols
                    ]
                    t_comment = ""
                    try:
                        t_comment = (insp.get_table_comment(t) or {}).get("text") or ""
                    except Exception:  # noqa: BLE001
                        pass
                    # 3 行采样
                    samples: List[Dict[str, Any]] = []
                    try:
                        with engine.connect() as conn:
                            # 注意：t 来自 insp.get_table_names()，不是用户输入，直接拼是安全的
                            rs = conn.execute(text(f"SELECT * FROM {t} LIMIT {int(sample_rows)}"))
                            col_names = list(rs.keys())
                            for row in rs.fetchall():
                                samples.append(dict(zip(col_names, [str(v)[:120] for v in row])))
                    except Exception as e:  # noqa: BLE001
                        logger.debug("introspect sample %s failed: %r", t, e)
                    snapshot_tables.append(TableInfo(
                        name=t, schema="", comment=t_comment, columns=col_infos, sample_rows=samples,
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.warning("introspect table %s failed: %r", t, e)
                    continue
            return SchemaSnapshot(
                source_id=self.source_id,
                source_kind=self.source_kind,
                dialect=self._profile.name,
                tables=snapshot_tables,
            )
        finally:
            self.close()

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        """异步入口：生成 SQL 与执行都是阻塞的，用 run_in_executor 包一下。"""
        import time as _t
        try:
            from chayuan.server.observability.ks_metrics import KS_CONNECTOR_SEARCH
        except Exception:  # noqa: BLE001
            KS_CONNECTOR_SEARCH = None
        _t0 = _t.time()
        status = "ok"
        try:
            loop = asyncio.get_event_loop()
            out = await loop.run_in_executor(None, self._search_sync, query)
            # 若 meta.error 有值，细分为业务性错误
            if out and isinstance(out[0].citation.meta, dict) and out[0].citation.meta.get("error"):
                status = str(out[0].citation.meta.get("error"))[:32]
            return out
        except Exception:
            status = "exception"
            raise
        finally:
            try:
                if KS_CONNECTOR_SEARCH is not None:
                    KS_CONNECTOR_SEARCH.labels(
                        kind=self.source_kind, dialect=self._profile.name, status=status,
                    ).observe(_t.time() - _t0)
            except Exception:  # noqa: BLE001
                pass

    def _load_schema(self) -> SchemaSnapshot:
        """三层 schema 缓存：Redis 热层 → DB 冷层 → 现采 introspect。

        Redis 命中：数毫秒；DB 命中：10-50ms；现采：秒级。效果差 2-3 个数量级。
        """
        # 1) Redis 热层
        try:
            from chayuan.server.knowledge_source.cache import schema_cache_get
            hot = schema_cache_get(self.source_id)
            if hot and hot.get("tables"):
                return _snapshot_from_dict(hot, self.source_id, self.source_kind, self._profile.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("schema hot cache read 失败：%r", e)

        # 2) DB 冷层
        try:
            from chayuan.server.db.repository.knowledge_source_repository import (
                load_schema_cache,
            )
            cached = load_schema_cache(self.source_id)
            if cached is not None and cached.tables:
                cached.dialect = self._profile.name
                # 回写 Redis
                try:
                    from chayuan.server.knowledge_source.cache import schema_cache_set
                    schema_cache_set(self.source_id, _snapshot_to_dict(cached))
                except Exception:  # noqa: BLE001
                    pass
                return cached
        except Exception as e:  # noqa: BLE001
            logger.debug("load_schema_cache 异常（忽略，现采）：%r", e)

        # 3) 现采 + 双层回写
        snap = self.introspect(sample_rows=3)
        try:
            from chayuan.server.db.repository.knowledge_source_repository import (
                replace_schema_cache,
            )
            replace_schema_cache(self.source_id, snap)
            from chayuan.server.knowledge_source.cache import schema_cache_set
            schema_cache_set(self.source_id, _snapshot_to_dict(snap))
        except Exception as e:  # noqa: BLE001
            logger.debug("schema 缓存回写失败：%r", e)
        return snap

    def _effective_tenant(self, user_role: str = "") -> str:
        """N-9：当前请求的 tenant_id；空串代表不启用多租户。

        来源优先级：
        1. spec.options["tenant_id"]（数据源级强制；admin 配的）
        2. shared.tenant_context.current_tenant_id()（请求级）
        """
        try:
            explicit = (self.spec.options or {}).get("tenant_id") or ""
            if explicit:
                return str(explicit)
            from chayuan.server.shared.tenant_context import current_tenant_id
            return str(current_tenant_id() or "")
        except Exception:  # noqa: BLE001
            return ""

    def _exec_sql(self, sql: str, top_k: int,
                  user_role: str = "", masking_level: str = ""):
        """返回 (columns, rows, error_msg)。error_msg 非空表示失败。

        **P1-9 治理接入**：
        - 默认按 ``user_role`` 的脱敏等级对 rows 做单元格级 PII 扫描 + 替换
        - ``masking_level`` 非空时覆盖默认（供策略层强制注入）
        - admin 或 masking_level=off 时行集原样返回
        - 脱敏失败 fail-open（只打 debug 日志，不影响查询结果返回）
        """
        from sqlalchemy import text

        # 防御:无论上游(text2sql / 模板缓存 / 用户手填)给的 SQL 是否被 LLM
        # 用 markdown 围栏 ```sql ... ``` 包过,在执行前再剥一遍。这样即使
        # 某个新模型 / 新 provider 不守规矩,也不会让 psycopg2 报
        # `syntax error at or near "```"`。
        sql = strip_sql_fences(sql or "")

        # N-9：tenant 注入（应用层 WHERE + 连接级 SET LOCAL）
        tenant_id = self._effective_tenant(user_role=user_role)
        effective_sql = sql
        if tenant_id:
            try:
                from chayuan.server.knowledge_source.sql.tenant_guard import (
                    inject_tenant_where,
                )
                tenant_tables = set((self.spec.options or {}).get("tenant_tables") or [])
                effective_sql = inject_tenant_where(
                    sql, dialect=self._profile.sqlglot_dialect,
                    tenant_id=tenant_id,
                    column=str((self.spec.options or {}).get("tenant_column") or "tenant_id"),
                    tenant_tables=tenant_tables or None,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("tenant inject 失败，按原 SQL 执行：%r", e)

        try:
            engine = self._get_engine()
            # PG 家族（含人大金仓 KingbaseES）共享 SET LOCAL statement_timeout 与 RLS 语法
            _is_pg_family = self._profile.name in ("postgres", "kingbase")
            # Oracle 家族（含达梦 DM）共享 SYSDATE / ROWNUM / TO_CHAR 等；也不走 SET LOCAL
            _is_oracle_family = self._profile.name in ("oracle", "dm")
            with engine.connect() as conn:
                # Postgres / Kingbase：额外设 SET LOCAL，配合业务表的 RLS 策略
                if tenant_id and _is_pg_family:
                    try:
                        from chayuan.server.knowledge_source.sql.tenant_guard import (
                            set_postgres_rls_tenant,
                        )
                        set_postgres_rls_tenant(conn, tenant_id)
                    except Exception:  # noqa: BLE001
                        pass
                # P2-14-a：per-query statement timeout（毫秒级 SET）
                qt = self._query_timeout_sec()
                if qt > 0:
                    try:
                        if _is_pg_family:
                            conn.execute(text(f"SET LOCAL statement_timeout = {qt * 1000}"))
                        elif self._profile.name == "mysql":
                            conn.execute(text(f"SET SESSION max_execution_time = {qt * 1000}"))
                        elif _is_oracle_family:
                            # Oracle / DM：用 resource plan 超时不方便；依赖客户端 socket timeout
                            pass
                        # Hive / SQLite / MSSQL / ClickHouse / Doris：依赖 socket/driver 层超时
                    except Exception:  # noqa: BLE001
                        logger.debug("apply query timeout failed", exc_info=True)
                rs = conn.execute(text(effective_sql))
                columns = list(rs.keys())
                rows = [list(r) for r in rs.fetchmany(max(1, int(top_k or 50)))]
        except Exception as e:  # noqa: BLE001
            return [], [], f"{type(e).__name__}: {e}"

        # P1-9：行集级脱敏
        try:
            from chayuan.server.governance.masking import (
                apply_masking, level_for_role, mask_row_values,
            )
            level = (masking_level or "").strip() or level_for_role(user_role or "")
            if level != "off":
                rows = mask_row_values(
                    rows, columns, override_level=level,
                )
            # 列名本身一般不含 PII，这里不脱敏
        except Exception as e:  # noqa: BLE001
            logger.debug("rows PII 脱敏失败（忽略）：%r", e)
        return columns, rows, ""

    def _auto_seed_ddl(self, schema: SchemaSnapshot) -> None:
        """首次出现的表 DDL 自动灌到训练语料（kind=ddl）。

        效果：用户刚接上一个新数据源，不用手工 train，RAG 已经能派上用场。
        写失败静默（训练语料非关键路径）。
        """
        try:
            from chayuan.server.db.repository.sql_training_repository import add_sample
            for t in schema.tables[:60]:
                add_sample(
                    source_id=self.source_id, kind="ddl",
                    sql=t.ddl_hint(), content=t.comment or "",
                    dialect=self._profile.name, approved=1,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("auto_seed_ddl 跳过：%r", e)

    def _search_sync(self, query: NLQuery) -> List[RetrievalChunk]:
        # 结果缓存（P0-2 L3）命中 → 直接还原 chunk
        # **P1-9 重要**：user_role 进 cache 作用域。不同角色看到的脱敏等级不同，
        # 必须分桶缓存；否则先访问的 admin 会把明文答案缓给后来的普通用户。
        try:
            from chayuan.server.knowledge_source.cache import result_cache_get
            cache_user_key = _masking_cache_user_key(query)
            cached = result_cache_get(
                "sql", self.source_id, query.query or "",
                user_id=cache_user_key,
            )
            if cached:
                return [_chunk_from_cached(cached, self.source_id, self.source_kind)]
        except Exception as e:  # noqa: BLE001
            logger.debug("result cache read 失败（忽略）：%r", e)

        schema = self._load_schema()
        # 后台顺手补种 DDL 训练样本（幂等：sha1 去重）
        self._auto_seed_ddl(schema)

        try:
            result = run_text2sql_pipeline(
                source_id=self.source_id,
                dialect=self._profile.name,
                sqlglot_dialect=self._profile.sqlglot_dialect,
                query=query,
                schema=schema,
                run_sql=lambda sql: self._exec_sql(
                    sql, top_k=int(query.top_k or 50),
                    user_role=query.user_role or "",
                    masking_level=query.masking_level_override or "",
                ),
                max_retries=2,
                # "范围查询"：spec.allowed_tables 决定 AST 硬校验开关（空=不校验）
                allowed_tables=list(self.spec.allowed_tables or []),
            )
        finally:
            # 每次检索后断开，避免长连接中的事务状态残留
            self.close()

        content = result.get("content") or ""
        meta = result.get("meta") or {}
        sql = result.get("generated_sql") or meta.get("generated_sql") or ""

        title = f"{self._profile.name}:{self.spec.database or '-'}"
        if meta.get("error"):
            title = f"{self._profile.name} {meta['error']}"

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

        # 结果缓存只写成功路径；失败不能缓存，否则用户会反复看到同一条错误
        if not meta.get("error"):
            try:
                from chayuan.server.knowledge_source.cache import result_cache_set
                result_cache_set(
                    "sql", self.source_id, query.query or "",
                    payload={
                        "content": chunk.content, "title": title,
                        "generated_query": sql, "meta": meta,
                    },
                    user_id=_masking_cache_user_key(query),
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("result cache write 失败（忽略）：%r", e)
        return [chunk]
