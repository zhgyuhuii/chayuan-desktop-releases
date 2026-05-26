from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from chayuan.server.db.models.annotation_model import AnnotationFeedbackModel, AnnotationTaskModel
from chayuan.server.db.session import with_session


@with_session
def create_task(
    session: Session,
    *,
    source: str,
    task_type: str,
    inputs: Dict[str, Any],
    model_output: Dict[str, Any],
    llm_prel_labels: Dict[str, Any],
    target_type: Optional[str],
    target_id: Optional[str],
    route_context_id: Optional[str],
    meta: Dict[str, Any],
    priority: int,
    note: str,
    created_by: Optional[int],
) -> Dict[str, Any]:
    row = AnnotationTaskModel(
        id=str(uuid.uuid4()),
        source=(source or "manual")[:64],
        task_type=(task_type or "qa_quality")[:64],
        status="pending",
        priority=int(priority or 0),
        inputs=inputs or {},
        model_output=model_output or {},
        llm_prel_labels=llm_prel_labels or {},
        target_type=(target_type or "")[:64] or None,
        target_id=(target_id or "")[:128] or None,
        route_context_id=(route_context_id or "")[:36] or None,
        meta=meta or {},
        note=note or "",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(row)
    session.flush()
    return row.to_dict()


@with_session
def get_task(session: Session, task_id: str) -> Optional[Dict[str, Any]]:
    row = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.id == task_id).one_or_none()
    return row.to_dict() if row else None


