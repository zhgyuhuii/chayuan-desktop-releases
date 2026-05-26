from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.db.repository import annotation_repository as repo
from chayuan.server.db.repository import route_context_repository


annotation_router = APIRouter(prefix="/annotation", tags=["Annotation / Evaluation"])


def _uid(user: Optional[Dict[str, Any]]) -> Optional[int]:
    try:
        return int((user or {}).get("id") or (user or {}).get("user_id"))
    except Exception:  # noqa: BLE001
        return None


class AnnotationTaskCreateBody(BaseModel):
    source: str = "manual"
    task_type: str = "qa_quality"
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    route_context_id: Optional[str] = None
    inputs: Dict[str, Any] = {}
    model_output: Dict[str, Any] = {}
    llm_prel_labels: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}
    priority: int = 0
    note: str = ""


class AnnotationTaskPatchBody(BaseModel):
    status: Optional[str] = None
    labels: Optional[Dict[str, Any]] = None
    review: Optional[Dict[str, Any]] = None
    error_tags: Optional[List[str]] = None
    note: Optional[str] = None


class AnnotationSampleRouteContextBody(BaseModel):
    route_context_id: str
    task_type: str = "rag_relevance"
    priority: int = 10
    note: str = ""


class AnnotationSampleKbAskBody(BaseModel):
    query: str
    ku_ids: List[str] = []
    top_k: int = 5
    result: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    task_type: str = "rag_relevance"
    priority: int = 10
    note: str = ""


class AnnotationFeedbackBody(BaseModel):
    task_id: Optional[str] = None
    source: str = "user_feedback"
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    source_ref: Dict[str, Any] = {}
    rating: Optional[int] = None
    quality: str = ""
    labels: Dict[str, Any] = {}
    error_tags: List[str] = []
    comment: str = ""
    create_task: bool = True
    task_type: str = "data_quality_feedback"
    inputs: Dict[str, Any] = {}
    model_output: Dict[str, Any] = {}


class AnnotationDatasetImportBody(BaseModel):
    items: List[Dict[str, Any]]
    source: str = "dataset_import"
    default_status: str = "pending"
    note: str = ""


class AnnotationDatasetMountBody(BaseModel):
    name: str
    description: str = ""
    task_type: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    sample_ids: List[str] = []
    scope_type: str = "user"
    scope_id: str = ""
    mount_modes: List[str] = ["preference", "fewshot", "retrieval_boost"]
    priority: int = 0
    max_items: int = 20
    max_tokens: int = 1600
    publish: bool = True


_TEMPLATE_SAMPLE_TEXTS = [
    "样例 根据知识库内容 合同解除需要满足哪些条件 样例原文片段 当事人协商一致 可以解除合同 出现法定解除情形时 也可以依法解除 样例回答 合同解除通常包括协商解除和法定解除两类",
    "样例 请依据知识库把当前段落改写得更正式 样例原文 这个事情比较重要 我们要尽快做 样例改写 该事项具有重要意义 建议尽快组织推进并形成闭环",
]


def _annotation_sample_text(value: Any) -> str:
    try:
        raw = json.dumps(value or {}, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        raw = str(value or "")
    raw = re.sub(r"annotation-template-sample-[\w-]+", "", raw.lower())
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw, flags=re.U)
    return re.sub(r"\s+", " ", raw).strip()


def _token_set(text: str) -> set:
    out = set()
    for token in re.split(r"\s+", text or ""):
        if len(token) >= 2:
            out.add(token)
        if re.search(r"[\u4e00-\u9fff]", token):
            for i in range(max(0, len(token) - 1)):
                out.add(token[i:i + 2])
    return out


def _similarity(a: str, b: str) -> float:
    aa, bb = _token_set(a), _token_set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(len(aa), len(bb))


def _is_template_sample_like(item: Dict[str, Any]) -> bool:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    if meta.get("is_template_sample") is True:
        return True
    if str(item.get("id") or "").lower().startswith("annotation-template-sample"):
        return True
    if str(item.get("source") or "").lower() == "annotation_template":
        return True
    text = _annotation_sample_text(item)
    return any(_similarity(text, sample) >= 0.82 for sample in _TEMPLATE_SAMPLE_TEXTS)


