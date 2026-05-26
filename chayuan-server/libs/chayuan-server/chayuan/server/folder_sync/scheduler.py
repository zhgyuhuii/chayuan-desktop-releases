"""95-4:apscheduler 包装,按 job.interval 触发同步。

用 BackgroundScheduler(线程池),不阻塞 FastAPI 主 event loop。
单例,所有 job 共享一个 scheduler。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("chayuan.folder_sync.scheduler")

_SCHEDULER = None
_SCHED_LOCK = threading.Lock()


def get_scheduler():
    """返回 BackgroundScheduler 单例;首次调用时启动。"""
    global _SCHEDULER  # noqa: PLW0603
    with _SCHED_LOCK:
        if _SCHEDULER is not None:
            return _SCHEDULER
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError as e:
            raise RuntimeError(
                "apscheduler 未安装;pip install apscheduler"
            ) from e
        _SCHEDULER = BackgroundScheduler(daemon=True)
        _SCHEDULER.start()
    return _SCHEDULER


def schedule_job(job_id: int, interval_seconds: int, run_fn) -> None:
    """添加 / 更新一个定时任务。job_id 重复时替换。"""
    sched = get_scheduler()
    sched.add_job(
        run_fn,
        trigger="interval", seconds=int(interval_seconds),
        id=str(job_id), replace_existing=True,
        misfire_grace_time=60, coalesce=True, max_instances=1,
    )
    logger.info("[folder_sync] job %d scheduled every %ds",
                job_id, interval_seconds)


def unschedule_job(job_id: int) -> bool:
    """移除任务。"""
    if _SCHEDULER is None:
        return False
    try:
        _SCHEDULER.remove_job(str(job_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def shutdown() -> None:
    """关闭 scheduler(进程退出时)。"""
    global _SCHEDULER  # noqa: PLW0603
    with _SCHED_LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        _SCHEDULER = None
