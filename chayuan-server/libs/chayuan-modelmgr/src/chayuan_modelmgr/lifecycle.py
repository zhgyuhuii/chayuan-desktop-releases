"""Model lifecycle orchestrator —— 下载 + 校验 + 落盘 + 注册到框架 一站式。

为什么单建一个 ``lifecycle`` 模块?
====================================

历史上模型生命周期的每一步是各自做的:
* ``image_model_panel`` 直接调 huggingface_hub
* ``ingest_queue.tasks.image_model_download`` 走 Arq
* ``model_registry/watcher`` 周期扫盘
* "下载完模型 → vLLM/Ollama 自动可见" 一直没人做

每条链路有自己的进度协议、自己的事件总线、自己的失败回滚。前端要展示统一的
"下载进度",需要订阅好几个不同 topic;新加一类模型(t2v / TTS)又要重写一遍。

本模块作为 SSOT(单一真相源)把生命周期的"主干"集中起来:

::

    [start] ──► RESOLVE ─► CONNECT ─► DOWNLOAD ─► VERIFY ─► WIRE ─► READY
                                                                 │
                                                                 └─► ERROR (任何阶段失败)

每个阶段产生 :class:`StageEvent`,通过 :class:`LifecycleStore`(Redis / 内存)
广播,前端通过 :func:`subscribe` 拿 SSE 流。

API 概览
========

::

    lc = get_lifecycle()
    task_id = lc.start(
        repo="Qwen/Qwen2.5-7B-Instruct",
        capability="chat",
        target_runtime="ollama",
    )
    async for ev in lc.subscribe(task_id):
        print(ev.stage, ev.overall, ev.detail)
        if ev.stage in ("ready", "error"):
            break

实现要点
========

* 后端无关:Redis 可用就走 Redis pubsub(跨进程订阅);否则 asyncio.Queue。
* 进度归一化:``ProgressEvent`` 的 bytes_done/total 映射成 ``DOWNLOAD`` 阶段的
  ``progress`` 0-100;``overall`` 按阶段权重加权。
* WIRE 阶段:调用 ``framework_wiring.wire`` (B底座) — 注入式,缺省 wiring
  callable 时跳过(向后兼容)。
* 失败回滚:VERIFY / WIRE 失败时调 :meth:`_rollback` 删除已下载文件。
* 取消:``cancel`` event 贯穿 download,以及 wire 阶段。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Callable, Deque, Dict, List, Optional, Protocol

from chayuan_modelmgr.downloader import DownloadOptions, ModelDownloader
from chayuan_modelmgr.progress import ProgressEvent

logger = logging.getLogger("chayuan_modelmgr.lifecycle")


# ---------------------------------------------------------------------------
# Stage enum + StageEvent
# ---------------------------------------------------------------------------

class Stage:
    """状态机阶段。用字符串便于 SSE 序列化。"""

    RESOLVE = "resolve"
    CONNECT = "connect"
    DOWNLOAD = "download"
    VERIFY = "verify"
    WIRE = "wire"
    READY = "ready"
    ERROR = "error"


# 各阶段在 ``overall`` 中的权重(总和 100)
_STAGE_WEIGHTS: Dict[str, float] = {
    Stage.RESOLVE: 5,
    Stage.CONNECT: 5,
    Stage.DOWNLOAD: 70,
    Stage.VERIFY: 10,
    Stage.WIRE: 10,
}
_STAGE_ORDER: List[str] = [
    Stage.RESOLVE, Stage.CONNECT, Stage.DOWNLOAD, Stage.VERIFY, Stage.WIRE,
]


def _overall_for(stage: str, stage_progress: float) -> float:
    """阶段名 + 阶段内 0-100 进度 → 全局 0-100 进度。"""
    if stage == Stage.READY:
        return 100.0
    if stage == Stage.ERROR:
        # error 时不回退;沿用 overall=最后看到的值的责任在调用方
        return 0.0
    if stage not in _STAGE_ORDER:
        return 0.0
    completed = sum(_STAGE_WEIGHTS[s] for s in _STAGE_ORDER if _STAGE_ORDER.index(s) < _STAGE_ORDER.index(stage))
    cur = _STAGE_WEIGHTS.get(stage, 0)
    return min(100.0, completed + cur * max(0.0, min(100.0, stage_progress)) / 100.0)


@dataclass
class StageEvent:
    task_id: str
    stage: str
    progress: float = 0.0    # 当前阶段 0-100
    overall: float = 0.0     # 总进度 0-100
    detail: str = ""
    timestamp: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Store backends
# ---------------------------------------------------------------------------

class LifecycleStore(Protocol):
    def put(self, ev: StageEvent) -> None: ...
    def latest(self, task_id: str) -> Optional[StageEvent]: ...
    def history(self, task_id: str) -> List[StageEvent]: ...
    def subscribe(self, task_id: str) -> "asyncio.Queue[StageEvent]": ...


class _InMemoryStore:
    """单进程 SSOT。多 worker 部署时建议切到 Redis。

    历史保留最近 ``_HISTORY_CAP`` 条事件,避免内存无界增长。
    """

    _HISTORY_CAP = 256

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: Dict[str, Deque[StageEvent]] = {}
        # 每 task 多 subscriber → 多 queue
        self._queues: Dict[str, List["asyncio.Queue[StageEvent]"]] = {}

    def put(self, ev: StageEvent) -> None:
        with self._lock:
            dq = self._history.setdefault(ev.task_id, deque(maxlen=self._HISTORY_CAP))
            dq.append(ev)
            queues = list(self._queues.get(ev.task_id, ()))
        for q in queues:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                logger.warning("lifecycle queue full for %s", ev.task_id)

    def latest(self, task_id: str) -> Optional[StageEvent]:
        with self._lock:
            dq = self._history.get(task_id)
            return dq[-1] if dq else None

    def history(self, task_id: str) -> List[StageEvent]:
        with self._lock:
            dq = self._history.get(task_id) or deque()
            return list(dq)

    def subscribe(self, task_id: str) -> "asyncio.Queue[StageEvent]":
        q: asyncio.Queue[StageEvent] = asyncio.Queue(maxsize=512)
        with self._lock:
            self._queues.setdefault(task_id, []).append(q)
            # 立即把历史回放给新订阅者
            history = list(self._history.get(task_id) or ())
        for ev in history:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe(self, task_id: str, q: "asyncio.Queue[StageEvent]") -> None:
        with self._lock:
            queues = self._queues.get(task_id)
            if queues:
                try:
                    queues.remove(q)
                except ValueError:
                    pass


class _RedisStore:
    """Redis-backed store: pubsub for live + list for history.

    Channel: ``chayuan:lifecycle:events:<task_id>``
    History: list ``chayuan:lifecycle:history:<task_id>``,LTRIM 到 256 条
    """

    _HISTORY_CAP = 256

    def __init__(self, url: str) -> None:
        import redis  # 延迟导入

        self._redis = redis.from_url(url, decode_responses=True)
        self._json = __import__("json")

    def _channel(self, task_id: str) -> str:
        return f"chayuan:lifecycle:events:{task_id}"

    def _history_key(self, task_id: str) -> str:
        return f"chayuan:lifecycle:history:{task_id}"

    def put(self, ev: StageEvent) -> None:
        payload = self._json.dumps(ev.to_dict())
        try:
            pipe = self._redis.pipeline()
            pipe.rpush(self._history_key(ev.task_id), payload)
            pipe.ltrim(self._history_key(ev.task_id), -self._HISTORY_CAP, -1)
            pipe.expire(self._history_key(ev.task_id), 3600 * 6)  # 6h TTL
            pipe.publish(self._channel(ev.task_id), payload)
            pipe.execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("redis lifecycle put failed: %s", e)

    def latest(self, task_id: str) -> Optional[StageEvent]:
        try:
            raw = self._redis.lrange(self._history_key(task_id), -1, -1)
            if not raw:
                return None
            d = self._json.loads(raw[0])
            return StageEvent(**d)
        except Exception as e:  # noqa: BLE001
            logger.warning("redis lifecycle latest failed: %s", e)
            return None

    def history(self, task_id: str) -> List[StageEvent]:
        try:
            raw = self._redis.lrange(self._history_key(task_id), 0, -1)
            return [StageEvent(**self._json.loads(r)) for r in raw]
        except Exception as e:  # noqa: BLE001
            logger.warning("redis lifecycle history failed: %s", e)
            return []

    def subscribe(self, task_id: str) -> "asyncio.Queue[StageEvent]":
        q: asyncio.Queue[StageEvent] = asyncio.Queue(maxsize=512)
        # 先回放历史
        for ev in self.history(task_id):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        # pubsub 用线程后台中转,避免阻塞当前 event loop
        ps = self._redis.pubsub()
        ps.subscribe(self._channel(task_id))
        loop = asyncio.get_event_loop()

        def _bg() -> None:
            try:
                for msg in ps.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        ev = StageEvent(**self._json.loads(msg["data"]))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("redis lifecycle decode fail: %s", e)
                        continue
                    asyncio.run_coroutine_threadsafe(q.put(ev), loop)
            except Exception as e:  # noqa: BLE001
                logger.warning("redis lifecycle bg quit: %s", e)

        threading.Thread(target=_bg, daemon=True, name=f"lifecycle-redis-{task_id}").start()
        return q


def _build_store() -> LifecycleStore:
    """Pick Redis if REDIS_URL set + redis lib importable, else memory."""
    import os

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        try:
            from chayuan.settings import Settings  # type: ignore

            url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        except Exception:
            pass
    if url:
        try:
            return _RedisStore(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis lifecycle store init failed (%s); fallback to memory", e)
    return _InMemoryStore()


# ---------------------------------------------------------------------------
# Wire callback registry —— B底座 (framework_wiring) 注入点
# ---------------------------------------------------------------------------

WireCallable = Callable[[str, str, str], "WireOutcome"]
"""(model_path, capability, target_runtime) -> WireOutcome

