"""新增 ``model_metadata`` 表 —— LLM 自动补全的"模型简介/发布日期/性能" 等元信息。

每行 = (platform_name, model_id) 唯一对;由 admin 在 SettingsDialog "AI 补全简介"
触发后端 enrich 端点写入,前端 catalog 接口顺带 join 出来给卡片 hover tooltip。

Revision ID: 0005_model_metadata
Revises: 0004_model_platform
Create Date: 2026-04-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_model_metadata"
down_revision = "0004_model_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_metadata",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("platform_name", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        # —— LLM 抽取出的字段(允许为空,LLM 不知道时给 null) ——
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("release_date", sa.String(length=32), nullable=True,
                  comment="发布日期(YYYY-MM 或 YYYY-MM-DD)"),
        sa.Column("context_length", sa.Integer(), nullable=True,
                  comment="上下文窗口 tokens 数;LLM 不知道时为 null"),
        sa.Column("performance_note", sa.Text(), nullable=True,
                  comment="性能/能力一句话评价"),
        # —— 元信息 ——
        sa.Column("source", sa.String(length=32), nullable=False, server_default="llm",
                  comment="来源:llm(默认) / manual(用户手填) / vendor_doc(后续可扩展)"),
        sa.Column("source_model", sa.String(length=128), nullable=True,
                  comment="生成时使用的 LLM 模型 id;便于追溯版本"),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("update_time", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_name", "model_id", name="uq_model_metadata_pk"),
    )
    op.create_index(
        "ix_model_metadata_platform_name", "model_metadata", ["platform_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_metadata_platform_name", table_name="model_metadata")
    op.drop_table("model_metadata")
