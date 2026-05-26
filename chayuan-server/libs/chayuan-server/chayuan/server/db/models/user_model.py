"""用户模型。

最小字段集：
- id: 内部数字 id；
- username: 登录名；全库唯一；
- email: 可选，仅用于找回密码场景（现阶段没实现也无妨）；
- password_hash: bcrypt hash（或 scrypt 兜底），永远不存明文；
- role: 'admin' | 'user'；admin 跨租户可见全部资源；
- is_active: 禁用账号时置 0；
- created_at / updated_at / last_login_at。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from chayuan.server.db.base import Base


class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="用户ID")
    username = Column(String(64), unique=True, nullable=False, index=True, comment="登录名")
    email = Column(String(128), unique=False, nullable=True, comment="邮箱（可选）")
    password_hash = Column(String(255), nullable=False, comment="密码哈希")
    role = Column(String(16), default="user", nullable=False, comment="角色：admin/user")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")

    created_at = Column(DateTime, default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")
    last_login_at = Column(DateTime, nullable=True, comment="最近登录时间")

    def __repr__(self):
        return (
            f"<User(id={self.id}, username='{self.username}', role='{self.role}', "
            f"active={self.is_active})>"
        )


class UserPublicSchema(BaseModel):
    """暴露给前端的用户字段（不含 password_hash）。"""

    id: int
    username: str
    email: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True
