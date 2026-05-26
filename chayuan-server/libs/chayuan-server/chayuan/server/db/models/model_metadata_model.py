"""模型元数据 ORM —— LLM 自动补全的 "模型简介 / 发布日期 / 性能" 等字段。

每行 = (platform_name, model_id) 唯一对;由 admin 在 SettingsDialog 触发的
``/admin/model_platforms/{name}/enrich`` 端点写入,前端 catalog 接口顺带 JOIN
出来给卡片 hover tooltip 用。

source 字段记录来源:
  - llm: 由内部 LLM 自动生成(默认)
  - manual: 用户手填(预留)
  - vendor_doc: 解析厂商官方文档(预留)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from chayuan.server.db.base import Base


class ModelMetadataModel(Base):
    """模型元数据(简介 / 发布日期 / 上下文长度 / 性能短评)。"""

    __tablename__ = "model_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    platform_name = Column(String(64), nullable=False, comment="所属厂商 platform_name")
    model_id = Column(String(128), nullable=False, comment="模型 id(去重 key 的另一半)")

    description = Column(Text, nullable=True, comment="一段中文简介(2-4 句)")
    release_date = Column(
        String(32), nullable=True, comment="发布日期(YYYY-MM 或 YYYY-MM-DD;不知道为 null)",
    )
    context_length = Column(Integer, nullable=True, comment="上下文窗口 tokens 数")
    performance_note = Column(Text, nullable=True, comment="性能 / 能力一句话评价")

    source = Column(String(32), nullable=False, default="llm",
                    comment="来源: llm(默认) / manual / vendor_doc")
    source_model = Column(String(128), nullable=True, comment="生成时使用的 LLM 模型 id")

    create_time = Column(DateTime, default=func.now(), comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform_name": self.platform_name,
            "model_id": self.model_id,
            "description": self.description or "",
            "release_date": self.release_date or "",
            "context_length": int(self.context_length) if self.context_length else None,
            "performance_note": self.performance_note or "",
            "source": self.source or "llm",
            "source_model": self.source_model or "",
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }


__all__ = ["ModelMetadataModel"]
