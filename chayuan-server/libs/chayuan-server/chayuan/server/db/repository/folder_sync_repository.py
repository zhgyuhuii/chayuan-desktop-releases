"""95-1:folder_sync_jobs 仓储层。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from chayuan.server.db.models.folder_sync_model import (
    DEFAULT_EXCLUDE_GLOBS, DEFAULT_INCLUDE_GLOBS, FolderSyncJobModel,
)
from chayuan.server.db.session import with_session


@with_session
def create_job(
    session, *,
    name: str, folder_path: str, target: str, owner_id: int,
    interval_seconds: int = 300, recursive: bool = True,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    if not name or not name.strip():
        raise ValueError("name required")
    if not folder_path or not folder_path.strip():
        raise ValueError("folder_path required")
    if not target or not target.strip():
        raise ValueError("target required")
    if int(interval_seconds) < 30:
        raise ValueError("interval_seconds 不能小于 30 秒")
    obj = FolderSyncJobModel(
        name=name.strip(),
        folder_path=folder_path.strip(),
        target=target.strip(),
        owner_id=int(owner_id),
        interval_seconds=int(interval_seconds),
        recursive=bool(recursive),
        include_globs=list(include_globs or DEFAULT_INCLUDE_GLOBS),
        exclude_globs=list(exclude_globs or DEFAULT_EXCLUDE_GLOBS),
        enabled=bool(enabled),
    )
    session.add(obj)
    session.flush()
    return obj.to_dict()


@with_session
def get_job(session, job_id: int) -> Optional[Dict[str, Any]]:
    obj = session.get(FolderSyncJobModel, int(job_id))
    return obj.to_dict() if obj else None


@with_session
def list_jobs(
    session, *, owner_id: Optional[int] = None, enabled_only: bool = False,
) -> List[Dict[str, Any]]:
    q = session.query(FolderSyncJobModel)
    if owner_id is not None:
        q = q.filter(FolderSyncJobModel.owner_id == int(owner_id))
    if enabled_only:
        q = q.filter(FolderSyncJobModel.enabled.is_(True))
    return [obj.to_dict() for obj in
            q.order_by(FolderSyncJobModel.create_time.desc()).all()]


@with_session
def update_job(
    session, job_id: int, *,
    name: Optional[str] = None,
    folder_path: Optional[str] = None,
    target: Optional[str] = None,
    interval_seconds: Optional[int] = None,
    enabled: Optional[bool] = None,
    recursive: Optional[bool] = None,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    obj = session.get(FolderSyncJobModel, int(job_id))
    if obj is None:
        return None
    if name is not None:
        obj.name = name.strip() or obj.name
    if folder_path is not None:
        obj.folder_path = folder_path.strip() or obj.folder_path
    if target is not None:
        obj.target = target.strip() or obj.target
    if interval_seconds is not None:
        if int(interval_seconds) < 30:
            raise ValueError("interval_seconds 不能小于 30 秒")
        obj.interval_seconds = int(interval_seconds)
    if enabled is not None:
        obj.enabled = bool(enabled)
    if recursive is not None:
        obj.recursive = bool(recursive)
    if include_globs is not None:
        obj.include_globs = list(include_globs)
    if exclude_globs is not None:
        obj.exclude_globs = list(exclude_globs)
    session.flush()
    return obj.to_dict()


@with_session
def delete_job(session, job_id: int) -> bool:
    obj = session.get(FolderSyncJobModel, int(job_id))
    if obj is None:
        return False
    session.delete(obj)
    return True


@with_session
def record_sync_result(
    session, job_id: int, summary: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """同步完成后记录 last_sync_at + last_sync_summary。"""
    obj = session.get(FolderSyncJobModel, int(job_id))
    if obj is None:
        return None
    obj.last_sync_at = datetime.utcnow()
    obj.last_sync_summary = dict(summary or {})
    session.flush()
    return obj.to_dict()