@with_session
def list_tasks(
    session: Session,
    *,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    source: Optional[str] = None,
    assignee_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    q = session.query(AnnotationTaskModel)
    if status:
        q = q.filter(AnnotationTaskModel.status == status)
    if task_type:
        q = q.filter(AnnotationTaskModel.task_type == task_type)
    if source:
        q = q.filter(AnnotationTaskModel.source == source)
    if assignee_id is not None:
        q = q.filter(AnnotationTaskModel.assignee_id == assignee_id)
    total = q.count()
    rows = (
        q.order_by(AnnotationTaskModel.priority.desc(), AnnotationTaskModel.create_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in rows], int(total)


@with_session
def update_task(
    session: Session,
    *,
    task_id: str,
    actor_id: Optional[int],
    status: Optional[str] = None,
    labels: Optional[Dict[str, Any]] = None,
    review: Optional[Dict[str, Any]] = None,
    error_tags: Optional[Sequence[str]] = None,
    note: Optional[str] = None,
    assignee_id: Optional[int] = None,
    reviewer_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    row = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.id == task_id).one_or_none()
    if row is None:
        return None
    if status is not None:
        row.status = status[:24]
    if labels is not None:
        row.labels = labels
    if review is not None:
        row.review = review
    if error_tags is not None:
        row.error_tags = list(error_tags)
    if note is not None:
        row.note = note
    if assignee_id is not None:
        row.assignee_id = assignee_id
    if reviewer_id is not None:
        row.reviewer_id = reviewer_id
    row.updated_by = actor_id
    row.update_time = datetime.utcnow()
    session.flush()
    return row.to_dict()


def _feedback_summary(rows: List[AnnotationFeedbackModel]) -> Dict[str, Any]:
    total = len(rows)
    positives = sum(1 for r in rows if (r.rating or 0) > 0 or r.quality == "good")
    negatives = sum(1 for r in rows if (r.rating or 0) < 0 or r.quality == "bad")
    neutral = max(total - positives - negatives, 0)
    tag_counts: Dict[str, int] = {}
    rating_sum = 0
    rating_count = 0
    for row in rows:
        if row.rating is not None:
            rating_sum += int(row.rating)
            rating_count += 1
        for tag in row.error_tags or []:
            key = str(tag)
            tag_counts[key] = tag_counts.get(key, 0) + 1
    return {
        "total": total,
        "positive": positives,
        "negative": negatives,
        "neutral": neutral,
        "avg_rating": (rating_sum / rating_count) if rating_count else None,
        "error_tag_counts": tag_counts,
        "consensus": (
            "good" if total > 0 and positives / total >= 0.67
            else "bad" if total > 0 and negatives / total >= 0.67
            else "mixed" if total > 1
            else ""
        ),
    }


@with_session
def create_feedback(
    session: Session,
    *,
    task_id: Optional[str],
    source: str,
    target_type: Optional[str],
    target_id: Optional[str],
    source_ref: Dict[str, Any],
    rating: Optional[int],
    quality: str,
    labels: Dict[str, Any],
    error_tags: Sequence[str],
    comment: str,
    created_by: Optional[int],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    task = None
    if task_id:
        task = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.id == task_id).one_or_none()
        if task is None:
            raise ValueError("annotation task not found")

    row = AnnotationFeedbackModel(
        id=str(uuid.uuid4()),
        task_id=task_id,
        source=(source or "user_feedback")[:64],
        target_type=(target_type or "")[:64] or None,
        target_id=(target_id or "")[:128] or None,
        source_ref=source_ref or {},
        rating=rating,
        quality=(quality or "")[:32],
        labels=labels or {},
        error_tags=list(error_tags or []),
        comment=comment or "",
        created_by=created_by,
    )
    session.add(row)
    session.flush()

    updated_task: Optional[Dict[str, Any]] = None
    if task is not None:
        rows = (
            session.query(AnnotationFeedbackModel)
            .filter(AnnotationFeedbackModel.task_id == task_id)
            .order_by(AnnotationFeedbackModel.create_time.asc())
            .all()
        )
        review = dict(task.review or {})
        review["feedback_summary"] = _feedback_summary(rows)
        review["latest_feedback"] = row.to_dict()
        task.review = review
        task.status = "submitted"
        task.updated_by = created_by
        task.update_time = datetime.utcnow()
        session.flush()
        updated_task = task.to_dict()

    return row.to_dict(), updated_task


@with_session
def list_feedback(
    session: Session,
    *,
    task_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    q = session.query(AnnotationFeedbackModel)
    if task_id:
        q = q.filter(AnnotationFeedbackModel.task_id == task_id)
    if target_type:
        q = q.filter(AnnotationFeedbackModel.target_type == target_type)
    if target_id:
        q = q.filter(AnnotationFeedbackModel.target_id == target_id)
    total = q.count()
    rows = (
        q.order_by(AnnotationFeedbackModel.create_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in rows], int(total)


@with_session
def export_dataset(
    session: Session,
    *,
    statuses: Sequence[str],
    task_type: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    q = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.status.in_(list(statuses)))
    if task_type:
        q = q.filter(AnnotationTaskModel.task_type == task_type)
    rows = q.order_by(AnnotationTaskModel.update_time.desc()).limit(limit).all()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = row.to_dict()
        out.append({
            "id": d["id"],
            "task_type": d["task_type"],
            "input": d["inputs"],
            "model_output": d["model_output"],
            "labels": d["labels"],
            "error_tags": d["error_tags"],
            "route_context_id": d["route_context_id"],
            "meta": d["meta"],
        })
    return out


@with_session
def list_usable_samples(
    session: Session,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return approved samples that online chains are allowed to consume.

    This is the first closed-loop boundary: only human-reviewed/approved
    annotation tasks are used by retrieval ranking.
    """
    q = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.status == "approved")
    if target_type:
        q = q.filter(AnnotationTaskModel.target_type == target_type)
    if target_id:
        q = q.filter(AnnotationTaskModel.target_id == target_id)
    if task_type:
        q = q.filter(AnnotationTaskModel.task_type == task_type)
    rows = q.order_by(AnnotationTaskModel.update_time.desc()).limit(max(1, min(int(limit or 500), 5000))).all()
    return [r.to_dict() for r in rows]


def _label_score(labels: Dict[str, Any]) -> int:
    if not isinstance(labels, dict):
        return 0
    if labels.get("is_correct") is False or labels.get("answer_correct") is False:
        return -1
    if labels.get("retrieval_relevant") is False or labels.get("citation_supported") is False:
        return -1
    for key in ("relevance_score", "faithfulness_score", "answer_quality_score", "style_score"):
        try:
            if key in labels:
                value = float(labels.get(key) or 0)
                if value >= 4:
                    return 1
                if value > 0 and value <= 2:
                    return -1
        except Exception:
            pass
    quality = str(labels.get("answer_quality") or labels.get("quality") or "").lower()
    if quality in ("good", "excellent", "pass", "approved"):
        return 1
    if quality in ("bad", "poor", "fail", "rejected", "irrelevant"):
        return -1
    if labels.get("is_correct") is True or labels.get("answer_correct") is True:
        return 1
    return 0


def _doc_vote_key(item: Dict[str, Any]) -> Optional[str]:
    file_name = str(item.get("file_name") or item.get("source") or "").strip()
    if not file_name:
        return None
    chunk = item.get("chunk_index")
    if chunk in (None, ""):
        return file_name
    return f"{file_name}#chunk={chunk}"


@with_session
def rag_document_votes(
    session: Session,
    *,
    knowledge_base_id: str,
    limit: int = 500,
) -> Dict[str, Dict[str, Any]]:
    """Build file/chunk-level votes from approved RAG annotation samples."""
    rows = (
        session.query(AnnotationTaskModel)
        .filter(AnnotationTaskModel.status == "approved")
        .filter(AnnotationTaskModel.target_type == "knowledge_base")
        .filter(AnnotationTaskModel.target_id == str(knowledge_base_id))
        .filter(AnnotationTaskModel.task_type.in_(["rag_relevance", "qa_quality"]))
        .order_by(AnnotationTaskModel.update_time.desc())
        .limit(max(1, min(int(limit or 500), 2000)))
        .all()
    )
    votes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        labels = dict(row.labels or {})
        score = _label_score(labels)
        if score == 0:
            continue
        output = dict(row.model_output or {})
        items = output.get("retrieved_items")
        if not isinstance(items, list):
            continue
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            key = _doc_vote_key(item)
            if not key:
                continue
            cur = votes.setdefault(key, {"positive": 0, "negative": 0, "task_ids": []})
            if score > 0:
                cur["positive"] = int(cur.get("positive") or 0) + 1
            else:
                cur["negative"] = int(cur.get("negative") or 0) + 1
            cur["task_ids"] = list(cur.get("task_ids") or [])[:8] + [row.id]
    return votes


@with_session
def usage_summary(session: Session, *, limit: int = 1000) -> Dict[str, Any]:
    q = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.status == "approved")
    rows = q.order_by(AnnotationTaskModel.update_time.desc()).limit(max(1, min(limit, 5000))).all()
    by_type: Dict[str, int] = {}
    by_target: Dict[str, int] = {}
    for row in rows:
        by_type[row.task_type] = by_type.get(row.task_type, 0) + 1
        key = row.target_type or "unknown"
        by_target[key] = by_target.get(key, 0) + 1
    return {
        "usable_total": len(rows),
        "by_task_type": by_type,
        "by_target_type": by_target,
        "online_consumers": [
            "RAG retrieval ranking",
            "dataset export / evaluation",
        ],
    }
