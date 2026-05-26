"""KB 远端同步路由 — 挂在 /knowledge_base/remote_sources/* 下,与 kb_routes 同前缀。

端点设计:
  GET  /kinds                             → 列已知 source kind + 是否 available
  POST /test                              → 试连
  POST /browse                            → 浏览目录(分页)
  POST /preflight                         → 给定 paths + filter,只统计不写入
  POST /jobs                              → 启动同步,返回 job_id
  GET  /jobs/{job_id}                     → 查 snapshot
  GET  /jobs/{job_id}/stream              → SSE 流式进度
  POST /jobs/{job_id}/cancel              → 申请取消
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sse_starlette import EventSourceResponse

from chayuan.server.auth.access import can_write_kb, is_kb_owner
from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.knowledge_base.remote_sources import (
    JobSpec,
    SourceConfig,
    SourceError,
    SyncEngine,
    SyncFilter,
    build_source,
    get_job_manager,
    list_source_kinds,
)
from chayuan.server.knowledge_base.remote_sources.job_manager import JobStatus
from chayuan.server.utils import BaseResponse

logger = logging.getLogger("chayuan.api.kb.remote_sync")

remote_sync_router = APIRouter(
    prefix="/knowledge_base/remote_sources",
    tags=["KB Remote Sync"],
)


# ──────────────────────────────────────────────────────────────────────
# 鉴权 helper:同步 = 写操作,要求 owner 或 admin
# ──────────────────────────────────────────────────────────────────────

def _require_write(user, kb_name: str) -> None:
    if user is None:
        return
    if not can_write_kb(user, kb_name):
        raise HTTPException(403, f"no write permission on kb {kb_name!r}")


def _build_or_4xx(kind: str, options: Dict[str, Any]):
    if not kind:
        raise HTTPException(400, "kind 必填")
    try:
        return build_source(SourceConfig(kind=kind, options=options or {}))
    except SourceError as e:
        raise HTTPException(400, str(e))


# ──────────────────────────────────────────────────────────────────────
# 端点
# ──────────────────────────────────────────────────────────────────────

@remote_sync_router.get("/kinds", response_model=BaseResponse, summary="列出已知 source kind")
def get_kinds(_user=Depends(require_auth_enabled())):
    return BaseResponse(code=0, msg="ok", data=list_source_kinds())


@remote_sync_router.post("/test", response_model=BaseResponse, summary="试连")
def test_connection(
    kind: str = Body(..., examples=["minio"]),
    options: Dict[str, Any] = Body(default_factory=dict),
    _user=Depends(require_auth_enabled()),
):
    src = _build_or_4xx(kind, options)
    try:
        result = src.test()
    except SourceError as e:
        return BaseResponse(code=400, msg=str(e), data={"ok": False, "msg": str(e)})
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
    return BaseResponse(code=0, msg="ok", data=result)


@remote_sync_router.post("/browse", response_model=BaseResponse, summary="浏览远端目录")
def browse(
    kind: str = Body(...),
    options: Dict[str, Any] = Body(default_factory=dict),
    path: str = Body("", description="远端路径,根用空串"),
    marker: Optional[str] = Body(None, description="分页游标"),
    limit: int = Body(200, ge=1, le=1000),
    _user=Depends(require_auth_enabled()),
):
    src = _build_or_4xx(kind, options)
    try:
        page = src.browse(path, marker=marker, limit=limit)
    except SourceError as e:
        raise HTTPException(400, str(e))
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
    return BaseResponse(code=0, msg="ok", data=page.to_dict())


@remote_sync_router.post("/preflight", response_model=BaseResponse, summary="预扫描:统计待同步文件")
def preflight(
    kind: str = Body(...),
    options: Dict[str, Any] = Body(default_factory=dict),
    paths: List[str] = Body(default_factory=list),
    extensions: List[str] = Body(default_factory=list),
    max_size_bytes: Optional[int] = Body(None),
    include_globs: List[str] = Body(default_factory=list),
    exclude_globs: List[str] = Body(default_factory=list),
    sample: int = Body(20, ge=0, le=200, description="样本文件名条数,UI 预览用"),
    _user=Depends(require_auth_enabled()),
):
    src = _build_or_4xx(kind, options)
    flt = SyncFilter(
        extensions=extensions,
        max_size_bytes=max_size_bytes,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
    )
    total = 0
    bytes_total = 0
    sample_list: List[Dict[str, Any]] = []
    try:
        for root in paths or [""]:
            for f in src.walk(root):
                if not flt.accepts(f):
                    continue
                total += 1
                bytes_total += f.size
                if len(sample_list) < sample:
                    sample_list.append(f.to_dict())
    except SourceError as e:
        raise HTTPException(400, str(e))
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
    return BaseResponse(code=0, msg="ok", data={
        "total": total,
        "bytes_total": bytes_total,
        "sample": sample_list,
    })


@remote_sync_router.post("/jobs", response_model=BaseResponse, summary="启动同步任务")
def start_job(
    kb_name: str = Body(..., examples=["samples"]),
    kind: str = Body(...),
    options: Dict[str, Any] = Body(default_factory=dict),
    paths: List[str] = Body(default_factory=list),
    extensions: List[str] = Body(default_factory=list),
    max_size_bytes: Optional[int] = Body(None),
    include_globs: List[str] = Body(default_factory=list),
    exclude_globs: List[str] = Body(default_factory=list),
    concurrency: int = Body(4, ge=1, le=16),
    override: bool = Body(False),
    to_vector_store: bool = Body(True),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, kb_name)
    src = _build_or_4xx(kind, options)
    spec = JobSpec(
        kb_name=kb_name,
        paths=paths,
        filter=SyncFilter(
            extensions=extensions,
            max_size_bytes=max_size_bytes,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        ),
        concurrency=concurrency,
        override=override,
        to_vector_store=to_vector_store,
    )
    engine = SyncEngine(get_job_manager())
    job = engine.submit(src, spec)
    return BaseResponse(code=0, msg="ok", data=job.snapshot())


@remote_sync_router.get("/jobs/{job_id}", response_model=BaseResponse, summary="查询 job 状态")
def get_job(job_id: str, _user=Depends(require_auth_enabled())):
    j = get_job_manager().get(job_id)
    if j is None:
        raise HTTPException(404, "job 不存在或已过期")
    return BaseResponse(code=0, msg="ok", data=j.snapshot())


@remote_sync_router.post("/jobs/{job_id}/cancel", response_model=BaseResponse, summary="申请取消")
def cancel_job(job_id: str, _user=Depends(require_auth_enabled())):
    ok = get_job_manager().cancel(job_id)
    if not ok:
        raise HTTPException(409, "job 不存在或已结束")
    return BaseResponse(code=0, msg="ok")


@remote_sync_router.get("/jobs/{job_id}/stream", summary="SSE 流式进度")
async def stream_job(
    job_id: str,
    request: Request,
    _user=Depends(require_auth_enabled()),
):
    """SSE 流。每个事件 = 一个 JobEvent;event 名 = type,data = JSON。

    客户端断开 → unsubscribe + 资源回收。job 终态后再补一帧 close 让客户端关流。
    """
    jm = get_job_manager()
    if jm.get(job_id) is None:
        raise HTTPException(404, "job 不存在或已过期")

    async def gen():
        q = await jm.subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # heartbeat 让代理不掐流
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": ev.type, "data": json.dumps(ev.data, ensure_ascii=False)}
                # 终态事件后补一个 done 让前端关连接
                if ev.type == "status" and ev.data.get("status") in (
                    JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value,
                ):
                    yield {"event": "done", "data": "{}"}
                    return
        finally:
            jm.unsubscribe(job_id, q)

    return EventSourceResponse(gen())
