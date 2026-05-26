"""Text2SQL 训练语料表（Vanna-style RAG 的数据面）。

三类训练样本：
- ddl：建表语句 / 列注释 / 表关系（多为自动从 schema_cache 派生）
- doc：业务文档 / 术语表 / 指标定义（管理员手工上传）
- pair：(question, sql) 对（由用户手工提交 / 管理员审核通过历史成功查询）

每条记录会被 embed 到向量库（sql_training_vs），检索时按 kind 分桶取 Top-K。
ref_hash 用于幂等：同源 + 同内容 不重复落库。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func

from chayuan.server.db.base import Base


class SqlTrainingSampleModel(Base):
    __tablename__ = "sql_training_sample"
    __table_args__ = (
        Index("ix_sts_source_kind", "source_id", "kind"),
        Index("ix_sts_ref_hash", "ref_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(16), nullable=False, comment="ddl / doc / pair")
    question = Column(Text, default="", comment="kind=pair 时的自然语言问题")
    sql = Column(Text, default="", comment="kind=pair 时的参考 SQL；kind=ddl 时的 DDL 片段")
    content = Column(Text, default="", comment="kind=doc 时的业务说明；通用 metadata")
    dialect = Column(String(32), default="")
    # 是否已审核通过（只有通过的才进入 RAG 检索；默认 True，便于自动入库）
    approved = Column(Integer, default=1, comment="1=可检索 0=隐藏")
    # 使用计数 / 反馈分：未来可做权重重排
    hit_count = Column(Integer, default=0)
    feedback_score = Column(Integer, default=0, comment="用户点赞-踩之和")
    ref_hash = Column(String(64), default="", comment="source_id+kind+内容的 sha1，做幂等")
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class SqlTrainingSampleSchema(BaseModel):
    id: int
    source_id: int
    kind: str
    question: Optional[str] = ""
    sql: Optional[str] = ""
    content: Optional[str] = ""
    dialect: Optional[str] = ""
    approved: int = 1
    hit_count: int = 0
    feedback_score: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
