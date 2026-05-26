"""Alembic 环境脚本。

关键点：
- ``SQLALCHEMY_DATABASE_URI`` 不再从 ``alembic.ini`` 读，而是动态从 ``Settings`` 取，
  避免开发 / 容器 / 生产三套环境分别维护 alembic.ini
- 导入 ``chayuan.server.db.models`` 触发全部 ORM 模型注册，让 ``target_metadata``
  拿到最新 schema，支持 ``alembic revision --autogenerate``
- ``compare_type=True`` + ``compare_server_default=True`` 让列类型 / 默认值变更也能被 diff 出来
"""
from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

logger = logging.getLogger("chayuan.alembic")

# alembic.ini 的 [alembic] 段
config = context.config
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # noqa: BLE001
        pass

# 动态注入 sqlalchemy.url
try:
    from chayuan.settings import Settings
    uri = Settings.basic_settings.SQLALCHEMY_DATABASE_URI
    if uri:
        config.set_main_option("sqlalchemy.url", uri)
except Exception as e:  # noqa: BLE001
    logger.warning("[alembic] 无法从 Settings 注入 URI：%r", e)

# 触发所有 ORM 模型 import，让 target_metadata 完整
try:
    from chayuan.server.db import base as _db_base  # noqa: F401
    import chayuan.server.db.models  # noqa: F401
    target_metadata = _db_base.Base.metadata
except Exception as e:  # noqa: BLE001
    logger.warning("[alembic] import models 失败：%r", e)
    target_metadata = None


def run_migrations_offline() -> None:
    """不连 DB，只打印 SQL。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True, compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """实际连 DB 跑。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True, compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
