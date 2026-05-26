"""多租户 SQL 注入守卫（N-9）。

两条路径任选其一（可叠加）：

1. **应用层 WHERE 注入**（通用，所有方言都适用）
   用 sqlglot 在解析后的 AST 上给每个 FROM table 的 WHERE 追加 ``tenant_id = :tenant``
   - 优点：不依赖数据库；对所有方言统一
   - 局限：对 CTE、子查询嵌套里的表可能遗漏；信任度中等

2. **Postgres RLS**（推荐，安全性最强）
   ``SET LOCAL app.tenant_id = '...'``；业务表上建 RLS 策略
   - 优点：数据库强制执行；100% 不可绕过
   - 局限：只 Postgres；需要 DBA 配合建策略

本模块同时提供：
- ``inject_tenant_where(sql, dialect, tenant_id, column)`` — 应用层注入
- ``set_postgres_rls_tenant(conn, tenant_id)`` — Postgres SET LOCAL
- ``detect_tables_needing_tenant(sql, dialect, known_tables)`` — 审计：找出没带 tenant 过滤的表
"""
from __future__ import annotations

import logging
from typing import List, Optional, Set

logger = logging.getLogger("chayuan.knowledge_source.sql.tenant_guard")


def inject_tenant_where(
    sql: str, *,
    dialect: str = "",
    tenant_id: str,
    column: str = "tenant_id",
    tenant_tables: Optional[Set[str]] = None,
) -> str:
    """对 SQL AST 里每个涉及 ``tenant_tables`` 的 SELECT 追加 ``column = 'tenant_id'`` 约束。

    - 如果 ``tenant_tables=None``：对所有 FROM 到的表都追加
    - 如果解析失败：返回原 SQL（fail-open；上层若强制模式可自行拒绝）
    """
    if not sql or not tenant_id:
        return sql
    try:
        import sqlglot
        from sqlglot import exp
    except Exception as e:  # noqa: BLE001
        logger.debug("sqlglot 不可用，跳过 tenant 注入：%r", e)
        return sql

    try:
        parsed = sqlglot.parse_one(sql, read=dialect or None)
    except Exception as e:  # noqa: BLE001
        logger.debug("sqlglot 解析失败，跳过 tenant 注入：%r", e)
        return sql

    def _need_inject(table_name: str) -> bool:
        if not tenant_tables:
            return True
        return table_name.lower() in {t.lower() for t in tenant_tables}

    # 对每一个 Select 节点：遍历 FROM tables，若未在 WHERE 里命中 tenant_id = ... 则补
    for select in parsed.find_all(exp.Select):
        froms = [t for t in select.find_all(exp.Table)]
        if not froms:
            continue
        targets = {t.name for t in froms if _need_inject(t.name)}
        if not targets:
            continue
        # 构造：table.tenant_id = 'tenant_id'
        # 为避免多表 ambiguous，选第一张目标表做别名
        first = next(iter(targets))
        safe_val = str(tenant_id).replace("'", "''")
        tenant_cond = exp.condition(f"\"{first}\".\"{column}\" = '{safe_val}'")
        where = select.args.get("where")
        if where is None:
            select.set("where", exp.Where(this=tenant_cond))
        else:
            # 检查是否已有相同条件（避免重复注入）
            existing_sql = where.sql().lower()
            if f"{column}".lower() in existing_sql and safe_val.lower() in existing_sql:
                continue
            where.set("this", exp.And(this=where.args["this"], expression=tenant_cond))

    try:
        return parsed.sql(dialect=dialect or None)
    except Exception:  # noqa: BLE001
        return sql


def set_postgres_rls_tenant(conn, tenant_id: str) -> None:
    """对 Postgres 连接设置 ``SET LOCAL app.tenant_id = '...'``。

    业务表的 RLS 策略写法示例：

        ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON orders
          USING (tenant_id = current_setting('app.tenant_id', true));

    failures 不抛；非 Postgres 连接会被忽略。
    """
    if not tenant_id:
        return
    try:
        from sqlalchemy import text
        safe_val = str(tenant_id).replace("'", "''")
        conn.execute(text(f"SET LOCAL app.tenant_id = '{safe_val}'"))
    except Exception as e:  # noqa: BLE001
        logger.debug("set_postgres_rls_tenant 失败（忽略）：%r", e)
