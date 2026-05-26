"""新增 ``model_platform`` 表，把 yaml ``MODEL_PLATFORMS`` 迁到 DB。

DB 行是主权威源，yaml 仍作为 seed/fallback；写后由 ``model_platform_repository``
``bump_platform_version()`` 让 ``server/utils.get_config_platforms`` 的 5s TTL
缓存即时失效。

Revision ID: 0004_model_platform
Revises: 0003_enable_rls
Create Date: 2026-04-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_model_platform"
down_revision = "0003_enable_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_platform",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("platform_name", sa.String(length=64), nullable=False),
        sa.Column("platform_type", sa.String(length=32), nullable=False, server_default="custom openai"),
        sa.Column("api_base_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("api_key", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("api_proxy", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("api_concurrencies", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_detect_model", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("llm_models", sa.JSON(), nullable=False),
        sa.Column("embed_models", sa.JSON(), nullable=False),
        sa.Column("text2image_models", sa.JSON(), nullable=False),
        sa.Column("image2text_models", sa.JSON(), nullable=False),
        sa.Column("image2image_models", sa.JSON(), nullable=False),
        sa.Column("rerank_models", sa.JSON(), nullable=False),
        sa.Column("speech2text_models", sa.JSON(), nullable=False),
        sa.Column("text2speech_models", sa.JSON(), nullable=False),
        sa.Column("disabled_models", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("update_time", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("create_by", sa.String(length=64), nullable=True),
        sa.Column("update_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_name", name="uq_model_platform_name"),
    )
    op.create_index(
        "ix_model_platform_enabled", "model_platform", ["enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_platform_enabled", table_name="model_platform")
    op.drop_table("model_platform")
