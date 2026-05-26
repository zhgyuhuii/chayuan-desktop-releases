"""同步任务的 in-process 注册表 + 事件总线。

架构选择:
- 单机进程内字典 + asyncio.Queue 订阅。够用、零依赖、单元可测。
- 不上 Redis/Celery 的理由:同步任务天然有"宿主"语义(必须能 stream 进度回到
  本机的 SSE 连接),换分布式队列要么强制粘性路由,要么再加一层 fan-out — 划不来。
- 老 job 自动 GC:`gc()` 调用方在创建新 job 前调一次,把超过 _GC_TTL_SEC 的清掉。
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.kb.remote_sources.job")

_GC_TTL_SEC = 30 * 60  # 30 分钟未活动的 job 清掉


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclasses.dataclass
class JobEvent:
    """SSE 帧 payload。type 是 event 名,data 是 JSON 字典。

    type 取值约定:
      - meta:同步开始,带 total/filter/source/kb_name
      - progress:每个文件 done/failed 推一次,带 processed/total/last
      - log:辅助文本日志(列表枚举/警告等)
      - status:状态变更(queued→running→done/failed/cancelled)
    """
    type: str
    data: Dict[str, Any]
    ts: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass
class Job:
    id: str
    status: JobStatus
    kb_name: str
    source_kind: str
    created_at: float
    updated_at: float
    total: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_done: int = 0
    last_file: str = ""
    error: str = ""
    cancel_requested: bool = False

    def snapshot(self) -> Dict[str, Any]:
        # 只暴露必要字段;避免把 Future / thread 这类不可序列化的东西漏出去
        return {
            "id": self.id,
            "status": self.status.value,
            "kb_name": self.kb_name,
            "source_kind": self.source_kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total": self.total,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "bytes_done": self.bytes_done,
            "last_file": self.last_file,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
        }


class JobManager:
    """所有 sync job 的中枢。线程安全 — 后端线程池 push 事件,async SSE 端点 pop。

    每个 job 自带一组订阅者 queue:HTTP SSE 连接订阅时 attach 一个 asyncio.Queue,
    断开时 detach。push 时 fan-out 到所有 queue;新订阅者一上来先重放 last snapshot
    + 事件 backlog(最多 N 条),刷新页面也不会丢历史。
    """

    _BACKLOG_MAX = 200

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        # job_id → list[JobEvent]  (最近 N 条,新来的订阅者重放用)
        self._backlog: Dict[str, List[JobEvent]] = {}
        # job_id → set[asyncio.Queue]
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # asyncio loop 引用 — sync 线程要往 asyncio.Queue 投递必须 call_soon_threadsafe
        self._loops: Dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}

    # —— job lifecycle ——

    def create(self, kb_name: str, source_kind: str) -> Job:
        self._gc()
        job = Job(
            id=uuid.uuid4().hex,
            status=JobStatus.QUEUED,
            kb_name=kb_name,
            source_kind=source_kind,
            created_at=time.time(),
            updated_at=time.time(),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._backlog[job.id] = []
            self._subscribers[job.id] = []
        logger.info("job %s created kb=%s source=%s", job.id, kb_name, source_kind)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        with self._lock:
            return list(self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None or j.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                return False
            j.cancel_requested = True
            j.updated_at = time.time()
        self.push(job_id, JobEvent(type="status", data={"status": j.status.value, "cancel_requested": True}))
        return True

    def set_status(self, job_id: str, status: JobStatus, *, error: str = "") -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            j.status = status
            j.updated_at = time.time()
            if error:
                j.error = error
        self.push(job_id, JobEvent(type="status", data={"status": status.value, "error": error}))

    def update(self, job_id: str, **patch: Any) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            for k, v in patch.items():
                if hasattr(j, k):
                    setattr(j, k, v)
            j.updated_at = time.time()

    # —— 事件总线 ——

    def push(self, job_id: str, event: JobEvent) -> None:
        with self._lock:
            backlog = self._backlog.get(job_id)
            subs = list(self._subscribers.get(job_id, []))
        if backlog is not None:
            backlog.append(event)
            if len(backlog) > self._BACKLOG_MAX:
                del backlog[: len(backlog) - self._BACKLOG_MAX]
        for q in subs:
            self._safe_put(q, event)

    def _safe_put(self, q: asyncio.Queue, event: JobEvent) -> None:
        loop = self._loops.get(q)
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception as e:  # noqa: BLE001
            logger.debug("subscriber put failed: %r", e)

    async def subscribe(self, job_id: str) -> asyncio.Queue:
        """订阅 job 事件流。订阅时立刻把 backlog 发回。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(q)
            self._loops[q] = loop
            backlog = list(self._backlog.get(job_id, []))
        for ev in backlog:
            await q.put(ev)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            try:
                subs.remove(q)
            except ValueError:
                pass
            self._loops.pop(q, None)

    # —— GC ——

    def _gc(self) -> None:
        cutoff = time.time() - _GC_TTL_SEC
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.updated_at < cutoff and j.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)]
            for jid in stale:
                self._jobs.pop(jid, None)
                self._backlog.pop(jid, None)
                self._subscribers.pop(jid, None)


_singleton: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _singleton
    if _singleton is None:
        _singleton = JobManager()
    return _singleton