@annotation_router.post("/tasks", summary="创建标注任务")
def create_annotation_task(
    body: AnnotationTaskCreateBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    row = repo.create_task(
        source=body.source,
        task_type=body.task_type,
        inputs=body.inputs,
        model_output=body.model_output,
        llm_prel_labels=body.llm_prel_labels,
        target_type=body.target_type,
        target_id=body.target_id,
        route_context_id=body.route_context_id,
        meta=body.meta,
        priority=body.priority,
        note=body.note,
        created_by=_uid(user),
    )
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.post("/feedback", summary="提交数据质量反馈/多人标注")
def submit_annotation_feedback(
    body: AnnotationFeedbackBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    task_id = body.task_id
    created_task = None
    if not task_id and body.create_task:
        created_task = repo.create_task(
            source=body.source or "user_feedback",
            task_type=body.task_type or "data_quality_feedback",
            target_type=body.target_type,
            target_id=body.target_id,
            route_context_id=None,
            inputs=body.inputs or body.source_ref or {},
            model_output=body.model_output or {},
            llm_prel_labels={
                "needs_human_review": True,
                "suggested_task": body.task_type or "data_quality_feedback",
                "label_schema": {
                    "quality": "good|bad|partial|unsafe|irrelevant",
                    "rating": "-1|0|1 or 1-5",
                    "error_tags": "string[]",
                    "comment": "string",
                },
            },
            meta={
                "created_from": "annotation.feedback",
                "source_ref": body.source_ref,
            },
            priority=20 if body.quality == "bad" or (body.rating is not None and body.rating < 0) else 8,
            note=body.comment or "用户在业务流程中提交的数据质量反馈。",
            created_by=_uid(user),
        )
        task_id = created_task.get("id")

    try:
        feedback, updated_task = repo.create_feedback(
            task_id=task_id,
            source=body.source,
            target_type=body.target_type,
            target_id=body.target_id,
            source_ref=body.source_ref,
            rating=body.rating,
            quality=body.quality,
            labels=body.labels,
            error_tags=body.error_tags,
            comment=body.comment,
            created_by=_uid(user),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "feedback": feedback,
            "task": updated_task or created_task,
        },
    }


@annotation_router.get("/feedback", summary="查询数据质量反馈/多人标注记录")
def list_annotation_feedback(
    task_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    items, total = repo.list_feedback(
        task_id=task_id,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
        offset=offset,
    )
    return {"code": 0, "msg": "ok", "data": {"items": items, "total": total}}


@annotation_router.post("/sample/kb-ask", summary="从 KB 查询结果创建标注样本")
def sample_from_kb_ask(
    body: AnnotationSampleKbAskBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    blocks = body.result.get("results") if isinstance(body.result, dict) else []
    if not isinstance(blocks, list):
        blocks = []

    retrieved_items: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        ku_id = block.get("ku_id")
        kind = block.get("kind")
        if kind == "document":
            hits = block.get("results") or []
        elif kind == "vector":
            hits = block.get("hits") or []
        else:
            hits = block.get("rows") or block.get("results") or []
        if not isinstance(hits, list):
            hits = []
        for rank, hit in enumerate(hits[: max(1, min(int(body.top_k or 5), 20))], start=1):
            if not isinstance(hit, dict):
                continue
            retrieved_items.append({
                "ku_id": ku_id,
                "kind": kind,
                "rank": rank,
                "id": hit.get("id"),
                "file_name": hit.get("file_name") or hit.get("source"),
                "page": hit.get("page"),
                "chunk_index": hit.get("chunk_index"),
                "score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
                "snippet": hit.get("snippet") or hit.get("content") or hit.get("text") or "",
                "metadata": hit.get("metadata") or {},
            })

    row = repo.create_task(
        source="kb_ask",
        task_type=body.task_type,
        target_type="knowledge_base",
        target_id=(body.ku_ids[0] if body.ku_ids else "")[:128],
        route_context_id=None,
        inputs={
            "query": query,
            "ku_ids": body.ku_ids,
            "top_k": body.top_k,
            "options": body.options,
        },
        model_output={
            "result": body.result,
            "retrieved_items": retrieved_items,
            "retrieved_count": len(retrieved_items),
        },
        llm_prel_labels={
            "suggested_task": body.task_type,
            "needs_human_review": True,
            "label_schema": {
                "answer_correct": "boolean",
                "retrieval_relevant": "boolean",
                "citation_supported": "boolean",
                "missing_evidence": "boolean",
                "relevance_score": "1-5",
            },
        },
        meta={
            "created_from": "knowledge_universe.ask",
            "block_count": len(blocks),
            "ku_count": len(body.ku_ids),
        },
        priority=body.priority,
        note=body.note or "从 KB 查询结果自动采样，适合 RAG 相关性、引用正确性和回答质量评估。",
        created_by=_uid(user),
    )
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.post("/sample/route-context", summary="从 route_context 创建标注样本")
def sample_from_route_context(
    body: AnnotationSampleRouteContextBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    ctx = route_context_repository.get_for_user(
        context_id=body.route_context_id,
        user_id=_uid(user),
    )
    if not ctx:
        raise HTTPException(404, "route_context not found")

    context = ctx.get("context") or {}
    anchor = ctx.get("anchor") or {}
    meta = ctx.get("meta") or {}
    query = context.get("query") or ctx.get("title") or ""
    row = repo.create_task(
        source="route_context",
        task_type=body.task_type,
        target_type=ctx.get("target_type"),
        target_id=ctx.get("target_id"),
        route_context_id=ctx.get("id"),
        inputs={
            "query": query,
            "route": ctx.get("route"),
            "context": context,
            "anchor": anchor,
            "summary": ctx.get("summary") or "",
        },
        model_output={
            "retrieved_doc_ids": anchor.get("result_doc_ids") or [],
            "first_doc_id": anchor.get("first_doc_id"),
            "first_match_anchor": anchor.get("first_match_anchor"),
        },
        llm_prel_labels={
            "suggested_task": body.task_type,
            "needs_human_review": True,
            "label_schema": {
                "is_relevant": "boolean",
                "relevance_score": "1-5",
                "citation_correct": "boolean",
                "missing_result": "boolean",
            },
        },
        meta={
            "route_context": ctx,
            "result_count": meta.get("result_count"),
            "source": ctx.get("source"),
        },
        priority=body.priority,
        note=body.note or f"从 route_context {ctx.get('id')} 生成",
        created_by=_uid(user),
    )
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.get("/tasks", summary="列出标注任务")
def list_annotation_tasks(
    status: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    mine: bool = Query(False, description="只看分配给我的任务"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    items, total = repo.list_tasks(
        status=status,
        task_type=task_type,
        source=source,
        assignee_id=_uid(user) if mine else None,
        limit=limit,
        offset=offset,
    )
    return {"code": 0, "msg": "ok", "data": {"items": items, "total": total}}


@annotation_router.get("/tasks/{task_id}", summary="读取标注任务详情")
def get_annotation_task(
    task_id: str,
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    row = repo.get_task(task_id)
    if not row:
        raise HTTPException(404, "annotation task not found")
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.post("/tasks/{task_id}/claim", summary="领取标注任务")
def claim_annotation_task(
    task_id: str,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    row = repo.update_task(
        task_id=task_id,
        actor_id=_uid(user),
        status="in_progress",
        assignee_id=_uid(user),
    )
    if not row:
        raise HTTPException(404, "annotation task not found")
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.patch("/tasks/{task_id}", summary="提交标注标签")
def patch_annotation_task(
    task_id: str,
    body: AnnotationTaskPatchBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    row = repo.update_task(
        task_id=task_id,
        actor_id=_uid(user),
        status=body.status,
        labels=body.labels,
        review=body.review,
        error_tags=body.error_tags,
        note=body.note,
    )
    if not row:
        raise HTTPException(404, "annotation task not found")
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.post("/tasks/{task_id}/review", summary="复审/仲裁标注任务")
def review_annotation_task(
    task_id: str,
    body: AnnotationTaskPatchBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    status = body.status or "approved"
    row = repo.update_task(
        task_id=task_id,
        actor_id=_uid(user),
        status=status,
        labels=body.labels,
        review=body.review or {"decision": status},
        error_tags=body.error_tags,
        note=body.note,
        reviewer_id=_uid(user),
    )
    if not row:
        raise HTTPException(404, "annotation task not found")
    return {"code": 0, "msg": "ok", "data": row}


@annotation_router.get("/datasets/export", summary="导出已通过标注样本(JSON/JSONL)")
def export_annotation_dataset(
    task_type: Optional[str] = Query(None),
    status: str = Query("approved", description="逗号分隔状态,默认 approved"),
    fmt: str = Query("json", pattern="^(json|jsonl)$"),
    limit: int = Query(1000, ge=1, le=10000),
    _user=Depends(require_auth_enabled()),
):
    statuses = [x.strip() for x in status.split(",") if x.strip()]
    rows = repo.export_dataset(statuses=statuses or ["approved"], task_type=task_type, limit=limit)
    if fmt == "jsonl":
        def _iter():
            for row in rows:
                yield json.dumps(row, ensure_ascii=False) + "\n"
        return StreamingResponse(
            _iter(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="annotation-dataset.jsonl"'},
        )
    return {"code": 0, "msg": "ok", "data": {"items": rows, "total": len(rows)}}


@annotation_router.get("/usage/summary", summary="查看标注数据在线使用闭环状态")
def annotation_usage_summary(
    limit: int = Query(1000, ge=1, le=5000),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    return {"code": 0, "msg": "ok", "data": repo.usage_summary(limit=limit)}


@annotation_router.get("/usage/samples", summary="查询已通过且可被线上链路使用的标注样本")
def annotation_usage_samples(
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    rows = repo.list_usable_samples(
        target_type=target_type,
        target_id=target_id,
        task_type=task_type,
        limit=limit,
    )
    return {"code": 0, "msg": "ok", "data": {"items": rows, "total": len(rows)}}


@annotation_router.post("/datasets/mount", summary="把已审核标注样本挂载到问答运行时")
def mount_annotation_dataset(
    body: AnnotationDatasetMountBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    from chayuan.server.db.repository import data_mount_repository

    source_filter: Dict[str, Any] = {}
    if body.sample_ids:
        source_filter["sample_ids"] = body.sample_ids
    if body.task_type:
        source_filter["task_type"] = body.task_type
    if body.target_type:
        source_filter["target_type"] = body.target_type
    if body.target_id:
        source_filter["target_id"] = body.target_id

    scope_id = body.scope_id
    if body.scope_type == "user" and not scope_id:
        scope_id = str(_uid(user) or "")
    if body.scope_type == "global" and str((user or {}).get("role") or "").lower() != "admin":
        raise HTTPException(403, "只有管理员可以创建全局训练数据挂载")
    if body.scope_type == "user" and scope_id not in ("", str(_uid(user) or "")):
        raise HTTPException(403, "只能为当前用户创建个人挂载")

    mount = data_mount_repository.create_mount(
        name=body.name,
        description=body.description,
        scope_type=body.scope_type,
        scope_id=scope_id,
        source_filter=source_filter,
        mount_modes=body.mount_modes,
        priority=body.priority,
        max_items=body.max_items,
        max_tokens=body.max_tokens,
        created_by=_uid(user),
    )
    if body.publish:
        mount = data_mount_repository.publish_mount(mount["id"], actor_id=_uid(user)) or mount
    return {"code": 0, "msg": "ok", "data": mount}


@annotation_router.post("/datasets/import", summary="导入标注样本(JSON/JSONL 解析后数据)")
def import_annotation_dataset(
    body: AnnotationDatasetImportBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    if not body.items:
        raise HTTPException(400, "items 不能为空")
    if len(body.items) > 5000:
        raise HTTPException(400, "单次最多导入 5000 条")

    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    skipped = 0
    for idx, item in enumerate(body.items):
        if not isinstance(item, dict):
            errors.append({"index": idx, "error": "item must be object"})
            continue
        if _is_template_sample_like(item):
            skipped += 1
            continue
        task_type = str(item.get("task_type") or item.get("type") or "imported_sample").strip()
        inputs = item.get("inputs")
        if inputs is None:
            inputs = item.get("input")
        if inputs is None:
            inputs = item.get("query")
        model_output = item.get("model_output")
        if model_output is None:
            model_output = item.get("output")
        if not isinstance(inputs, dict):
            inputs = {"value": inputs}
        if not isinstance(model_output, dict):
            model_output = {"value": model_output}
        try:
            row = repo.create_task(
                source=str(item.get("source") or body.source or "dataset_import"),
                task_type=task_type,
                target_type=item.get("target_type"),
                target_id=item.get("target_id"),
                route_context_id=item.get("route_context_id"),
                inputs=inputs,
                model_output=model_output,
                llm_prel_labels=item.get("llm_prel_labels") if isinstance(item.get("llm_prel_labels"), dict) else {},
                meta={
                    **(item.get("meta") if isinstance(item.get("meta"), dict) else {}),
                    "imported_from": "annotation.datasets.import",
                    "import_source_id": item.get("id"),
                },
                priority=int(item.get("priority") or 0),
                note=str(item.get("note") or body.note or "导入的标注样本"),
                created_by=_uid(user),
            )
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else None
            review = item.get("review") if isinstance(item.get("review"), dict) else None
            error_tags = item.get("error_tags") if isinstance(item.get("error_tags"), list) else None
            status = str(item.get("status") or body.default_status or "pending")[:24]
            row = repo.update_task(
                task_id=row["id"],
                actor_id=_uid(user),
                status=status,
                labels=labels,
                review=review,
                error_tags=[str(x) for x in error_tags] if error_tags is not None else None,
                note=row.get("note"),
            ) or row
            created.append(row)
        except Exception as e:  # noqa: BLE001
            errors.append({"index": idx, "error": f"{type(e).__name__}: {e}"})

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "created": len(created),
            "failed": len(errors),
            "skipped": skipped,
            "items": created[:100],
            "errors": errors[:100],
        },
    }


@annotation_router.get("/plan", summary="LangChain 标注训练路线")
def annotation_plan(_user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "stages": [
                "P0: 采样与人工/LLM 预标注",
                "P1: LangChain/LangSmith 评测集与 evaluator",
                "P2: RAG chunk 相关性与 reranker hard negatives",
                "P3: 安全分类 / 意图识别小模型",
                "P4: 稳定高频任务的大模型微调",
            ],
            "formats": ["json", "jsonl", "preference_pair", "reranker_pair"],
        },
    }
