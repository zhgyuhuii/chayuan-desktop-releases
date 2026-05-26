from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from chayuan.server.db.models.route_context_model import RouteContextModel
from chayuan.server.db.session import with_session


@with_session
def create(
    session: Session,
    *,
    user_id: Optional[int],
    source: str,
    title: str,
    target_type: str,
    target_id: str,
    route: str,
    context: Dict[str, Any],
    anchor: Dict[str, Any],
    meta: Dict[str, Any],
    summary: str = "",
) -> Dict[str, Any]:
    row = RouteContextModel(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source=source[:64],
        title=title[:256],
        target_type=target_type[:64],
        target_id=target_id[:128],
        route=route[:512],
        context=context or {},
        anchor=anchor or {},
        meta=meta or {},
        summary=summary[:4000] if summary else "",
    )
    session.add(row)
    session.flush()
    return row.to_dict()


@with_session
def list_for_user(
    session: Session,
    *,
    user_id: Optional[int],
    source: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    q = session.query(RouteContextModel)
    if user_id is None:
        q = q.filter(RouteContextModel.user_id.is_(None))
    else:
        q = q.filter(RouteContextModel.user_id == user_id)
    if source:
        q = q.filter(RouteContextModel.source == source)
    if target_type:
        q = q.filter(RouteContextModel.target_type == target_type)
    rows = (
        q.order_by(RouteContextModel.create_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in rows]


@with_session
def get_for_user(
    session: Session,
    *,
    context_id: str,
    user_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    q = session.query(RouteContextModel).filter(RouteContextModel.id == context_id)
    if user_id is None:
        q = q.filter(RouteContextModel.user_id.is_(None))
    else:
        q = q.filter(RouteContextModel.user_id == user_id)
    row = q.one_or_none()
    return row.to_dict() if row else None