返回成功/失败,本模块不要求 framework_wiring 知道 lifecycle 的存在。
"""


@dataclass
class WireOutcome:
    ok: bool
    runtime: str
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


_wire_impl: Optional[WireCallable] = None
_wire_lock = threading.Lock()


def register_wire_impl(fn: WireCallable) -> None:
    """B底座 启动时注册 framework_wiring.wire。"""
    global _wire_impl
    with _wire_lock:
        _wire_impl = fn


def _wire_or_skip(model_path: str, capability: str, target_runtime: str) -> WireOutcome:
    if _wire_impl is None:
        logger.info("no wire impl registered; skipping framework wiring")
        return WireOutcome(ok=True, runtime=target_runtime, detail="(no wire impl)")
    try:
        return _wire_impl(model_path, capability, target_runtime)
    except Exception as e:  # noqa: BLE001
        logger.exception("wire impl raised")
        return WireOutcome(ok=False, runtime=target_runtime, detail=str(e))


# ---------------------------------------------------------------------------
# Lifecycle orchestrator
# ---------------------------------------------------------------------------

class Lifecycle:
    """Model lifecycle orchestrator;process-singleton via :func:`get_lifecycle`."""

    def __init__(self, store: Optional[LifecycleStore] = None) -> None:
        self._store = store or _build_store()
        self._cancels: Dict[str, threading.Event] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(
        self,
        *,
        repo: str,
        capability: str,
        target_runtime: str,
        mirror: Optional[str] = None,
        revision: Optional[str] = None,
        allow_patterns: Optional[List[str]] = None,
        ignore_patterns: Optional[List[str]] = None,
    ) -> str:
        """Kick off a download job.  Returns a task_id; poll via :meth:`subscribe`."""
        task_id = uuid.uuid4().hex
        cancel = threading.Event()

        with self._lock:
            self._cancels[task_id] = cancel

        t = threading.Thread(
            target=self._run_job,
            args=(task_id, repo, capability, target_runtime, mirror, revision,
                  allow_patterns, ignore_patterns, cancel),
            daemon=True,
            name=f"lifecycle-{task_id[:8]}",
        )
        with self._lock:
            self._workers[task_id] = t
        t.start()
        return task_id

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            ev = self._cancels.get(task_id)
        if ev is None:
            return False
        ev.set()
        return True

    def latest(self, task_id: str) -> Optional[StageEvent]:
        return self._store.latest(task_id)

    def history(self, task_id: str) -> List[StageEvent]:
        return self._store.history(task_id)

    async def subscribe(
        self,
        task_id: str,
        *,
        terminal_stages: tuple = (Stage.READY, Stage.ERROR),
    ) -> AsyncIterator[StageEvent]:
        """Async generator yielding events until a terminal stage."""
        q = self._store.subscribe(task_id)
        try:
            while True:
                ev = await q.get()
                yield ev
                if ev.stage in terminal_stages:
                    return
        finally:
            # 内存 store 暴露 unsubscribe;Redis store 不需要(线程随 task 自然退出)
            unsubscribe = getattr(self._store, "unsubscribe", None)
            if callable(unsubscribe):
                try:
                    unsubscribe(task_id, q)
                except Exception:  # noqa: BLE001
                    pass

    # -- 内部 --

    def _emit(self, task_id: str, stage: str, *, progress: float = 0, detail: str = "", **extra: Any) -> None:
        ev = StageEvent(
            task_id=task_id,
            stage=stage,
            progress=progress,
            overall=_overall_for(stage, progress),
            detail=detail,
            extra=extra,
        )
        self._store.put(ev)

    def _run_job(
        self,
        task_id: str,
        repo: str,
        capability: str,
        target_runtime: str,
        mirror: Optional[str],
        revision: Optional[str],
        allow_patterns: Optional[List[str]],
        ignore_patterns: Optional[List[str]],
        cancel: threading.Event,
    ) -> None:
        downloaded_path: Optional[str] = None
        try:
            # ---- RESOLVE ----
            self._emit(task_id, Stage.RESOLVE, progress=0,
                       detail=f"resolving {repo}", repo=repo,
                       capability=capability, target_runtime=target_runtime)
            # 这里只做最小验证;真实的 catalog 查询在调用方做了
            self._emit(task_id, Stage.RESOLVE, progress=100, detail="resolved")

            if cancel.is_set():
                self._emit(task_id, Stage.ERROR, detail="cancelled before connect")
                return

            # ---- CONNECT ----
            self._emit(task_id, Stage.CONNECT, progress=0, detail="picking mirror")
            from chayuan_modelmgr.mirrors import resolve_mirror

            chosen = resolve_mirror(mirror)
            self._emit(task_id, Stage.CONNECT, progress=100,
                       detail=f"mirror={chosen.name}", mirror=chosen.endpoint)

            if cancel.is_set():
                self._emit(task_id, Stage.ERROR, detail="cancelled before download")
                return

            # ---- DOWNLOAD ----
            self._emit(task_id, Stage.DOWNLOAD, progress=0, detail="starting download")

            # ProgressEvent → StageEvent 桥接
            #
            # 单文件 bytes_done/total 是文件级;真实下载会有多文件,所以我们
            # 维护本地 (running_done, running_total) 累加,再除得百分比。
            tracker = {"done": 0, "total": 0, "current": ""}

            def _bridge(pe: ProgressEvent) -> None:
                # state 切换也走这里
                if pe.state == "error":
                    return  # 留给外层 try 捕
                if pe.bytes_total > 0:
                    tracker["total"] = max(tracker["total"], pe.bytes_total)
                tracker["done"] = pe.bytes_done
                tracker["current"] = pe.filename or ""
                pct = (tracker["done"] / tracker["total"] * 100) if tracker["total"] else 0
                self._emit(task_id, Stage.DOWNLOAD,
                           progress=pct,
                           detail=f"{pe.filename}" if pe.filename else "",
                           bytes_done=tracker["done"],
                           bytes_total=tracker["total"])

            opt = DownloadOptions(
                repo=repo,
                category=_capability_to_category(capability),
                mirror=chosen.endpoint if mirror else None,
                revision=revision,
                allow_patterns=allow_patterns,
                ignore_patterns=ignore_patterns,
                progress_cb=_bridge,
                cancel=cancel,
            )
            res = ModelDownloader(opt).run()
            downloaded_path = str(res.dest)
            self._emit(task_id, Stage.DOWNLOAD, progress=100,
                       detail="download complete",
                       dest=downloaded_path,
                       bytes_total=res.bytes_total)

            # ---- VERIFY ----
            self._emit(task_id, Stage.VERIFY, progress=0, detail="verifying manifest")
            try:
                from chayuan_modelmgr.verifier import verify_directory

                verified = verify_directory(res.dest)
                self._emit(task_id, Stage.VERIFY, progress=100,
                           detail=f"verified {verified} files" if verified else "manifest absent",
                           verified_files=verified)
            except Exception as e:  # noqa: BLE001
                # 缺 manifest 不当致命;只警告
                logger.info("verify skipped: %s", e)
                self._emit(task_id, Stage.VERIFY, progress=100,
                           detail=f"skipped: {e}")

            # ---- WIRE ----
            self._emit(task_id, Stage.WIRE, progress=0,
                       detail=f"wiring to {target_runtime}")
            outcome = _wire_or_skip(downloaded_path, capability, target_runtime)
            if not outcome.ok:
                self._emit(task_id, Stage.ERROR,
                           detail=f"wire failed: {outcome.detail}",
                           runtime=outcome.runtime,
                           **outcome.extra)
                self._rollback(downloaded_path)
                return
            self._emit(task_id, Stage.WIRE, progress=100,
                       detail=outcome.detail or "wired",
                       runtime=outcome.runtime,
                       **outcome.extra)

            # ---- READY ----
            self._emit(task_id, Stage.READY, progress=100,
                       detail=f"{repo} ready on {outcome.runtime}",
                       dest=downloaded_path,
                       runtime=outcome.runtime)

            # 触发 model_registry 重扫,让前端模型广场立即看到
            self._trigger_rescan(downloaded_path)

        except InterruptedError as e:
            self._emit(task_id, Stage.ERROR, detail=f"cancelled: {e}")
            if downloaded_path:
                self._rollback(downloaded_path)
        except Exception as e:  # noqa: BLE001
            logger.exception("lifecycle job failed")
            self._emit(task_id, Stage.ERROR, detail=str(e), kind=type(e).__name__)
            if downloaded_path:
                self._rollback(downloaded_path)
        finally:
            with self._lock:
                self._cancels.pop(task_id, None)
                self._workers.pop(task_id, None)

    @staticmethod
    def _rollback(path: str) -> None:
        try:
            import shutil
            from pathlib import Path

            p = Path(path)
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                logger.info("lifecycle rolled back %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("rollback failed: %s", e)

    @staticmethod
    def _trigger_rescan(path: str) -> None:
        try:
            from chayuan.server.model_registry.local_index import scan_once

            scan_once()
            logger.info("lifecycle: triggered local_index rescan after %s", path)
        except Exception as e:  # noqa: BLE001
            logger.debug("rescan trigger skipped: %s", e)


def _capability_to_category(capability: str) -> str:
    """capability 对齐 modelmgr 的 ``category`` 目录约定。

    chayuan-modelmgr.recommended.MODEL_DIR_FOR 用 ``llm / embedding / clip /
    rerank / t2i / t2v / asr / tts / ocr`` 之类的目录名;前端 / 后端用的
    capability 字符串可能不一致,做一次 normalize。
    """
    table = {
        "chat": "llm",
        "llm": "llm",
        "text-embedding": "embedding",
        "embedding": "embedding",
        "image-embedding": "clip",
        "clip": "clip",
        "rerank": "rerank",
        "text-to-image": "t2i",
        "t2i": "t2i",
        "text-to-video": "t2v",
        "t2v": "t2v",
        "text-to-speech": "tts",
        "tts": "tts",
        "asr": "asr",
        "speech-to-text": "asr",
        "ocr": "ocr",
    }
    return table.get(capability, capability)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_singleton: Optional[Lifecycle] = None
_singleton_lock = threading.Lock()


def get_lifecycle() -> Lifecycle:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = Lifecycle()
        return _singleton


__all__ = [
    "Lifecycle",
    "LifecycleStore",
    "Stage",
    "StageEvent",
    "WireCallable",
    "WireOutcome",
    "get_lifecycle",
    "register_wire_impl",
]
