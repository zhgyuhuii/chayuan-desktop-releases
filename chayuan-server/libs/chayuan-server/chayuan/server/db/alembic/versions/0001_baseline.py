"""baseline：把现有 Base.metadata 的全部表落进 alembic 版本链。

此 migration 的 upgrade 路径用 ``Base.metadata.create_all`` 直接创建；对已经有
``create_all`` 历史的库无危害（``checkfirst=True`` 幂等），对新库等于首次建表。
后续任何 schema 变更都**必须**通过 ``alembic revision --autogenerate -m "<why>"``
产出新 migration，不再在 Base 里改模型就完事。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 从 ORM metadata 重现全部表。此路径与开发侧 ``create_tables()`` 等价。
    # 生产上首次运行会创建全部表；若 DB 已有同名表，``checkfirst=True``（默认）
    # 保证幂等——只补缺的。
    from chayuan.server.db.base import Base
    import chayuan.server.db.models  # noqa: F401，触发 ORM 注册

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from chayuan.server.db.base import Base
    import chayuan.server.db.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
