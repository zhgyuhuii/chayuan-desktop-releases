"""开放平台 App → 知识库 授权（plan v1.3 §4.3 / 0020_app_kb_grant）。

与 ``KBAccessGrantModel``（用户→KB）平行；本表是 App→KB。
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from chayuan.server.db.base import Base


class AppKBGrantModel(Base):
    __tablename__ = "app_kb_grant"
    __table_args__ = (
        UniqueConstraint("app_id", "kb_id", name="uq_app_kb_grant_app_kb"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(String(64), nullable=False, index=True,
                    comment="apps_store.AppSpec.app_id（32 hex）")
    kb_id = Column(Integer, nullable=False, index=True,
                   comment="knowledge_base.id")
    role = Column(String(16), nullable=False, default="reader",
                  comment="reader / editor；owner 不可分配")
    granted_by = Column(Integer, nullable=True)
    granted_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=True, index=True)

    def __repr__(self) -> str:
        return (f"<AppKBGrant(app_id={self.app_id!r}, kb_id={self.kb_id}, "
                f"role={self.role!r}, expires={self.expires_at})>")
