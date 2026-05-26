"""配置中心 ORM 模型。

- ``ConfigEntry``：当前值表，``(namespace, key)`` 唯一键。``value`` 列用
  JSON 序列化（SQLite / Postgres / MySQL 都支持 TEXT 储 json 字符串，跨 DB 最稳）。
- ``ConfigHistory``：每次写入都落一行；保留完整变更历史，便于排障 / 回滚 / 审计。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from chayuan.server.db.base import Base


class ConfigEntry(Base):
    __tablename__ = "chayuan_config"
    __table_args__ = (
        UniqueConstraint("namespace", "key", name="uq_config_ns_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    namespace = Column(String(64), nullable=False, index=True)
    key = Column(String(255), nullable=False, index=True)
    # JSON 字符串；读出后用 json.loads。给运维/DBA 用 SQL 查值时也是人类可读的。
    value = Column(Text, nullable=False, default="null")
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(128), nullable=False, default="system")
    comment = Column(String(500), nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConfigHistory(Base):
    __tablename__ = "chayuan_config_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    namespace = Column(String(64), nullable=False, index=True)
    key = Column(String(255), nullable=False, index=True)
    value = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    updated_by = Column(String(128), nullable=False, default="system")
    comment = Column(String(500), nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)
