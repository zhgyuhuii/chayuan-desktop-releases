"""Data annotation tasks for evaluation and model improvement."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from chayuan.server.db.base import Base


class AnnotationTaskModel(Base):
    """A single human/LLM annotation task.

    The payload fields stay JSON on purpose: the same table can represent RAG
    relevance, answer quality, safety labels, and preference pairs before those
    workflows deserve dedicated typed tables.
    """

    __tablename__ = "annotation_task"

    id = Column(String(36), primary_key=True)
    source = Column(String(64), nullable=False, default="manual", index=True)
    task_type = Column(String(64), nullable=False, default="qa_quality", index=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    priority = Column(Integer, nullable=False, default=0, index=True)

    target_type = Column(String(64), nullable=True, index=True)
    target_id = Column(String(128), nullable=True, index=True)
    route_context_id = Column(String(36), nullable=True, index=True)

    inputs = Column(JSON, nullable=False, default=dict)
    model_output = Column(JSON, nullable=False, default=dict)
    llm_prel_labels = Column(JSON, nullable=False, default=dict)
    labels = Column(JSON, nullable=False, default=dict)
    review = Column(JSON, nullable=False, default=dict)
    error_tags = Column(JSON, nullable=False, default=list)
    meta = Column(JSON, nullable=False, default=dict)
    note = Column(Text, nullable=True)

    assignee_id = Column(Integer, nullable=True, index=True)
    reviewer_id = Column(Integer, nullable=True, index=True)
    created_by = Column(Integer, nullable=True, index=True)
    updated_by = Column(Integer, nullable=True)

    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "task_type": self.task_type,
            "status": self.status,
            "priority": int(self.priority or 0),
            "target_type": self.target_type,
            "target_id": self.target_id,
            "route_context_id": self.route_context_id,
            "inputs": self.inputs or {},
            "model_output": self.model_output or {},
            "llm_prel_labels": self.llm_prel_labels or {},
            "labels": self.labels or {},
            "review": self.review or {},
            "error_tags": list(self.error_tags or []),
            "meta": self.meta or {},
            "note": self.note or "",
            "assignee_id": int(self.assignee_id) if self.assignee_id is not None else None,
            "reviewer_id": int(self.reviewer_id) if self.reviewer_id is not None else None,
            "created_by": int(self.created_by) if self.created_by is not None else None,
            "updated_by": int(self.updated_by) if self.updated_by is not None else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }


class AnnotationFeedbackModel(Base):
    """One user/judge feedback submission for an annotation target.

    Multiple rows may point to the same task so that independent annotators do
    not overwrite each other. The task.review field can then store aggregation.
    """

    __tablename__ = "annotation_feedback"

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("annotation_task.id", ondelete="SET NULL"), nullable=True, index=True)
    source = Column(String(64), nullable=False, default="user_feedback", index=True)
    target_type = Column(String(64), nullable=True, index=True)
    target_id = Column(String(128), nullable=True, index=True)
    source_ref = Column(JSON, nullable=False, default=dict)
    rating = Column(Integer, nullable=True, index=True)
    quality = Column(String(32), nullable=False, default="", index=True)
    labels = Column(JSON, nullable=False, default=dict)
    error_tags = Column(JSON, nullable=False, default=list)
    comment = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=True, index=True)
    create_time = Column(DateTime, default=func.now(), nullable=False, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "source": self.source,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "source_ref": self.source_ref or {},
            "rating": int(self.rating) if self.rating is not None else None,
            "quality": self.quality or "",
            "labels": self.labels or {},
            "error_tags": list(self.error_tags or []),
            "comment": self.comment or "",
            "created_by": int(self.created_by) if self.created_by is not None else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }


__all__ = ["AnnotationTaskModel", "AnnotationFeedbackModel"]
