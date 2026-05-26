"""High-level CRUD with built-in event publishing.

Discovery / modelmgr / CLI all go through this single class so the event
bus stays consistent across writers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chayuan_core.events import (
    TOPIC_MODEL_ADDED,
    TOPIC_MODEL_REMOVED,
    TOPIC_MODEL_UPDATED,
    EventBus,
    get_bus,
)
from chayuan_registry.models import Model, ModelAlias, ModelStatus


class ModelRepository:
    def __init__(self, session: Session, bus: EventBus | None = None) -> None:
        self.s = session
        self.bus = bus or get_bus()

    # ---- queries ----------------------------------------------------------

    def list(
        self,
        *,
        category: str | None = None,
        status: ModelStatus | None = None,
        enabled: bool | None = None,
        include_removed: bool = False,
    ) -> list[Model]:
        stmt = select(Model)
        if category is not None:
            stmt = stmt.where(Model.category == category)
        if status is not None:
            stmt = stmt.where(Model.status == status)
        if enabled is not None:
            stmt = stmt.where(Model.enabled.is_(enabled))
        if not include_removed:
            stmt = stmt.where(Model.status != ModelStatus.REMOVED)
        return list(self.s.execute(stmt).scalars().all())

    def get(self, public_id: str) -> Model | None:
        stmt = select(Model).where(Model.repo == public_id)
        m = self.s.execute(stmt).scalar_one_or_none()
        if m:
            return m
        # alias lookup
        a = self.s.execute(select(ModelAlias).where(ModelAlias.alias == public_id)).scalar_one_or_none()
        return a.model if a else None

    def get_by_path(self, path: str) -> Model | None:
        return self.s.execute(select(Model).where(Model.path == path)).scalar_one_or_none()

    def by_id(self, mid: int) -> Model | None:
        return self.s.get(Model, mid)

    # ---- writes -----------------------------------------------------------

    def upsert(self, payload: dict[str, Any]) -> tuple[Model, bool]:
        """Create or update by (repo, category). Returns (model, created)."""
        repo, category, path = payload["repo"], payload["category"], payload["path"]
        existing = self.s.execute(
            select(Model).where(Model.repo == repo, Model.category == category)
        ).scalar_one_or_none()
        if existing is None:
            m = Model(
                repo=repo,
                name=payload.get("name", repo.split("/")[-1]),
                category=category,
                runtime=payload.get("runtime", "auto"),
                format=payload.get("format", "unknown"),
                quantization=payload.get("quantization", ""),
                path=path,
                size_bytes=int(payload.get("size_bytes", 0)),
                sha256=payload.get("sha256", ""),
                status=ModelStatus(payload.get("status", ModelStatus.READY.value)),
                capabilities=payload.get("capabilities", {}),
                extra=payload.get("extra", {}),
                source=payload.get("source", "discovered"),
                file_mtime=float(payload.get("file_mtime", 0.0)),
            )
            self.s.add(m)
            self.s.flush()
            self.bus.publish(TOPIC_MODEL_ADDED, m.to_public())
            return m, True

        # update path
        changed = False
        for field in (
            "name", "runtime", "format", "quantization", "size_bytes", "sha256",
            "capabilities", "extra", "file_mtime", "source",
        ):
            if field in payload:
                old = getattr(existing, field)
                new = payload[field]
                if old != new:
                    setattr(existing, field, new)
                    changed = True
        if "status" in payload:
            new_status = ModelStatus(payload["status"])
            if existing.status != new_status:
                existing.status = new_status
                changed = True
        if "path" in payload and existing.path != path:
            existing.path = path
            changed = True
        if existing.status == ModelStatus.REMOVED:
            existing.status = ModelStatus(payload.get("status", ModelStatus.READY.value))
            changed = True
        existing.updated_at = datetime.utcnow()
        self.s.flush()
        if changed:
            self.bus.publish(TOPIC_MODEL_UPDATED, existing.to_public())
        return existing, False

    def soft_remove_by_path(self, path: str) -> Model | None:
        m = self.get_by_path(path)
        if m is None or m.status == ModelStatus.REMOVED:
            return m
        m.status = ModelStatus.REMOVED
        m.updated_at = datetime.utcnow()
        self.s.flush()
        self.bus.publish(TOPIC_MODEL_REMOVED, m.to_public())
        return m

    def hard_remove(self, public_id: str) -> bool:
        m = self.get(public_id)
        if m is None:
            return False
        payload = m.to_public()
        self.s.delete(m)
        self.s.flush()
        self.bus.publish(TOPIC_MODEL_REMOVED, payload)
        return True

    def add_alias(self, public_id: str, alias: str) -> bool:
        m = self.get(public_id)
        if m is None:
            return False
        if any(a.alias == alias for a in m.aliases):
            return True
        self.s.add(ModelAlias(model=m, alias=alias))
        self.s.flush()
        self.bus.publish(TOPIC_MODEL_UPDATED, m.to_public())
        return True

    def set_default(self, category: str, public_id: str) -> bool:
        target = self.get(public_id)
        if target is None or target.category != category:
            return False
        for m in self.list(category=category, include_removed=True):
            if m.is_default and m.id != target.id:
                m.is_default = False
        target.is_default = True
        self.s.flush()
        self.bus.publish(TOPIC_MODEL_UPDATED, target.to_public())
        return True

    def set_enabled(self, public_id: str, enabled: bool) -> bool:
        m = self.get(public_id)
        if m is None:
            return False
        if m.enabled == enabled:
            return True
        m.enabled = enabled
        self.s.flush()
        self.bus.publish(TOPIC_MODEL_UPDATED, m.to_public())
        return True
