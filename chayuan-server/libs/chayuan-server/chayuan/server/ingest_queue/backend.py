"""Arq 后端封装 + 降级路径。

- ``enqueue_task(name, payload)``：异步把任务塞进 Redis，返回 (task_id, 状态)。
  状态含义：
    - "queued"：成功入队；
    - "sync"：Arq / Redis 不可用，已降级为同步执行；
    - "failed"：同步执行也失败；
- ``get_task_status(task_id)``：从 Redis 读一次 JSON 状态；
- ``worker_settings()``：给 arq cli 用的 WorkerSettings 类。

这里故意避免在 import 时就建立 Arq 连接，以防 uvicorn worker 之间共享 event loop 造成问题。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("chayuan.ingest_queue.backend")


_STATUS_KEY = "chayuan:ingest:task:{task_id}"
_CANCEL_KEY = "chayuan:ingest:task:{task_id}:cancel"
_IDEMPOTENCY_KEY = "chayuan:ingest:idempotency:{key}"
_DEAD_LETTER_KEY = "chayuan:ingest:dead:{task_id}"
_STATUS_TTL = 24 * 3600  # 状态保留 24h
_LOCAL_STATUS: Dict[str, Dict[str, Any]] = {}
_LOCAL_CANCELLED: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Arq 可用性检测
# ---------------------------------------------------------------------------

def _arq_available() -> bool:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("arq", "arq>=0.25,<0.27")
    except Exception:  # noqa: BLE001
        pass
    try:
        import arq  # noqa: F401
        return True
    except ImportError:
        return False


def _redis_url() -> str:
    from chayuan.settings import Settings
    return (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()


def _queue_name() -> str:
    from chayuan.settings import Settings
    return (getattr(Settings.basic_settings, "ARQ_QUEUE_NAME", "chayuan:ingest") or "chayuan:ingest")


def _task_queue_name(name: str) -> str:
    """任务类型 → 独立队列名。未拆分的老任务仍回落默认队列。"""
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    if name in ("ingest_upload_docs",):
        return getattr(bs, "ARQ_QUEUE_INGEST", "") or _queue_name()
    if name in ("reindex_kb_files", "raptor_build", "graphrag_build"):
        return getattr(bs, "ARQ_QUEUE_EMBEDDING", "") or _queue_name()
    if name.startswith("annotation"):
        return getattr(bs, "ARQ_QUEUE_ANNOTATION", "") or _queue_name()
    return _queue_name()


def _task_max_tries(name: str) -> int:
    from chayuan.settings import Settings
    default = int(getattr(Settings.basic_settings, "ARQ_TASK_MAX_TRIES", 2) or 2)
    if name in ("ingest_upload_docs", "reindex_kb_files"):
        return max(1, default)
    return max(1, min(default, 3))


def _idempotency_key(name: str, payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("idempotency_key") or "").strip()
    if explicit:
        return explicit
    summary = {
        "name": name,
        "kb_name": payload.get("kb_name"),
        "file_names": payload.get("file_names"),
        "file_hashes": payload.get("file_hashes"),
        "doc_id": payload.get("doc_id"),
        "content_hash": payload.get("content_hash") or payload.get("payload_hash"),
    }
    raw = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


# ---------------------------------------------------------------------------
# 任务状态写 / 读
# ---------------------------------------------------------------------------

def _status_key(task_id: str) -> str:
    return _STATUS_KEY.format(task_id=task_id)


async def _async_write_status(task_id: str, status: Dict[str, Any]) -> None:
    from chayuan.server.shared import get_redis
    r = get_redis()
    if r is None:
        return
    try:
        await r.set(_status_key(task_id), json.dumps(status, ensure_ascii=False), ex=_STATUS_TTL)
    except Exception as e:  # noqa: BLE001
        logger.debug("write task status failed: %r", e)


def _sync_write_status(task_id: str, status: Dict[str, Any]) -> None:
    """同步路径（降级执行用）：开一次同步 redis 客户端直接写。"""
    _LOCAL_STATUS[task_id] = dict(status)
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        url = _redis_url()
        if not url:
            return
        client = redis.Redis.from_url(url, decode_responses=True)
        client.set(_status_key(task_id), json.dumps(status, ensure_ascii=False), ex=_STATUS_TTL)
    except Exception as e:  # noqa: BLE001
        logger.debug("sync write task status failed: %r", e)


def local_patch_status(task_id: str, patch: Dict[str, Any]) -> None:
    state = dict(_LOCAL_STATUS.get(task_id) or {"task_id": task_id})
    state.update(patch)
    _LOCAL_STATUS[task_id] = state


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        url = _redis_url()
        if not url:
            return _LOCAL_STATUS.get(task_id)
        client = redis.Redis.from_url(url, decode_responses=True)
        raw = client.get(_status_key(task_id))
        if not raw:
            return _LOCAL_STATUS.get(task_id)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.debug("get task status failed: %r", e)
        return _LOCAL_STATUS.get(task_id)


def request_task_cancel(task_id: str, reason: str = "user_cancelled") -> bool:
    """标记任务取消。worker 在阶段边界调用 is_task_cancelled 后安全退出。"""
    payload = {"task_id": task_id, "reason": reason, "cancelled_at": int(time.time())}
    _LOCAL_CANCELLED[task_id] = payload
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        url = _redis_url()
        if not url:
            return True
        client = redis.Redis.from_url(url, decode_responses=True)
        client.set(_CANCEL_KEY.format(task_id=task_id), json.dumps(payload, ensure_ascii=False), ex=_STATUS_TTL)
        state = dict(get_task_status(task_id) or {"task_id": task_id})
        state.update({"state": "cancelling", "cancel_reason": reason})
        client.set(_status_key(task_id), json.dumps(state, ensure_ascii=False), ex=_STATUS_TTL)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("request task cancel failed: %r", e)
        return True


def is_task_cancelled(task_id: str) -> bool:
    if task_id in _LOCAL_CANCELLED:
        return True
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        url = _redis_url()
        if not url:
            return False
        client = redis.Redis.from_url(url, decode_responses=True)
        return bool(client.exists(_CANCEL_KEY.format(task_id=task_id)))
    except Exception:
        return False


def _write_dead_letter(task_id: str, name: str, payload: Dict[str, Any], error: str) -> None:
    item = {
        "task_id": task_id,
        "name": name,
        "queue": _task_queue_name(name),
        "error": error,
        "payload_summary": _payload_summary(payload),
        "failed_at": int(time.time()),
    }
    try:
        from chayuan.settings import Settings
        ttl = int(getattr(Settings.basic_settings, "ARQ_DEAD_LETTER_TTL_SECONDS", 7 * 24 * 3600) or 7 * 24 * 3600)
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        url = _redis_url()
        if not url:
            return
        client = redis.Redis.from_url(url, decode_responses=True)
        client.set(_DEAD_LETTER_KEY.format(task_id=task_id), json.dumps(item, ensure_ascii=False), ex=ttl)
    except Exception as e:  # noqa: BLE001
        logger.debug("write dead letter failed: %r", e)


# ---------------------------------------------------------------------------
# 入队 + 降级
# ---------------------------------------------------------------------------

def enqueue_task(name: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    """把任务塞进 Arq；失败就同步执行。"""
    task_id = uuid.uuid4().hex
    queue = _task_queue_name(name)
    idem = _idempotency_key(name, payload)

    url = _redis_url()
    if not url or not _arq_available():
        reason = "redis 未配置" if not url else "arq 包未安装"
        logger.warning("enqueue_task fallback to sync: %s", reason)
        # 仅当是 redis 这边不可用时，附上"请到配置面板配置 Redis"的限频指引
        if not url:
            from chayuan.server.shared.redis_health import warn_redis_unavailable
            warn_redis_unavailable("ingest_async", reason="REDIS_URL 未配置")
        payload = {**payload, "idempotency_key": idem}
        _run_sync(task_id, name, payload, reason=reason)
        return task_id, "sync"

    try:
        existing = _claim_idempotency(idem, task_id)
        if existing and existing != task_id:
            logger.info("duplicate task ignored: name=%s idem=%s existing=%s", name, idem, existing)
            return existing, "duplicate"
        payload = {**payload, "idempotency_key": idem}
        ok = _schedule_arq(task_id, name, payload, queue_name=queue)
    except Exception as e:  # noqa: BLE001
        # Arq 层起来了，却连不上 Redis —— 最常见的"需要配 Redis"信号
        from chayuan.server.shared.redis_health import warn_redis_unavailable
        warn_redis_unavailable("ingest_async", reason=f"{type(e).__name__}: {e}")
        logger.warning("arq enqueue failed, fallback to sync: %r", e)
        _run_sync(task_id, name, payload, reason=f"arq error: {e!r}")
        return task_id, "sync"

    if not ok:
        _run_sync(task_id, name, payload, reason="arq returned no job")
        return task_id, "sync"

    _sync_write_status(task_id, {
        "task_id": task_id,
        "name": name,
        "queue": queue,
        "priority": int(payload.get("priority") or 0),
        "idempotency_key": idem,
        "state": "queued",
        "enqueued_at": int(time.time()),
        "payload_summary": _payload_summary(payload),
    })
    return task_id, "queued"


def enqueue_task_background(name: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    """优先 Arq；不可用时用本进程后台线程兜底,让前端仍可轮询进度。"""
    url = _redis_url()
    if url and _arq_available():
        try:
            ok_task, state = enqueue_task(name, payload)
            if state == "queued":
                return ok_task, state
        except Exception as e:  # noqa: BLE001
            logger.warning("background enqueue arq failed, fallback to local thread: %r", e)

    task_id = uuid.uuid4().hex
    payload = {**payload, "idempotency_key": _idempotency_key(name, payload)}
    _sync_write_status(task_id, {
        "task_id": task_id,
        "name": name,
        "queue": _task_queue_name(name),
        "priority": int(payload.get("priority") or 0),
        "idempotency_key": payload.get("idempotency_key"),
        "state": "queued",
        "enqueued_at": int(time.time()),
        "payload_summary": _payload_summary(payload),
    })
    th = threading.Thread(
        target=_run_sync,
        args=(task_id, name, payload, "local background fallback"),
        daemon=True,
    )
    th.start()
    return task_id, "local"


def _claim_idempotency(key: str, task_id: str) -> Optional[str]:
    try:
        from chayuan.settings import Settings
        ttl = int(getattr(Settings.basic_settings, "ARQ_IDEMPOTENCY_TTL_SECONDS", 3600) or 3600)
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        client = redis.Redis.from_url(_redis_url(), decode_responses=True)
        redis_key = _IDEMPOTENCY_KEY.format(key=key)
        ok = client.set(redis_key, task_id, nx=True, ex=ttl)
        if ok:
            return task_id
        existing = client.get(redis_key)
        return str(existing) if existing else task_id
    except Exception as e:  # noqa: BLE001
        logger.debug("claim idempotency failed: %r", e)
        return task_id


def _schedule_arq(task_id: str, name: str, payload: Dict[str, Any], *, queue_name: str) -> bool:
    """把 ArqRedis 打开 / 发任务这一串同步封装起来。"""
    from arq import create_pool  # type: ignore
    from arq.connections import RedisSettings  # type: ignore

    rs = RedisSettings.from_dsn(_redis_url())
    loop = _get_loop()
    pool = loop.run_until_complete(create_pool(rs, default_queue_name=queue_name))
    try:
        job = loop.run_until_complete(
            pool.enqueue_job(
                name,
                task_id,
                payload,
                _queue_name=queue_name,
                _job_id=task_id,
            )
        )
        return job is not None
    finally:
        loop.run_until_complete(pool.close())


def _get_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _payload_summary(p: Dict[str, Any]) -> Dict[str, Any]:
    """只保留小而关键的字段，防止 Redis 里塞太大。"""
    out = {}
    for k in ("kb_name", "user_id"):
        if k in p:
            out[k] = p[k]
    if isinstance(p.get("file_names"), list):
        out["file_count"] = len(p["file_names"])
    return out


def _run_sync(task_id: str, name: str, payload: Dict[str, Any], reason: str) -> None:
    """降级同步执行：直接在当前进程里跑 task 函数。"""
    from chayuan.server.ingest_queue import tasks as task_impls

    _sync_write_status(task_id, {
        "task_id": task_id,
        "name": name,
        "queue": _task_queue_name(name),
        "priority": int(payload.get("priority") or 0),
        "idempotency_key": payload.get("idempotency_key"),
        "state": "running",
        "reason": reason,
        "started_at": int(time.time()),
        "payload_summary": _payload_summary(payload),
    })

    # 路由表：arq 入口是 async，这里映射到同步实现
    sync_routes: Dict[str, Callable[..., Any]] = {
        "ingest_upload_docs": task_impls._ingest_upload_docs_sync_impl,
        "reindex_kb_files": task_impls._reindex_kb_files_sync_impl,
        "raptor_build": task_impls._raptor_build_sync_impl,
        "graphrag_build": task_impls._graphrag_build_sync_impl,
        "image_model_download": task_impls._image_model_download_sync_impl,
        "image_model_smoke_test": task_impls._image_model_smoke_test_sync_impl,
    }
    fn = sync_routes.get(name)
    if fn is None:
        _sync_write_status(task_id, {
            "task_id": task_id, "name": name, "state": "failed",
            "error": f"unknown task {name!r}",
        })
        _write_dead_letter(task_id, name, payload, f"unknown task {name!r}")
        return

    try:
        result = fn(task_id, payload)
        _sync_write_status(task_id, {
            "task_id": task_id, "name": name, "state": "success",
            "finished_at": int(time.time()),
            "result": result,
            "fallback_reason": reason,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("sync task failed: %s", name)
        _sync_write_status(task_id, {
            "task_id": task_id, "name": name, "state": "failed",
            "error": f"{type(e).__name__}: {e}",
        })
        _write_dead_letter(task_id, name, payload, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# WorkerSettings（`arq worker` / `chayuan worker` 启动时用）
# ---------------------------------------------------------------------------

def worker_settings(queue_name: Optional[str] = None):
    """返回一个类，供 arq cli `arq chayuan.server.ingest_queue.backend.WorkerSettings` 使用。

    我们不在模块顶层写一个 class WorkerSettings —— 否则 import arq 会在
    `import chayuan` 链里把未安装 arq 的部署直接打挂。
    """
    if not _arq_available():
        raise RuntimeError(
            "arq 未安装；请先 `pip install arq`，或关闭 INGEST_ASYNC_ENABLED。"
        )

    from arq.connections import RedisSettings  # type: ignore

    from chayuan.server.ingest_queue.tasks import (
        graphrag_build,
        image_model_download,
        image_model_smoke_test,
        ingest_upload_docs,
        on_job_end as _on_job_end,
        on_job_start as _on_job_start,
        raptor_build,
        reindex_kb_files,
    )
    from chayuan.settings import Settings
    selected_queue = queue_name or _queue_name()

    class _Worker:
        redis_settings = RedisSettings.from_dsn(_redis_url() or "redis://127.0.0.1:6379/0")
        queue_name = selected_queue
        functions = [
            ingest_upload_docs, reindex_kb_files, raptor_build, graphrag_build,
            image_model_download, image_model_smoke_test,
        ]
        on_job_start = staticmethod(_on_job_start)
        on_job_end = staticmethod(_on_job_end)
        max_jobs = int(getattr(Settings.basic_settings, "ARQ_MAX_JOBS", 10) or 10)
        max_tries = int(getattr(Settings.basic_settings, "ARQ_TASK_MAX_TRIES", 2) or 2)
        keep_result = _STATUS_TTL
        # GraphRAG 在大 KB 上可能跑几小时 → 单任务 6h 封顶
        job_timeout = 6 * 3600

    return _Worker
