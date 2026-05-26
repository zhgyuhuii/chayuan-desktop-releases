from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.kb_query.authz import subject_from_user
from chayuan.server.kb_query.evals import (
    eval_trend,
    get_eval_run,
    list_eval_runs,
    load_golden_set,
    run_eval,
    run_gate,
    save_golden_set,
)
from chayuan.server.kb_query.jobs import (
    assert_job_access,
    allocate_ndjson_input_path,
    cancel_job,
    cleanup_expired_jobs,
    create_job,
    create_ndjson_job,
    get_job,
    get_job_jsonl_path,
    get_job_result,
    run_job,
    run_ndjson_job,
)
from chayuan.server.kb_query.schemas import SearchRequest
from chayuan.server.kb_query.service import list_knowledge_bases, search


kb_query_router = APIRouter(
    prefix="/api/v1/kb-query",
    tags=["Knowledge Base Query"],
)


def _setting_int(name: str, default: int) -> int:
    try:
        from chayuan.settings import Settings

        return int(getattr(Settings.basic_settings, name, default))
    except Exception:
        return default


def _enforce_sync_limits(request: Optional[Request], body: SearchRequest) -> None:
    max_count = _setting_int("KB_QUERY_SYNC_CONTENT_MAX_COUNT", 1000)
    if len(body.contents) > max_count:
        raise HTTPException(
            413,
            f"contents 超过同步接口上限 {max_count}，请使用 /api/v1/kb-query/search-jobs",
        )

    max_mb = _setting_int("KB_QUERY_SYNC_BODY_MAX_MB", 50)
    if request is None:
        return
    raw_len = request.headers.get("content-length") or ""
    if not raw_len:
        return
    try:
        body_bytes = int(raw_len)
    except ValueError:
        return
    if body_bytes > max_mb * 1024 * 1024:
        raise HTTPException(
            413,
            f"请求体超过同步接口上限 {max_mb}MB，请使用 /api/v1/kb-query/search-jobs",
        )


