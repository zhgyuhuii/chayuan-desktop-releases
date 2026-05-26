"""数据治理（P1-9）持久化模型。

四表：
- governance_policy            策略：PII 级别 / 脱敏规则 / 配额上限（按 user_role 或 user_id）
- chayuan_lineage              血缘：每次 chat 都有一条（含 sources / 命中 chunk / 生成 SQL）
- chayuan_lineage_touch        血缘子表：细粒度的表/列/文件命中记录
- chayuan_usage_counter        累计用量：token / qps 分桶（用于展示与日报；实时配额走 Redis）

大规模部署建议把 lineage / usage_counter 切到 ClickHouse / OpenSearch；此处
表结构设计为"扁平可迁移"，便于后续导出。
"""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel
from sqlalchemy import (
    Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)

from chayuan.server.db.base import Base


class GovernancePolicyModel(Base):
    """策略主表。scope = user:<id> / role:<name> / global。

    一条策略可同时定义：
    - daily_token_budget：每日 token 上限
    - qps：每秒请求上限
    - masking_level：strict / loose / off
    - allowed_modes：JSON 数组，限制可用 mode
    """

    __tablename__ = "governance_policy"
    __table_args__ = (
        UniqueConstraint("scope", name="uq_gov_policy_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(64), nullable=False, index=True,
                    comment="user:123 / role:admin / role:analyst / global")
    daily_token_budget = Column(Integer, default=-1, comment="-1 表示不限")
    qps = Column(Integer, default=-1, comment="每秒请求上限；-1 不限")
    masking_level = Column(String(16), default="loose", comment="strict/loose/off")
    allowed_modes_json = Column(Text, default="")
    # 策略生效的其它自定义键值（JSON）
    extra_json = Column(Text, default="")
    enabled = Column(Integer, default=1)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class LineageRecordModel(Base):
    """每次 chat 的血缘主记录。"""

    __tablename__ = "chayuan_lineage"
    __table_args__ = (
        Index("ix_lineage_user_time", "user_id", "created_at"),
        Index("ix_lineage_mode_time", "mode", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(128), default="")
    conversation_id = Column(String(64), default="", index=True)
    request_id = Column(String(64), default="")
    mode = Column(String(32), default="", index=True,
                  comment="llm/kb/file/search_engine/multi_source/agent/vision")
    llm_model = Column(String(80), default="")
    tokens_total = Column(Integer, default=0)
    pii_count = Column(Integer, default=0, comment="输出中识别到的 PII 条数")
    query = Column(Text, default="")
    answer_preview = Column(Text, default="", comment="截断到 500 字节，避免超长")
    sources_json = Column(Text, default="", comment="命中的源 meta JSON")
    created_at = Column(DateTime, default=func.now(), index=True)


class LineageTouchModel(Base):
    """血缘细粒度：一次 chat 里真正被读到的表/列/文件。

    例子：Text2SQL 命中 `products.id, products.name` → 两条记录。
    场景：合规审计、"哪个字段最常被 AI 读到"。
    """

    __tablename__ = "chayuan_lineage_touch"
    __table_args__ = (
        Index("ix_touch_lineage", "lineage_id"),
        Index("ix_touch_object", "object_type", "object_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    lineage_id = Column(Integer, nullable=False, index=True)
    source_id = Column(Integer, nullable=True)
    object_type = Column(String(16), default="", comment="table/column/file/collection/index")
    object_name = Column(String(255), default="", comment="products / products.price / a.pdf ...")
    qualified_name = Column(String(512), default="", comment="source:db.schema.table[.column]")
    created_at = Column(DateTime, default=func.now())


class UsageCounterModel(Base):
    """按天聚合的累计用量；供面板展示 & 月度报表。

    实时配额判定不走这张表（Redis 令牌桶足够快）；本表是"冷侧"归档。
    """

    __tablename__ = "chayuan_usage_counter"
    __table_args__ = (
        UniqueConstraint("user_id", "date_bucket", "mode", name="uq_usage_user_date_mode"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    date_bucket = Column(String(10), nullable=False, comment="YYYY-MM-DD")
    mode = Column(String(32), default="")
    request_count = Column(Integer, default=0)
    tokens_total = Column(Integer, default=0)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class GovernancePolicySchema(BaseModel):
    id: int
    scope: str
    daily_token_budget: int = -1
    qps: int = -1
    masking_level: str = "loose"
    allowed_modes_json: Optional[str] = ""
    extra_json: Optional[str] = ""
    enabled: int = 1
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LineageRecordSchema(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = ""
    conversation_id: Optional[str] = ""
    mode: Optional[str] = ""
    llm_model: Optional[str] = ""
    tokens_total: int = 0
    pii_count: int = 0
    query: Optional[str] = ""
    answer_preview: Optional[str] = ""
    sources_json: Optional[str] = ""
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
