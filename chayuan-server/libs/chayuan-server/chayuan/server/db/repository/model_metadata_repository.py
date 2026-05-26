"""model_metadata DB CRUD —— 简介 / 发布日期 / 上下文长度 / 性能短评。

只暴露最常用的 4 个操作:list_for_platform / get / upsert / delete_one。
没有"版本号缓存"概念,因为元数据是只读 hot path 的副信息,前端 catalog
拿到时已经被一次性 join 出来,后续靠 RQ staleTime 自然过期。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from chayuan.server.db.models.model_metadata_model import ModelMetadataModel
from chayuan.server.db.session import session_scope

logger = logging.getLogger("chayuan.repository.model_metadata")


def list_for_platform(platform_name: str) -> List[Dict[str, Any]]:
    with session_scope() as s:
        rows = (
            s.query(ModelMetadataModel)
            .filter(ModelMetadataModel.platform_name == platform_name)
            .all()
        )
        return [r.to_dict() for r in rows]


def list_all() -> List[Dict[str, Any]]:
    """全量快照,catalog 端点一次拉,前端按 (platform, model_id) 索引查找。"""
    with session_scope() as s:
        rows = s.query(ModelMetadataModel).all()
        return [r.to_dict() for r in rows]


def get(platform_name: str, model_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as s:
        row = (
            s.query(ModelMetadataModel)
            .filter(
                ModelMetadataModel.platform_name == platform_name,
                ModelMetadataModel.model_id == model_id,
            )
            .one_or_none()
        )
        return row.to_dict() if row else None


def upsert(
    *,
    platform_name: str,
    model_id: str,
    description: Optional[str] = None,
    release_date: Optional[str] = None,
    context_length: Optional[int] = None,
    performance_note: Optional[str] = None,
    source: str = "llm",
    source_model: Optional[str] = None,
) -> Dict[str, Any]:
    """存在则覆盖(以本次传入字段为准;未传字段保留旧值)。"""
    with session_scope() as s:
        row = (
            s.query(ModelMetadataModel)
            .filter(
                ModelMetadataModel.platform_name == platform_name,
                ModelMetadataModel.model_id == model_id,
            )
            .one_or_none()
        )
        if row is None:
            row = ModelMetadataModel(
                platform_name=platform_name,
                model_id=model_id,
                description=description,
                release_date=release_date,
                context_length=context_length,
                performance_note=performance_note,
                source=source,
                source_model=source_model,
            )
            s.add(row)
        else:
            if description is not None:
                row.description = description
            if release_date is not None:
                row.release_date = release_date
            if context_length is not None:
                row.context_length = context_length
            if performance_note is not None:
                row.performance_note = performance_note
            if source:
                row.source = source
            if source_model is not None:
                row.source_model = source_model
            row.update_time = datetime.utcnow()
        s.flush()
        return row.to_dict()


def delete_one(platform_name: str, model_id: str) -> bool:
    with session_scope() as s:
        row = (
            s.query(ModelMetadataModel)
            .filter(
                ModelMetadataModel.platform_name == platform_name,
                ModelMetadataModel.model_id == model_id,
            )
            .one_or_none()
        )
        if row is None:
            return False
        s.delete(row)
        return True
