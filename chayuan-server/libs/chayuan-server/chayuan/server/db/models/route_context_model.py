"""Persisted UI navigation context.

Stores enough information to restore a user to the exact product surface that
created a search result, chat message, or KB chunk.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, func

from chayuan.server.db.base import Base


class RouteContextModel(Base):
    __tablename__ = "route_context"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    source = Column(String(64), nullable=False, default="kb")
    title = Column(String(256), nullable=False, default="")
    target_type = Column(String(64), nullable=False, default="")
    target_id = Column(String(128), nullable=False, default="")
    route = Column(String(512), nullable=False, default="")
    context = Column(JSON, nullable=False, default=dict)
    anchor = Column(JSON, nullable=False, default=dict)
    meta = Column(JSON, nullable=False, default=dict)
    summary = Column(Text, nullable=True)
    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": int(self.user_id) if self.user_id is not None else None,
            "source": self.source,
            "title": self.title,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "route": self.route,
            "context": self.context or {},
            "anchor": self.anchor or {},
            "meta": self.meta or {},
            "summary": self.summary or "",
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }


__all__ = ["RouteContextModel"]
