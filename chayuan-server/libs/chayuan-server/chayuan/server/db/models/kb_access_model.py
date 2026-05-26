"""知识库访问授权表。

`knowledge_base.visibility` 语义：
- `private`：只有 owner 与被授权用户（本表）可见；
- `public`：所有已登录用户可读；写仍受授权表约束（或 admin）。

本表仅存**显式授权**，owner 与 admin 不依赖本表。
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

from chayuan.server.db.base import Base


class KBAccessGrantModel(Base):
    __tablename__ = "kb_access_grants"
    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_kb_access_kb_user"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(Integer, nullable=False, index=True, comment="knowledge_base.id")
    user_id = Column(Integer, nullable=False, index=True, comment="users.id")
    role = Column(
        String(16), default="reader", nullable=False,
        comment="授权角色：reader / editor；owner 由 knowledge_base.owner_id 直接给",
    )
    granted_at = Column(DateTime, default=func.now())
    granted_by = Column(Integer, nullable=True, comment="授予者 users.id；系统自动授权为 NULL")

    def __repr__(self):
        return f"<KBGrant(kb_id={self.kb_id}, user_id={self.user_id}, role={self.role})>"
