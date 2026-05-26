"""异步入库队列（基于 Arq + Redis）。

对外 API：
- ``enqueue_upload_docs(...)``：HTTP 路由调用；先同步把文件落盘，再入队，返回 task_id。
- ``get_task_status(task_id)``：查询任务状态，返回 dict（queued / running / success / failed）。
- ``worker_settings()``：提供给 ``chayuan worker`` 命令；Arq 通过它启动后台 worker。

为什么不用 FastAPI 的 `BackgroundTasks`：后者在同一 uvicorn worker 进程里跑，
5000 并发上传会吃光 API 的 event loop；Arq 单独起 worker 进程，与 API 隔离。

缺 `arq` 包时：``enqueue_upload_docs`` 会**自动降级**为同步调用原 handler，
外加在返回里说明 `queue=disabled`，保证即使不装 arq，API 依然可用。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import UploadFile

from chayuan.server.ingest_queue.backend import (  # noqa: F401
    enqueue_task,
    get_task_status,
    request_task_cancel,
    worker_settings,
)

logger = logging.getLogger("chayuan.ingest_queue")


def enqueue_upload_docs(
    *,
    files: List[UploadFile],
    knowledge_base_name: str,
    override: bool,
    to_vector_store: bool,
    chunk_size: int,
    chunk_overlap: int,
    zh_title_enhance: bool,
    docs: str,
    user_id: Optional[int],
) -> Dict[str, Any]:
    """先把文件落盘，再把"剩余的耗时操作"（解析 + 向量化）扔到队列。

    返回：{ code, msg, data: { task_id, saved_files, failed_saves, queue } }
    """
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.kb_doc_api import _save_files_in_thread
    from chayuan.server.knowledge_base.utils import validate_kb_name
    from chayuan.server.utils import BaseResponse

    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me").dict()
    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(
            code=404, msg=f"未找到知识库 {knowledge_base_name}"
        ).dict()

    # 1) 先落盘（阻塞，必须在 HTTP 生命周期内完成，否则 UploadFile 会被清理）
    failed_saves: Dict[str, str] = {}
    saved: List[str] = []
    file_hashes: Dict[str, str] = {}
    for r in _save_files_in_thread(
        files, knowledge_base_name=knowledge_base_name, override=override,
    ):
        fname = r["data"]["file_name"]
        if r["code"] != 200:
            failed_saves[fname] = r["msg"]
        else:
            saved.append(fname)
            file_hashes[fname] = r["data"].get("file_hash") or ""

    if not to_vector_store:
        return {
            "code": 200,
            "msg": "文件已上传（未向量化）。",
            "data": {
                "saved_files": saved,
                "failed_saves": failed_saves,
                "task_id": None,
                "queue": "skipped",
            },
        }

    # 2) 入队。缺 arq → 立即同步跑；不影响可用性，只是失去并发优势
    import json as _json
    try:
        docs_dict = _json.loads(docs) if docs else {}
    except Exception:
        docs_dict = {}

    payload = {
        "kb_name": knowledge_base_name,
        "file_names": saved,
        "chunk_size": int(chunk_size),
        "chunk_overlap": int(chunk_overlap),
        "zh_title_enhance": bool(zh_title_enhance),
        "docs": docs_dict,
        "file_hashes": file_hashes,
        "user_id": user_id,
    }
    task_id, queue_state = enqueue_task("ingest_upload_docs", payload)
    resp: Dict[str, Any] = {
        "code": 200 if queue_state in ("queued", "sync", "duplicate") else 500,
        "msg": "任务已入队" if queue_state == "queued" else (
            "检测到重复任务，已复用已有 task_id" if queue_state == "duplicate" else
            "Arq/Redis 不可用，已降级为同步执行" if queue_state == "sync" else "入队失败"
        ),
        "data": {
            "saved_files": saved,
            "failed_saves": failed_saves,
            "task_id": task_id,
            "queue": queue_state,
        },
    }
    # 降级到 sync 时，把"去哪配 Redis"指引透给前端 / 运维
    if queue_state == "sync":
        from chayuan.server.shared.redis_health import redis_hint
        resp["data"]["redis_hint"] = redis_hint()
    return resp


def enqueue_reindex_kb_files(
    *,
    knowledge_base_name: str,
    file_names: List[str],
    chunk_size: int,
    chunk_overlap: int,
    zh_title_enhance: bool,
    user_id: Optional[int],
) -> Dict[str, Any]:
    """提交文件级重新向量化任务。

    与上传入库共用同一个 Arq/Redis 后端；Redis/Arq 不可用时同步降级。
    """
    from chayuan.server.knowledge_base.utils import validate_kb_name
    from chayuan.server.utils import BaseResponse

    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me").dict()
    if not file_names:
        return BaseResponse(code=400, msg="file_names 不能为空").dict()

    payload = {
        "kb_name": knowledge_base_name,
        "file_names": file_names,
        "chunk_size": int(chunk_size),
        "chunk_overlap": int(chunk_overlap),
        "zh_title_enhance": bool(zh_title_enhance),
        "user_id": user_id,
    }
    from chayuan.server.ingest_queue.backend import enqueue_task_background
    task_id, queue_state = enqueue_task_background("reindex_kb_files", payload)
    resp: Dict[str, Any] = {
        "code": 200 if queue_state in ("queued", "sync", "local", "duplicate") else 500,
        "msg": "重新向量化任务已入队" if queue_state == "queued" else (
            "检测到重复任务，已复用已有 task_id" if queue_state == "duplicate" else
            "Redis/Arq 不可用，已使用本进程后台任务" if queue_state == "local"
            else "Arq/Redis 不可用，已同步完成重新向量化" if queue_state == "sync" else "入队失败"
        ),
        "data": {
            "task_id": task_id,
            "queue": queue_state,
            "file_count": len(file_names),
        },
    }
    if queue_state == "sync":
        from chayuan.server.shared.redis_health import redis_hint
        resp["data"]["redis_hint"] = redis_hint()
    return resp
