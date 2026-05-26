"""T9：为 chayuan 自身核心表启用 Postgres RLS。

对象表：
- conversation（会话）
- message（消息）
- knowledge_base
- knowledge_file
- knowledge_source
- audit_log

行为：
- **仅 Postgres 后端执行 DDL**。SQLite / MySQL 跳过整个 migration（RLS 是 PG 专属特性）。
- 给每张表加 ``tenant_id VARCHAR(64) NULL``（nullable 保证历史数据不破），并索引。
- 启用 ``ROW LEVEL SECURITY``，配合 **permissive** 策略：
    USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true))
  - ``tenant_id IS NULL`` 让历史数据（legacy）在 RLS 打开后仍可见——避免数据突然消失
  - 新写入的数据由应用层（AuthContext / TenantContextMiddleware）填充 tenant_id

配套运行时：
- 每个请求在 session 打开后 ``SET LOCAL app.tenant_id = '<tenant>'``
- SQL Connector 对外部用户数据源仍走 ``inject_tenant_where`` AST 注入（双保险）

Revision ID: 0003_enable_rls
Revises: 0002_noop_sentinel
Create Date: 2026-04-24
"""
from __future__ import annotations

import logging
from alembic import op
import sqlalchemy as sa

revision = "0003_enable_rls"
down_revision = "0002_noop_sentinel"
branch_labels = None
depends_on = None

logger = logging.getLogger("chayuan.alembic.0003")


_TABLES = [
    "conversation",
    "message",
    "knowledge_base",
    "knowledge_file",
    "knowledge_source",
    "audit_log",
]


def _is_postgres() -> bool:
    try:
        return op.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001
        return False


def upgrade() -> None:
    if not _is_postgres():
        logger.info("[0003_enable_rls] 非 Postgres 后端，跳过 RLS DDL")
        return

    conn = op.get_bind()
    for tbl in _TABLES:
        # 1) 表可能不存在（例如 audit_log 由 governance 动态建）：先 try
        exists = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        ), {"t": tbl}).first()
        if not exists:
            logger.info("[0003_enable_rls] 表 %s 不存在，跳过", tbl)
            continue

        # 2) 补 tenant_id 列（nullable，带索引）
        has_col = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'tenant_id'"
        ), {"t": tbl}).first()
        if not has_col:
            conn.execute(sa.text(
                f'ALTER TABLE "{tbl}" ADD COLUMN tenant_id VARCHAR(64) NULL'
            ))
            conn.execute(sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_{tbl}_tenant_id '
                f'ON "{tbl}" (tenant_id)'
            ))

        # 3) 启用 RLS + 建策略（策略名按表生成，幂等）
        conn.execute(sa.text(f'ALTER TABLE "{tbl}" ENABLE ROW LEVEL SECURITY'))
        # FORCE RLS：对表 owner 也生效，避免超级用户误越权
        conn.execute(sa.text(f'ALTER TABLE "{tbl}" FORCE ROW LEVEL SECURITY'))

        policy_name = f"tenant_isolation_{tbl}"
        # 幂等：先 DROP IF EXISTS 再 CREATE
        conn.execute(sa.text(
            f'DROP POLICY IF EXISTS {policy_name} ON "{tbl}"'
        ))
        conn.execute(sa.text(
            f'CREATE POLICY {policy_name} ON "{tbl}" '
            f"USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true))"
        ))


def downgrade() -> None:
    if not _is_postgres():
        return
    conn = op.get_bind()
    for tbl in _TABLES:
        try:
            conn.execute(sa.text(
                f'DROP POLICY IF EXISTS tenant_isolation_{tbl} ON "{tbl}"'
            ))
            conn.execute(sa.text(f'ALTER TABLE "{tbl}" DISABLE ROW LEVEL SECURITY'))
        except Exception as e:  # noqa: BLE001
            logger.debug("[0003_enable_rls] downgrade %s 失败（忽略）：%r", tbl, e)
        # 不删 tenant_id 列（避免丢数据）；需要时运维手工 ALTER TABLE DROP COLUMN
