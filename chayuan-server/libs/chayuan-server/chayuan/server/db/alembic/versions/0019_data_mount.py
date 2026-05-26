"""训练数据挂载运行时表。

Revision ID: 0019_data_mount
Revises: 0005_model_metadata
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_data_mount"
down_revision = "0005_model_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_mount",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("source_filter", sa.JSON(), nullable=False),
        sa.Column("mount_modes", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_items", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1600"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("update_time", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_mount_name", "data_mount", ["name"])
    op.create_index("ix_data_mount_scope_type", "data_mount", ["scope_type"])
    op.create_index("ix_data_mount_scope_id", "data_mount", ["scope_id"])
    op.create_index("ix_data_mount_priority", "data_mount", ["priority"])
    op.create_index("ix_data_mount_enabled", "data_mount", ["enabled"])
    op.create_index("ix_data_mount_status", "data_mount", ["status"])
    op.create_index("ix_data_mount_created_by", "data_mount", ["created_by"])
    op.create_index("ix_data_mount_create_time", "data_mount", ["create_time"])

    op.create_table(
        "data_mount_artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mount_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("artifact_type", sa.String(length=48), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mount_id"], ["data_mount.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_mount_artifact_mount_id", "data_mount_artifact", ["mount_id"])
    op.create_index("ix_data_mount_artifact_version", "data_mount_artifact", ["version"])
    op.create_index("ix_data_mount_artifact_artifact_type", "data_mount_artifact", ["artifact_type"])
    op.create_index("ix_data_mount_artifact_create_time", "data_mount_artifact", ["create_time"])

    op.create_table(
        "data_mount_hit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("conversation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("mount_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_type", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("sample_ids", sa.JSON(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effect_summary", sa.JSON(), nullable=False),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_mount_hit_log_request_id", "data_mount_hit_log", ["request_id"])
    op.create_index("ix_data_mount_hit_log_conversation_id", "data_mount_hit_log", ["conversation_id"])
    op.create_index("ix_data_mount_hit_log_user_id", "data_mount_hit_log", ["user_id"])
    op.create_index("ix_data_mount_hit_log_mount_id", "data_mount_hit_log", ["mount_id"])
    op.create_index("ix_data_mount_hit_log_create_time", "data_mount_hit_log", ["create_time"])


def downgrade() -> None:
    op.drop_index("ix_data_mount_hit_log_create_time", table_name="data_mount_hit_log")
    op.drop_index("ix_data_mount_hit_log_mount_id", table_name="data_mount_hit_log")
    op.drop_index("ix_data_mount_hit_log_user_id", table_name="data_mount_hit_log")
    op.drop_index("ix_data_mount_hit_log_conversation_id", table_name="data_mount_hit_log")
    op.drop_index("ix_data_mount_hit_log_request_id", table_name="data_mount_hit_log")
    op.drop_table("data_mount_hit_log")
    op.drop_index("ix_data_mount_artifact_create_time", table_name="data_mount_artifact")
    op.drop_index("ix_data_mount_artifact_artifact_type", table_name="data_mount_artifact")
    op.drop_index("ix_data_mount_artifact_version", table_name="data_mount_artifact")
    op.drop_index("ix_data_mount_artifact_mount_id", table_name="data_mount_artifact")
    op.drop_table("data_mount_artifact")
    op.drop_index("ix_data_mount_create_time", table_name="data_mount")
    op.drop_index("ix_data_mount_created_by", table_name="data_mount")
    op.drop_index("ix_data_mount_status", table_name="data_mount")
    op.drop_index("ix_data_mount_enabled", table_name="data_mount")
    op.drop_index("ix_data_mount_priority", table_name="data_mount")
    op.drop_index("ix_data_mount_scope_id", table_name="data_mount")
    op.drop_index("ix_data_mount_scope_type", table_name="data_mount")
    op.drop_index("ix_data_mount_name", table_name="data_mount")
    op.drop_table("data_mount")