@kb_query_router.get("/knowledge-bases", summary="获取当前调用方可查询的知识库清单")
def knowledge_bases(
    kind: Optional[str] = Query(None, description="document / structured / image / vector"),
    include_vector: bool = Query(False),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    return list_knowledge_bases(subject, kind=kind, include_vector=include_vector)


@kb_query_router.post("/search", summary="批量查询单个知识库")
def search_knowledge_base(
    request: Request,
    body: SearchRequest = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    _enforce_sync_limits(request, body)
    request_id = str(getattr(request.state, "request_id", "") or "")
    return search(subject, body, request_id=request_id)


@kb_query_router.post("/search-jobs", summary="创建异步知识库查询任务")
def create_search_job(
    background_tasks: BackgroundTasks,
    request: Request,
    body: SearchRequest = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    request_id = str(getattr(request.state, "request_id", "") or "")
    job = create_job(subject, body, request_id=request_id)
    background_tasks.add_task(run_job, job["job_id"], subject, body)
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "job_id": job["job_id"],
            "status": job["status"],
            "request_id": request_id,
            "accepted_count": job["content_count"],
        },
    }


@kb_query_router.post("/search-jobs/ndjson", summary="创建 NDJSON 流式异步知识库查询任务")
async def create_search_job_ndjson(
    background_tasks: BackgroundTasks,
    request: Request,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    request_id = str(getattr(request.state, "request_id", "") or "")
    job_id, input_path = allocate_ndjson_input_path()
    max_mb = _setting_int("KB_QUERY_NDJSON_BODY_MAX_MB", 1024)
    max_lines = _setting_int("KB_QUERY_NDJSON_MAX_LINES", 1_000_000)

    raw_len = request.headers.get("content-length") or ""
    if raw_len:
        try:
            if int(raw_len) > max_mb * 1024 * 1024:
                raise HTTPException(
                    413,
                    f"NDJSON 请求体超过上限 {max_mb}MB，请拆分为多个任务",
                )
        except ValueError:
            pass

    line_count = 0
    total_bytes = 0
    last_byte = b""
    try:
        with input_path.open("wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > max_mb * 1024 * 1024:
                    raise HTTPException(
                        413,
                        f"NDJSON 请求体超过上限 {max_mb}MB，请拆分为多个任务",
                    )
                line_count += chunk.count(b"\n")
                if line_count > max_lines:
                    raise HTTPException(
                        413,
                        f"NDJSON 行数超过上限 {max_lines}，请拆分为多个任务",
                    )
                last_byte = chunk[-1:]
                f.write(chunk)
    except Exception:
        input_path.unlink(missing_ok=True)
        raise
    if total_bytes > 0 and last_byte != b"\n":
        line_count += 1
    if line_count > max_lines:
        input_path.unlink(missing_ok=True)
        raise HTTPException(
            413,
            f"NDJSON 行数超过上限 {max_lines}，请拆分为多个任务",
        )

    job = create_ndjson_job(
        subject,
        input_path=input_path,
        content_count=line_count,
        request_id=request_id,
    )
    background_tasks.add_task(run_ndjson_job, job_id, subject, str(input_path))
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "job_id": job["job_id"],
            "status": job["status"],
            "request_id": request_id,
            "accepted_count": job["content_count"],
            "format": "ndjson",
        },
    }


@kb_query_router.get("/search-jobs/{job_id}", summary="查询异步知识库查询任务状态")
def get_search_job(job_id: str, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    subject = subject_from_user(user)
    job = assert_job_access(subject, job_id)
    return {"code": 0, "msg": "ok", "data": job}


@kb_query_router.post("/search-jobs/{job_id}/cancel", summary="取消异步知识库查询任务")
def cancel_search_job(job_id: str, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    subject = subject_from_user(user)
    job = cancel_job(subject, job_id)
    return {"code": 0, "msg": "ok", "data": job}


@kb_query_router.delete("/search-jobs/expired", summary="管理员清理过期知识库查询任务")
def cleanup_search_jobs(
    older_than_hours: int = Query(24, ge=1, le=24 * 30),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    result = cleanup_expired_jobs(subject, older_than_hours=older_than_hours)
    return {"code": 0, "msg": "ok", "data": result}


@kb_query_router.get("/search-jobs/{job_id}/results", summary="获取异步知识库查询任务结果")
def get_search_job_results(
    job_id: str,
    format: str = Query("json", description="json / jsonl"),
    user=Depends(require_auth_enabled()),
):
    subject = subject_from_user(user)
    job = assert_job_access(subject, job_id)
    jsonl_path = get_job_jsonl_path(job_id)
    if format == "jsonl" or job.get("job_type") == "ndjson":
        if jsonl_path is None:
            raise HTTPException(404, "job jsonl result not found")

        def iter_file():
            with jsonl_path.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            iter_file(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.results.jsonl"'},
        )

    data = get_job_result(job_id)
    if data is None:
        raise HTTPException(404, "job result not found")
    return JSONResponse(content=data)


@kb_query_router.get("/search-jobs/{job_id}/events", summary="SSE 订阅异步知识库查询任务状态")
async def stream_search_job_events(job_id: str, user=Depends(require_auth_enabled())):
    subject = subject_from_user(user)
    assert_job_access(subject, job_id)

    async def event_gen():
        last_status = None
        while True:
            job = get_job(job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"message": "job not found"}, ensure_ascii=False)}
                break
            status = job.get("status")
            if status != last_status:
                yield {"event": "progress", "data": json.dumps(job, ensure_ascii=False, default=str)}
                last_status = status
            if status in ("succeeded", "failed"):
                result = get_job_result(job_id)
                if result is not None:
                    yield {"event": "result", "data": json.dumps(result, ensure_ascii=False, default=str)}
                yield {"event": "done", "data": json.dumps({"status": status}, ensure_ascii=False)}
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())


@kb_query_router.put("/evals/golden-set", summary="管理员保存知识库评测集")
def put_golden_set(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    result = save_golden_set(
        subject,
        kb_id=body.get("kb_id"),
        knowledge_base=body.get("knowledge_base"),
        cases=list(body.get("cases") or []),
    )
    return {"code": 0, "msg": "ok", "data": result}


@kb_query_router.get("/evals/golden-set", summary="读取知识库评测集")
def get_golden_set(
    kb_id: Optional[str] = Query(None),
    knowledge_base: Optional[str] = Query(None),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    return {
        "code": 0,
        "msg": "ok",
        "data": load_golden_set(subject, kb_id=kb_id, knowledge_base=knowledge_base),
    }


@kb_query_router.post("/evals/run", summary="运行知识库评测")
def run_kb_eval(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    result = run_eval(
        subject,
        kb_id=body.get("kb_id"),
        knowledge_base=body.get("knowledge_base"),
        top_k=int(body.get("top_k") or 5),
    )
    return {"code": 0, "msg": "ok", "data": result}


@kb_query_router.post("/evals/gate", summary="运行知识库评测发布门禁")
def run_kb_eval_gate(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    result = run_gate(
        subject,
        kb_id=body.get("kb_id"),
        knowledge_base=body.get("knowledge_base"),
        top_k=int(body.get("top_k") or 10),
        min_recall=float(body.get("min_recall") or 0.95),
        min_mrr=float(body.get("min_mrr") or 0.85),
        max_zero_hit_rate=float(body.get("max_zero_hit_rate") or 0.05),
    )
    return {"code": 0, "msg": "ok", "data": result}


@kb_query_router.get("/evals/runs", summary="列出知识库评测历史")
def list_kb_eval_runs(
    kb_id: Optional[str] = Query(None),
    knowledge_base: Optional[str] = Query(None),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    return {
        "code": 0,
        "msg": "ok",
        "data": list_eval_runs(subject, kb_id=kb_id, knowledge_base=knowledge_base),
    }


@kb_query_router.get("/evals/trend", summary="查看知识库评测趋势")
def get_kb_eval_trend(
    kb_id: Optional[str] = Query(None),
    knowledge_base: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    return {
        "code": 0,
        "msg": "ok",
        "data": eval_trend(
            subject,
            kb_id=kb_id,
            knowledge_base=knowledge_base,
            limit=limit,
        ),
    }


@kb_query_router.get("/evals/runs/{run_id}", summary="读取知识库评测历史详情")
def get_kb_eval_run(
    run_id: str,
    kb_id: Optional[str] = Query(None),
    knowledge_base: Optional[str] = Query(None),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    subject = subject_from_user(user)
    return {
        "code": 0,
        "msg": "ok",
        "data": get_eval_run(
            subject,
            kb_id=kb_id,
            knowledge_base=knowledge_base,
            run_id=run_id,
        ),
    }

