"""noop sentinel：为 0001_baseline 之后建立一个空的 revision 哨兵。

为什么要这个空 migration？
- baseline 本身是"把 Base.metadata 落地"的宽泛步骤；生产首次 ``alembic upgrade head``
  后 ``alembic current`` 会停在 0001。后续真正的 schema 变更 migration（例如 T9 的 RLS）
  依赖 ``down_revision`` 链，此哨兵提供一个稳定锚点，避免未来第一条业务 migration
  直接挂在 baseline 上时 reviewer / DBA 分不清 "schema 已建" vs "schema 未动"。
- 同时验证 alembic 版本链可以递增：CI 里跑 ``alembic upgrade head`` 到 0002 全无操作，
  证明 env / config 正常。

Revision ID: 0002_noop_sentinel
Revises: 0001_baseline
Create Date: 2026-04-24
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0002_noop_sentinel"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # intentionally empty — see module docstring
    pass


def downgrade() -> None:
    # intentionally empty — see module docstring
    pass
