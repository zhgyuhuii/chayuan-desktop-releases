"""GraphRAG 数据模型。

三表结构（kb_name 粒度）：
- graphrag_entity     实体：name / type / description / chunk_id（源 chunk）
- graphrag_relation   关系：src_id / dst_id / type / description / chunk_id
- graphrag_community  社区：level / members_json / summary_doc_id（向量库里的摘要 id）

社区摘要本身**不**存本表；存的是指向向量库的 ``summary_doc_id``，摘要文本以普通
Document（metadata.graphrag_type=community）形式存在 KB 的向量库里，这样查询时
向量召回自动覆盖它们。

对于 10k 级 chunks 的 KB，entity 规模约 50k-200k；关系 200k-500k。SQLite 能扛。
超过 100 万级建议切 Postgres + 对 name 建倒排索引。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import (
    Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)

from chayuan.server.db.base import Base


class GraphEntityModel(Base):
    __tablename__ = "graphrag_entity"
    __table_args__ = (
        Index("ix_ge_kb_name", "kb_name", "name"),
        UniqueConstraint("kb_name", "name", name="uq_ge_kb_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_name = Column(String(80), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    entity_type = Column(String(64), default="", comment="PERSON / ORG / GPE / PRODUCT / CONCEPT ...")
    description = Column(Text, default="")
    # 允许一个实体来自多个 chunk；这里只保留"第一次出现"的 chunk，节省空间
    first_seen_chunk_id = Column(String(128), default="")
    mention_count = Column(Integer, default=1)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class GraphRelationModel(Base):
    __tablename__ = "graphrag_relation"
    __table_args__ = (
        Index("ix_gr_kb", "kb_name"),
        Index("ix_gr_src", "src_entity_id"),
        Index("ix_gr_dst", "dst_entity_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_name = Column(String(80), nullable=False, index=True)
    src_entity_id = Column(Integer, nullable=False)
    dst_entity_id = Column(Integer, nullable=False)
    relation_type = Column(String(64), default="")
    description = Column(Text, default="")
    weight = Column(Integer, default=1, comment="关系在语料中出现次数；用于图中边权")
    source_chunk_id = Column(String(128), default="")
    created_at = Column(DateTime, default=func.now())


class GraphCommunityModel(Base):
    __tablename__ = "graphrag_community"
    __table_args__ = (
        Index("ix_gc_kb_level", "kb_name", "level"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_name = Column(String(80), nullable=False, index=True)
    level = Column(Integer, default=0, comment="Louvain 层级（目前只用 0）")
    community_key = Column(String(32), default="",
                           comment="Louvain 输出的社区编号（字符串，兼容多算法）")
    members_json = Column(Text, default="",
                           comment="entity_id[] JSON；查询时 fast-lookup")
    summary = Column(Text, default="", comment="LLM 生成的社区摘要正文")
    summary_doc_id = Column(String(128), default="",
                             comment="向量库里对应的 Document.id，用于向量召回")
    created_at = Column(DateTime, default=func.now())


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class GraphEntitySchema(BaseModel):
    id: int
    kb_name: str
    name: str
    entity_type: Optional[str] = ""
    description: Optional[str] = ""
    mention_count: int = 1
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
