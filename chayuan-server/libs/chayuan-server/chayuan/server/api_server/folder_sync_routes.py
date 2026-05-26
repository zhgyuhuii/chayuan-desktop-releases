"""95-4:文件夹同步 HTTP 路由。

接口:
  GET    /folder_sync/jobs                 列出我的任务
  POST   /folder_sync/jobs                 新建
  GET    /folder_sync/jobs/{id}            详情
  PATCH  /folder_sync/jobs/{id}            更新
  DELETE /folder_sync/jobs/{id}            删除(顺带反注册 scheduler)
  POST   /folder_sync/jobs/{id}/trigger    立即同步一次
  POST   /folder_sync/jobs/{id}/dry_run    干跑:返 diff 不真上传
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from chayuan.server.auth.deps import require_auth_enabled

logger = logging.getLogger("chayuan.api.folder_sync")

folder_sync_router = APIRouter(
    prefix="/folder_sync", tags=["Folder Sync (定时同步)"],
)


# ---------------------------------------------------------------------------
# 真实 ingestion hook — 把 uploader 的占位 hook 接到 doc / image 真实链路
# ---------------------------------------------------------------------------

def _doc_upload_hook(kb_name: str, file_path: str) -> None:
    """文档文件 → kb_service.upload_doc(同步,内部已 chunk + embed)。"""
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        raise RuntimeError(f"文档 KB {kb_name!r} 不存在")
    # 沿用 file_rag 的 ingest 接口(返回 file_id);失败抛异常
    from chayuan.server.knowledge_base.utils import KnowledgeFile
    kf = KnowledgeFile(filename=file_path, knowledge_base_name=kb_name)
    kb.add_doc(kb_file=kf)


def _doc_delete_hook(kb_name: str, file_path: str) -> None:
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.utils import KnowledgeFile
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        raise RuntimeError(f"文档 KB {kb_name!r} 不存在")
    kf = KnowledgeFile(filename=file_path, knowledge_base_name=kb_name)
    kb.delete_doc(kb_file=kf, delete_content=False)


def _img_upload_hook(src_id: int, file_path: str) -> None:
    """image source → ImageConnector.add_image。"""
    from chayuan.server.db.repository.knowledge_source_repository import (
        connection_spec_for_source,
    )
    from chayuan.server.image_source.connector import ImageConnector
    resolved = connection_spec_for_source(int(src_id))
    if resolved is None:
        raise RuntimeError(f"image source {src_id} 不存在")
    _, spec = resolved
    conn = ImageConnector(spec=spec, source_id=int(src_id))
    conn.add_image(file_path)


def _img_delete_hook(src_id: int, file_path: str) -> None:
    from chayuan.server.db.repository.knowledge_source_repository import (
        connection_spec_for_source,
    )
    from chayuan.server.image_source.connector import ImageConnector
    resolved = connection_spec_for_source(int(src_id))
    if resolved is None:
        return  # source 没了就当删了
    _, spec = resolved
    conn = ImageConnector(spec=spec, source_id=int(src_id))
    if hasattr(conn, "delete_image"):
        try:
            conn.delete_image(file_path)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 同步执行
# ---------------------------------------------------------------------------

def _execute_sync(job_id: int, *, dry_run: bool = False) -> Dict[str, Any]:
    """执行一次同步。返回 summary。"""
    from chayuan.server.db.repository import folder_sync_repository as repo
    from chayuan.server.folder_sync import scanner as _scanner
    from chayuan.server.folder_sync import uploader as _uploader

    job = repo.get_job(job_id)
    if job is None:
        raise RuntimeError(f"job {job_id} 不存在")

    diff = _scanner.scan(
        job_id=job_id,
        folder_path=job["folder_path"],
        recursive=job["recursive"],
        include_globs=job["include_globs"],
        exclude_globs=job["exclude_globs"],
    )
    summary: Dict[str, Any] = {
        **diff.to_summary(),
        "scan_errors": list(diff.errors),
    }

    if dry_run:
        return {
            "diff_preview": {
                "added": [r.path for r in diff.added],
                "modified": [r.path for r in diff.modified],
                "removed": [r.path for r in diff.removed],
                "unchanged": diff.unchanged,
            },
            "summary": summary,
            "applied": False,
        }

    upload = _uploader.apply_diff(
        diff, target=job["target"],
        doc_upload=_doc_upload_hook,
        doc_delete=_doc_delete_hook,
        img_upload=_img_upload_hook,
        img_delete=_img_delete_hook,
    )
    _scanner.apply_state_after_diff(
        job_id, diff,
        successful_paths=upload.successful_paths,
    )

    summary.update(upload.summary)
    summary["upload_errors"] = list(upload.errors)
    repo.record_sync_result(job_id, summary)
    return {"summary": summary, "applied": True}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def _user_id(user: Any) -> int:
    if isinstance(user, dict):
        return int(user.get("id") or 0)
    return int(getattr(user, "id", 0) or 0)


def _check_owner(user: Any, job: Dict[str, Any]) -> None:
    if not isinstance(user, dict) or user.get("is_guest") \
            or user.get("role") == "admin":
        return
    if int(user.get("id") or 0) != int(job.get("owner_id") or -1):
        raise HTTPException(403, "你不是该任务的 owner")


def _schedule_if_enabled(job: Dict[str, Any]) -> None:
    """enabled 时把任务挂到 scheduler;否则反注册。"""
    from chayuan.server.folder_sync import scheduler as _sched
    if job.get("enabled"):
        try:
            _sched.schedule_job(
                job["id"], int(job["interval_seconds"]),
                lambda jid=job["id"]: _execute_sync(jid),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[folder_sync] schedule job %s failed: %r", job["id"], e,
            )
    else:
        _sched.unschedule_job(job["id"])


@folder_sync_router.get("/jobs", summary="95-4:列出我的同步任务")
def list_jobs_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.db.repository import folder_sync_repository as repo
    items = repo.list_jobs(owner_id=_user_id(user))
    return {"code": 0, "data": {"items": items, "total": len(items)}}


@folder_sync_router.post("/jobs", summary="95-4:新建同步任务")
def create_job_endpoint(
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    try:
        job = repo.create_job(
            name=str(payload.get("name") or "").strip(),
            folder_path=str(payload.get("folder_path") or "").strip(),
            target=str(payload.get("target") or "").strip(),
            owner_id=_user_id(user),
            interval_seconds=int(payload.get("interval_seconds") or 300),
            recursive=bool(payload.get("recursive", True)),
            include_globs=payload.get("include_globs"),
            exclude_globs=payload.get("exclude_globs"),
            enabled=bool(payload.get("enabled", True)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    _schedule_if_enabled(job)
    return {"code": 0, "data": job, "msg": "ok"}


@folder_sync_router.get("/jobs/{job_id}", summary="95-4:任务详情")
def get_job_endpoint(
    job_id: int = Path(..., ge=1),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    _check_owner(user, job)
    return {"code": 0, "data": job}


@folder_sync_router.patch("/jobs/{job_id}", summary="95-4:更新任务")
def update_job_endpoint(
    job_id: int,
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    cur = repo.get_job(job_id)
    if cur is None:
        raise HTTPException(404, "任务不存在")
    _check_owner(user, cur)
    try:
        job = repo.update_job(
            job_id,
            name=payload.get("name"),
            folder_path=payload.get("folder_path"),
            target=payload.get("target"),
            interval_seconds=payload.get("interval_seconds"),
            enabled=payload.get("enabled"),
            recursive=payload.get("recursive"),
            include_globs=payload.get("include_globs"),
            exclude_globs=payload.get("exclude_globs"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    _schedule_if_enabled(job)
    return {"code": 0, "data": job, "msg": "ok"}


@folder_sync_router.delete("/jobs/{job_id}", summary="95-4:删除任务")
def delete_job_endpoint(
    job_id: int,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    from chayuan.server.folder_sync import scheduler as _sched
    cur = repo.get_job(job_id)
    if cur is None:
        raise HTTPException(404, "任务不存在")
    _check_owner(user, cur)
    repo.delete_job(job_id)
    _sched.unschedule_job(job_id)
    return {"code": 0, "msg": "ok"}


@folder_sync_router.post(
    "/jobs/{job_id}/trigger",
    summary="95-4:立即同步一次(用户决策 4:定时+手动)",
)
def trigger_job_endpoint(
    job_id: int,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    cur = repo.get_job(job_id)
    if cur is None:
        raise HTTPException(404, "任务不存在")
    _check_owner(user, cur)
    try:
        result = _execute_sync(job_id, dry_run=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"同步失败: {type(e).__name__}: {e}")
    return {"code": 0, "data": result, "msg": "ok"}


@folder_sync_router.post(
    "/jobs/{job_id}/dry_run",
    summary="95-4:干跑预览 diff(不真上传)",
)
def dry_run_endpoint(
    job_id: int,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import folder_sync_repository as repo
    cur = repo.get_job(job_id)
    if cur is None:
        raise HTTPException(404, "任务不存在")
    _check_owner(user, cur)
    try:
        result = _execute_sync(job_id, dry_run=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"扫描失败: {type(e).__name__}: {e}")
    return {"code": 0, "data": result, "msg": "ok"}
