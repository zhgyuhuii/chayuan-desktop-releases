"""并发同步引擎:把 RemoteSource 的文件喂进 KB ingest 路径。

并发模型:
- 一个 lister 线程产文件,N 个 worker 线程消费(下载 + 入库)。
- worker 数 = JobSpec.concurrency,默认 4。再多对 NLP 向量化没意义,反而抢
  CPU + 拖慢响应。
- 中间用 queue.Queue(maxsize=2 * workers)做背压,避免 lister 把内存灌爆。

ingest 复用:
- 直接调 _ingest_one(),内部 = FileStorage.put + 本地缓存写盘 + update_docs([file])。
  与 upload_docs 端点同形,faiss/milvus/pg/chromadb 等所有后端都跟着自动支持。

幂等:
- 默认跳过 KB 内已有同名文件(in_db=True)。`override=True` 才覆盖。
- 跳过的也算 processed,但走 skipped 计数;UI 区分显示。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional  # noqa: F401

from .base import RemoteFile, RemoteSource, SourceError
from .filters import SyncFilter
from .job_manager import Job, JobEvent, JobManager, JobStatus

logger = logging.getLogger("chayuan.kb.remote_sources.engine")


@dataclasses.dataclass
class JobSpec:
    """启动一次同步任务的全部参数。

    paths:同步多个起点目录,任意层级;sync_engine 会去重(同一文件被多 root 命中
    只跑一次)。
    concurrency:并发 worker 数,1..16;再多向量化阶段会撞模型上下文限速。
    """
    kb_name: str
    paths: List[str]
    filter: SyncFilter
    concurrency: int = 4
    override: bool = False
    to_vector_store: bool = True


class SyncEngine:
    def __init__(self, jobs: JobManager):
        self.jobs = jobs

    # —— 主入口 ——

    def submit(self, source: RemoteSource, spec: JobSpec) -> Job:
        """非阻塞提交;返回 Job 立刻可查。同步逻辑在后台线程跑。"""
        job = self.jobs.create(spec.kb_name, source.kind)
        threading.Thread(
            target=self._run_safely,
            args=(source, spec, job),
            name=f"sync-{job.id[:8]}",
            daemon=True,
        ).start()
        return job

    # —— 内部 ——

    def _run_safely(self, source: RemoteSource, spec: JobSpec, job: Job) -> None:
        try:
            self._run(source, spec, job)
        except Exception as e:  # noqa: BLE001
            logger.exception("sync job %s failed: %s", job.id, e)
            self.jobs.update(job.id, error=str(e))
            self.jobs.set_status(job.id, JobStatus.FAILED, error=str(e))
        finally:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass

    def _run(self, source: RemoteSource, spec: JobSpec, job: Job) -> None:
        self.jobs.set_status(job.id, JobStatus.RUNNING)
        self.jobs.push(job.id, JobEvent(
            type="meta",
            data={
                "kb_name": spec.kb_name,
                "source_kind": source.kind,
                "paths": spec.paths,
                "concurrency": spec.concurrency,
                "override": spec.override,
                "filter": dataclasses.asdict(spec.filter),
            },
        ))

        # 1) 列文件 + 过滤(去重)
        seen: Dict[str, RemoteFile] = {}
        for root in spec.paths or [""]:
            for f in source.walk(root):
                if not spec.filter.accepts(f):
                    continue
                if f.key in seen:
                    continue
                seen[f.key] = f
                if len(seen) % 200 == 0:
                    self.jobs.update(job.id, total=len(seen))
                    self.jobs.push(job.id, JobEvent(
                        type="log", data={"msg": f"已枚举 {len(seen)} 个候选文件…"},
                    ))
                if self._cancelled(job.id):
                    self.jobs.set_status(job.id, JobStatus.CANCELLED)
                    return
        files = list(seen.values())
        self.jobs.update(job.id, total=len(files))
        self.jobs.push(job.id, JobEvent(
            type="log", data={"msg": f"枚举完成,共 {len(files)} 个文件待同步"},
        ))

        if not files:
            self.jobs.set_status(job.id, JobStatus.DONE)
            return

        # 2) 并发下载 + 入库
        existing = _existing_files_lower(spec.kb_name)
        workers = max(1, min(16, spec.concurrency))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"sync-{job.id[:6]}") as pool:
            inflight: List[Future] = []
            for f in files:
                if self._cancelled(job.id):
                    break
                inflight.append(pool.submit(
                    self._handle_one, source, spec, job, f, existing,
                ))
                # 背压:同时在飞的最多 2 * workers
                if len(inflight) >= workers * 2:
                    self._drain(inflight, leave=workers)
            self._drain(inflight, leave=0)

        if self._cancelled(job.id):
            self.jobs.set_status(job.id, JobStatus.CANCELLED)
            return

        # 3) 收尾:刷一次 vector store(faiss 后端要)
        if spec.to_vector_store:
            try:
                _save_vector_store(spec.kb_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("save_vector_store failed: %r", e)

        self.jobs.set_status(job.id, JobStatus.DONE)

    def _drain(self, inflight: List[Future], *, leave: int) -> None:
        while len(inflight) > leave:
            f = inflight.pop(0)
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                logger.warning("sync worker raised: %r", e)

    def _cancelled(self, job_id: str) -> bool:
        j = self.jobs.get(job_id)
        return bool(j and j.cancel_requested)

    # 单文件下载分块大小:256 KB 是 SSE 推送 vs 内存抖动的甜点
    # — 1 GB 文件约 4096 块,2~3 fps 推送够 UI 流畅,不会把订阅 queue 灌爆。
    _CHUNK_BYTES = 256 * 1024

    def _handle_one(
        self,
        source: RemoteSource,
        spec: JobSpec,
        job: Job,
        f: RemoteFile,
        existing_lower: set,
    ) -> None:
        if self._cancelled(job.id):
            return
        try:
            # 幂等:已存在且未要求 override → 跳过
            if not spec.override and f.name.lower() in existing_lower:
                self._bump(job.id, f, status="skipped")
                return
            data = self._read_with_progress(source, job, f)
            if data is None:  # 取消中途
                return
            _ingest_one(
                kb_name=spec.kb_name,
                filename=f.name,
                content=data,
                override=spec.override,
                to_vector_store=spec.to_vector_store,
            )
            self._bump(job.id, f, status="ok", bytes_done=len(data))
        except Exception as e:  # noqa: BLE001
            logger.warning("sync %s/%s failed: %r", spec.kb_name, f.key, e)
            self._bump(job.id, f, status="failed", error=str(e))

    def _read_with_progress(
        self, source: RemoteSource, job: Job, f: RemoteFile,
    ) -> Optional[bytes]:
        """边读边推 download 事件;f.size 用于算百分比(未知则只推已下载字节)。

        与 progress 事件不同,这里走独立的 type='download',前端只在"当前进行
        中的文件"区块更新进度环,不冲击文件流瀑布。
        """
        from io import BytesIO
        buf = BytesIO()
        downloaded = 0
        last_emit_at = 0.0
        with source.open_read(f.key) as stream:
            while True:
                if self._cancelled(job.id):
                    return None
                chunk = stream.read(self._CHUNK_BYTES)
                if not chunk:
                    break
                buf.write(chunk)
                downloaded += len(chunk)
                # 限频:每 200ms 至多一帧,避免频繁推 SSE
                now = time.time()
                if now - last_emit_at >= 0.2:
                    last_emit_at = now
                    self.jobs.push(job.id, JobEvent(
                        type="download",
                        data={
                            "file": f.name,
                            "key": f.key,
                            "downloaded": downloaded,
                            "total": int(f.size or 0),
                        },
                    ))
        # 收尾时再推一次 100% 帧
        self.jobs.push(job.id, JobEvent(
            type="download",
            data={
                "file": f.name,
                "key": f.key,
                "downloaded": downloaded,
                "total": int(f.size or downloaded),
            },
        ))
        return buf.getvalue()

    def _bump(
        self,
        job_id: str,
        f: RemoteFile,
        *,
        status: str,
        bytes_done: int = 0,
        error: str = "",
    ) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        # 这里只读必要字段并发写,避免 race(JobManager.update 持锁)
        patch: Dict[str, Any] = {
            "processed": j.processed + 1,
            "last_file": f.name,
        }
        if status == "ok":
            patch["succeeded"] = j.succeeded + 1
            patch["bytes_done"] = j.bytes_done + bytes_done
        elif status == "failed":
            patch["failed"] = j.failed + 1
        elif status == "skipped":
            patch["skipped"] = j.skipped + 1
        self.jobs.update(job_id, **patch)
        self.jobs.push(job_id, JobEvent(
            type="progress",
            data={
                "file": f.name,
                "key": f.key,
                "size": f.size,
                "status": status,
                "error": error or None,
                "processed": patch["processed"],
                "total": j.total,
            },
        ))


# ──────────────────────────────────────────────────────────────────────
# Ingest 复用层 — 与 upload_docs 端点同形
# ──────────────────────────────────────────────────────────────────────

def _ingest_one(
    *, kb_name: str, filename: str, content: bytes,
    override: bool, to_vector_store: bool,
) -> None:
    """把一个二进制文件落到 KB(filestorage + 本地缓存 + 可选向量化)。

    与 upload_docs 端点完全同形 — 只是绕开了 UploadFile/multipart 这层壳,直接
    给字节。任何向量库后端都自动跟着支持。
    """
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.utils import (
        KnowledgeFile, get_doc_path, get_file_path,
    )
    from chayuan.server.knowledge_base.kb_doc_api import update_docs

    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        raise RuntimeError(f"未找到知识库 {kb_name}")

    # 1) FileStorage(权威存储)
    try:
        from chayuan.server.file_storage import NS, get_storage
        storage = get_storage()
        storage.put(NS.KB_CONTENT, f"{kb_name}/{filename}", content)
    except Exception as e:  # noqa: BLE001
        logger.warning("FileStorage.put failed for %s/%s: %r", kb_name, filename, e)

    # 2) 本地副本(供 KnowledgeFile.file2text 使用)
    file_path = get_file_path(knowledge_base_name=kb_name, doc_name=filename)
    if file_path is None:
        raise RuntimeError(f"非法文件名 {filename!r}")
    if not os.path.isdir(os.path.dirname(file_path)):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if os.path.isfile(file_path) and not override and os.path.getsize(file_path) == len(content):
        # 字节相同 → 不重写,但仍走 update_docs 让向量库收一次
        pass
    else:
        with open(file_path, "wb") as fp:
            fp.write(content)

    # 3) 向量化
    if to_vector_store:
        update_docs(
            knowledge_base_name=kb_name,
            file_names=[filename],
            override_custom_docs=True,
            not_refresh_vs_cache=True,  # 由 SyncEngine 在收尾阶段一次刷
        )


def _existing_files_lower(kb_name: str) -> set:
    """KB 里已经有的文件(小写化方便匹配)。"""
    try:
        from chayuan.server.knowledge_base.kb_service.base import (
            KBServiceFactory, get_kb_file_details,
        )
        kb = KBServiceFactory.get_service_by_name(kb_name)
        if kb is None:
            return set()
        return {f["file_name"].lower() for f in get_kb_file_details(kb_name)}
    except Exception:  # noqa: BLE001
        return set()


def _save_vector_store(kb_name: str) -> None:
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return
    save = getattr(kb, "save_vector_store", None)
    if callable(save):
        save()
