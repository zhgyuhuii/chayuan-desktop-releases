"""审计日志。

满足"谁在何时对哪个源做了什么"的合规诉求。数据模型刻意扁平：
action / target_type / target_id 组合可覆盖 CRUD、授权、查询、下载。

超大规模部署建议将本表迁到 ClickHouse / OpenSearch 做冷热分离；保留 SQLAlchemy 模型
只作为兜底。
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from chayuan.server.db.base import Base


class AuditLogModel(Base):
    __tablename__ = "chayuan_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(128), default="")
    request_id = Column(String(64), default="", index=True)
    action = Column(String(64), nullable=False, index=True,
                    comment="source.create / source.delete / source.grant / "
                            "source.search / source.download / source.introspect ...")
    target_type = Column(String(32), default="", comment="source / kb / user ...")
    target_id = Column(String(64), default="")
    status = Column(String(16), default="ok", comment="ok / error / denied")
    # 结构化 payload（JSON 字符串）：脱敏后的入参摘要
    payload = Column(Text, default="")
    # 结构化 result（JSON）：输出摘要，仅保留体积小 / 可公开的字段
    result = Column(Text, default="")
    error_msg = Column(Text, default="")
    elapsed_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now(), index=True)
