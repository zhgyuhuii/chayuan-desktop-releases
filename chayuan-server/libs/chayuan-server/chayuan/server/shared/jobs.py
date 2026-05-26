"""统一异步任务接口（Arq wrapper）。

项目已经有 ``ingest_queue.backend`` 承担 KB 上传的异步入库；本模块把它做成
**泛化**的"submit→status→result"三接口，供 N-3（RAPTOR/GraphRAG build）、
N-5（RAGAS eval）等后续任务共用，避免重复造轮子。

用法（提交）：
    from chayuan.server.shared.jobs import submit_job
    task_id, status = submit_job("raptor_build", {"kb_name": "x"})
    # → ("raptor_build:abcd1234", "enqueued")
    # 若 Arq 不可用或 INGEST_ASYNC_ENABLED=false → ("...", "sync_pending")

用法（查询）：
    from chayuan.server.shared.jobs import get_job_status
    status = get_job_status(task_id)

Worker 端注册：在 ``ingest_queue.backend.worker_settings`` 的 functions 里
添加我们的任务函数即可（见 ingest_queue/tasks.py）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("chayuan.shared.jobs")


def submit_job(name: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    """提交一个后台任务。

    - 返回 ``(task_id, status)``；status 取值：enqueued / sync_pending / error
    - 若 Arq / Redis 不可用，底层会自动 fallback（由 ingest_queue.backend 实现）
    """
    try:
        from chayuan.server.ingest_queue.backend import enqueue_task
        return enqueue_task(name, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("submit_job(%s) 失败：%r", name, e)
        return ("", "error")


def get_job_status(task_id: str) -> Optional[Dict[str, Any]]:
    """读任务状态；任务不存在 / Redis 不可用 → None。"""
    try:
        from chayuan.server.ingest_queue.backend import get_task_status
        return get_task_status(task_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("get_job_status 失败：%r", e)
        return None


def async_enabled() -> bool:
    """全局开关：用户 basic_settings.INGEST_ASYNC_ENABLED + Redis 可用。"""
    try:
        from chayuan.settings import Settings
        if not bool(getattr(Settings.basic_settings, "INGEST_ASYNC_ENABLED", False)):
            return False
        from chayuan.server.ingest_queue.backend import _redis_url  # type: ignore
        return bool(_redis_url())
    except Exception:  # noqa: BLE001
        return False
