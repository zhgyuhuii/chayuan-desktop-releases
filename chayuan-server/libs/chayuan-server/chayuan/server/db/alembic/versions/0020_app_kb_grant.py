"""KB ACL：把开放平台 App 授权到具体知识库（plan v1.3 §4.3）。

设计要点（与 ``kb_access_grants`` 命名/语义对齐）：
* ``kb_access_grants``：人类用户 → KB 的授权（已存在）
* ``app_kb_grant``：开放平台 App → KB 的授权（本表，**新增**）

为什么 ``app_id`` 是 ``String`` 而不是 FK：
* AppSpec 持久化在 ``CHAYUAN_ROOT/apps.yaml`` + config_center DB（``chayuan.server.config_panel.apps_store``），
  不在主业务库；这里只能持有字符串引用。删除 App 时要由 admin 端点显式清理本表
  （``apps_store.delete_app`` 完成后调 ``app_acl.purge_app_grants(app_id)``）。

为什么 ``kb_id`` 用 INT FK：
* knowledge_base 是主业务表，``kb_access_grants.kb_id`` 也是 INT；保持一致便于做 JOIN。
* 用 KB id 而不是 kb_name：kb 改名（rare）时 grant 不破。

Revision ID: 0020_app_kb_grant
Revises: 0019_data_mount
Create Date: 2026-05-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_app_kb_grant"
down_revision = "0019_data_mount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_kb_grant",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("app_id", sa.String(length=64), nullable=False,
                  comment="apps_store.AppSpec.app_id（32 hex），不做 FK 因 App 存 yaml/config_center"),
        sa.Column("kb_id", sa.Integer(), nullable=False,
                  comment="knowledge_base.id"),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="reader",
                  comment="reader / editor；owner 永不分配给 App（应用账号不能拥有 KB）"),
        sa.Column("granted_by", sa.Integer(), nullable=True,
                  comment="授予者 users.id；管理员通过 /openapi/v1/admin/apps/.../kb_grants 创建"),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True,
                  comment="可选过期；NULL=永久；过期 grant 由日常 cron 清理"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "kb_id", name="uq_app_kb_grant_app_kb"),
    )
    op.create_index("ix_app_kb_grant_app_id", "app_kb_grant", ["app_id"])
    op.create_index("ix_app_kb_grant_kb_id", "app_kb_grant", ["kb_id"])
    op.create_index("ix_app_kb_grant_expires_at", "app_kb_grant", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_app_kb_grant_expires_at", table_name="app_kb_grant")
    op.drop_index("ix_app_kb_grant_kb_id", table_name="app_kb_grant")
    op.drop_index("ix_app_kb_grant_app_id", table_name="app_kb_grant")
    op.drop_table("app_kb_grant")
