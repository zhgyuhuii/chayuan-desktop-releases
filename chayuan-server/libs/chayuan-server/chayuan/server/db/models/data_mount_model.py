"""Training-data mounts used by online chat runtime."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from chayuan.server.db.base import Base


class DataMountModel(Base):
    """A published binding between approved annotation data and runtime chat."""

    __tablename__ = "data_mount"

    id = Column(String(36), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)

    scope_type = Column(String(32), nullable=False, default="global", index=True)
    scope_id = Column(String(128), nullable=False, default="", index=True)
    source_filter = Column(JSON, nullable=False, default=dict)
    mount_modes = Column(JSON, nullable=False, default=list)

    priority = Column(Integer, nullable=False, default=0, index=True)
    max_items = Column(Integer, nullable=False, default=20)
    max_tokens = Column(Integer, nullable=False, default=1600)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    status = Column(String(24), nullable=False, default="draft", index=True)
    version = Column(Integer, nullable=False, default=1)

    created_by = Column(Integer, nullable=True, index=True)
    updated_by = Column(Integer, nullable=True)
    published_at = Column(DateTime, nullable=True)
    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "scope_type": self.scope_type,
            "scope_id": self.scope_id or "",
            "source_filter": self.source_filter or {},
            "mount_modes": list(self.mount_modes or []),
            "priority": int(self.priority or 0),
            "max_items": int(self.max_items or 0),
            "max_tokens": int(self.max_tokens or 0),
            "enabled": bool(self.enabled),
            "status": self.status,
            "version": int(self.version or 1),
            "created_by": int(self.created_by) if self.created_by is not None else None,
            "updated_by": int(self.updated_by) if self.updated_by is not None else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }


class DataMountArtifactModel(Base):
    """Materialized runtime artifact for one mount version."""

    __tablename__ = "data_mount_artifact"

    id = Column(String(36), primary_key=True)
    mount_id = Column(String(36), ForeignKey("data_mount.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1, index=True)
    artifact_type = Column(String(48), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    stats = Column(JSON, nullable=False, default=dict)
    checksum = Column(String(64), nullable=False, default="")
    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "mount_id": self.mount_id,
            "version": int(self.version or 1),
            "artifact_type": self.artifact_type,
            "payload": self.payload or {},
            "stats": self.stats or {},
            "checksum": self.checksum or "",
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }


class DataMountHitLogModel(Base):
    """Best-effort audit log for mount usage in chat."""

    __tablename__ = "data_mount_hit_log"

    id = Column(String(36), primary_key=True)
    request_id = Column(String(64), nullable=False, default="", index=True)
    conversation_id = Column(String(64), nullable=False, default="", index=True)
    user_id = Column(Integer, nullable=True, index=True)
    mount_id = Column(String(36), nullable=False, index=True)
    artifact_type = Column(String(48), nullable=False, default="")
    sample_ids = Column(JSON, nullable=False, default=list)
    hit_count = Column(Integer, nullable=False, default=0)
    token_count = Column(Integer, nullable=False, default=0)
    effect_summary = Column(JSON, nullable=False, default=dict)
    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id or "",
            "conversation_id": self.conversation_id or "",
            "user_id": int(self.user_id) if self.user_id is not None else None,
            "mount_id": self.mount_id,
            "artifact_type": self.artifact_type or "",
            "sample_ids": list(self.sample_ids or []),
            "hit_count": int(self.hit_count or 0),
            "token_count": int(self.token_count or 0),
            "effect_summary": self.effect_summary or {},
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }


__all__ = ["DataMountModel", "DataMountArtifactModel", "DataMountHitLogModel"]
