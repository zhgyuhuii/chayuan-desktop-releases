"""模型平台配置 ORM。

把原本只在 ``model_settings.yaml`` 里的 ``MODEL_PLATFORMS`` 落库，让 admin 在线
新增/编辑/删除厂商成为可能；写入后通过 ``model_platform_repository`` 里的
``bump_platform_version()`` 让 ``server/utils.get_config_platforms`` 的 5s TTL
缓存即时失效，避免重启服务。

字段语义与 ``chayuan.settings.PlatformConfig`` 对齐——前端拿到 row 直接套到表单上。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, func

from chayuan.server.db.base import Base


class ModelPlatformModel(Base):
    """模型平台配置（一行 = 一个厂商，与 yaml 里 ``MODEL_PLATFORMS[i]`` 一一对应）。"""

    __tablename__ = "model_platform"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    platform_name = Column(String(64), unique=True, nullable=False, comment="平台名称（唯一）")
    platform_type = Column(
        String(32), nullable=False, default="custom openai",
        comment="平台类型：xinference / ollama / oneapi / fastchat / openai / custom openai",
    )

    api_base_url = Column(String(512), nullable=False, default="", comment="OpenAI 兼容 base url")
    api_key = Column(String(512), nullable=False, default="", comment="API Key")
    api_proxy = Column(String(256), nullable=False, default="", comment="HTTP 代理")
    api_concurrencies = Column(Integer, nullable=False, default=5, comment="单模型并发上限")

    enabled = Column(Boolean, nullable=False, default=True, comment="厂商总开关")
    auto_detect_model = Column(
        Boolean, nullable=False, default=False, comment="是否自动检测可用模型",
    )

    llm_models = Column(JSON, nullable=False, default=list, comment="LLM 模型清单")
    embed_models = Column(JSON, nullable=False, default=list, comment="嵌入模型清单")
    text2image_models = Column(JSON, nullable=False, default=list, comment="文生图清单")
    image2text_models = Column(JSON, nullable=False, default=list, comment="多模态清单")
    image2image_models = Column(JSON, nullable=False, default=list, comment="图生图清单")
    rerank_models = Column(JSON, nullable=False, default=list, comment="重排清单")
    speech2text_models = Column(JSON, nullable=False, default=list, comment="STT 清单")
    text2speech_models = Column(JSON, nullable=False, default=list, comment="TTS 清单")
    disabled_models = Column(JSON, nullable=False, default=list, comment="模型黑名单")

    description = Column(Text, nullable=True, comment="厂商描述（前端 hero 展示）")
    extra = Column(JSON, nullable=True, comment="扩展字段，留作未来兼容")

    create_time = Column(DateTime, default=func.now(), comment="创建时间")
    update_time = Column(
        DateTime, default=func.now(), onupdate=func.now(), comment="更新时间",
    )
    create_by = Column(String(64), nullable=True, comment="创建者")
    update_by = Column(String(64), nullable=True, comment="更新者")

    def to_dict(self) -> Dict[str, Any]:
        """返回前端 / get_config_platforms 可直接消费的 dict。"""
        return {
            "platform_name": self.platform_name,
            "platform_type": self.platform_type,
            "api_base_url": self.api_base_url or "",
            "api_key": self.api_key or "",
            "api_proxy": self.api_proxy or "",
            "api_concurrencies": int(self.api_concurrencies or 5),
            "enabled": bool(self.enabled),
            "auto_detect_model": bool(self.auto_detect_model),
            "llm_models": list(self.llm_models or []),
            "embed_models": list(self.embed_models or []),
            "text2image_models": list(self.text2image_models or []),
            "image2text_models": list(self.image2text_models or []),
            "image2image_models": list(self.image2image_models or []),
            "rerank_models": list(self.rerank_models or []),
            "speech2text_models": list(self.speech2text_models or []),
            "text2speech_models": list(self.text2speech_models or []),
            "disabled_models": list(self.disabled_models or []),
            "description": self.description or "",
            "extra": self.extra or {},
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }
