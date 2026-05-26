"""94-1:知识中心混合集合(kb_collection) ORM。

设计要点(决策见 ``docs/image-embedding-infinity-integration-plan.md`` 的 94 题):

* B 方案 — 不动现有 KB 表。新增两张关系表:
    - ``kb_collections``         集合本身(name / display_name / owner)
    - ``kb_collection_members``  成员关系(collection_id, ku_id, kind, sort)
* owner 跟随顶级走 — collection 有 owner_id,加成员时校验**子 KB owner 必须 = collection owner**
* 删除集合 = **级联删子 KB**(用户决策 2)
* 单 KB 同时可在 0~1 个集合里(避免重复出现)
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)

from chayuan.server.db.base import Base


class KBCollectionModel(Base):
    """图文混合集合(同 owner 名下的 doc-KB + image-KB 绑定)。"""

    __tablename__ = "kb_collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(80), unique=True, index=True, nullable=False,
                  comment="系统内唯一名,字母数字下划线")
    display_name = Column(String(120), default="",
                          comment="UI 显示名,可含中文")
    description = Column(Text, default="")
    owner_id = Column(Integer, nullable=False, index=True,
                      comment="集合 owner;子 KB owner 必须与之相同")
    visibility = Column(String(16), default="private",
                        comment="private / public(94 仅 private MVP)")
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(),
                         onupdate=func.now())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description or "",
            "owner_id": self.owner_id,
            "visibility": self.visibility,
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }


class KBCollectionMemberModel(Base):
    """集合成员关系。"""

    __tablename__ = "kb_collection_members"
    __table_args__ = (
        UniqueConstraint("ku_id", name="uq_collection_member_ku"),
        Index("ix_collection_member_collection", "collection_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(Integer, ForeignKey("kb_collections.id"),
                           nullable=False, index=True)
    ku_id = Column(String(64), nullable=False,
                   comment="knowledge_universe.ku_id;一个 ku 只能在 1 个集合")
    kind = Column(String(16), nullable=False,
                  comment="document / image,加速过滤")
    sort_order = Column(Integer, default=0,
                        comment="UI 排序;tab 内倒序按 created 排,这里管 tab 顺序")
    create_time = Column(DateTime, server_default=func.now())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "collection_id": self.collection_id,
            "ku_id": self.ku_id,
            "kind": self.kind,
            "sort_order": self.sort_order,
        }


class KBCollectionSchema(BaseModel):
    """API schema(返客户端用)。"""
    id: int
    name: str
    display_name: str
    description: str = ""
    owner_id: int
    visibility: str = "private"
    members: List[Dict[str, Any]] = []
    create_time: Optional[str] = None

    class Config:
        from_attributes = True
