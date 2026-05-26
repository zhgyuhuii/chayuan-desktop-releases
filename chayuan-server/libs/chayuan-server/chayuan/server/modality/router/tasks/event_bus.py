"""每个任务一个事件总线 — 多订阅者 + 重放最近 N 条。

为什么不用一张全局 queue
========================
TaskManager 同时跑多个任务,前端可能为不同 task 分别开 SSE。每个 task 一根
独立 ``Channel`` 才能让"订阅 task A 的客户端"看不到 task B 的事件,也避免
A 的事件慢消费阻塞 B。

为什么要 replay buffer
======================
SSE 续流场景:浏览器关闭后又重开,客户端订阅 ``/v1/modality/tasks/<id>/events``
时,任务可能已经跑出 50% 进度 / 一些 text-delta。我们留一段 ring buffer
(默认 1000 条 / per task),订阅时先把这些 yield 出去,再挂到 live 流上 —
客户端感受跟"首次发起"完全一致。

不持久化事件
============
Buffer 在内存里;进程重启 buffer 就丢。这与 store.py 的策略一致:落库的是
"终态 + 文件 + 文字结果",事件流是过程性的。重启后续流,客户端拿到的是
restore 重新触发的事件流(详见 manager.py 的 ``resume_from_db``)。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import AsyncIterator, Deque, Dict, List, Optional, Set

logger = logging.getLogger("chayuan.modality.tasks.event_bus")


_DEFAULT_BUFFER_SIZE = 1000


class TaskChannel:
    """单任务的事件管道 — 一个 deque 做 replay buffer + 一组 ``asyncio.Queue`` 做 live 转播。"""

    def __init__(self, task_id: str, buffer_size: int = _DEFAULT_BUFFER_SIZE) -> None:
        self.task_id = task_id
        self._buffer: Deque[Dict] = deque(maxlen=buffer_size)
        self._subscribers: Set[asyncio.Queue] = set()
        self._closed = False
        self._final_event: Optional[Dict] = None  # finish / error 事件,订阅时直接吐

    def publish(self, event: Dict) -> None:
        """生产端调用 — 把事件压入 buffer 并广播给所有 live 订阅者。"""
        if self._closed:
            logger.debug("[event_bus] publish on closed channel task=%s ev=%s", self.task_id, event.get("type"))
            return
        self._buffer.append(event)
        # 终态记忆 — 已结束的任务再被订阅时,直接吐 final + close
        etype = event.get("type")
        if etype in ("finish",):
            self._final_event = event
        # 广播
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢订阅者 — 丢弃最老的,塞新事件
                try:
                    _ = q.get_nowait()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    q.put_nowait(event)
                except Exception:  # noqa: BLE001
                    logger.warning("[event_bus] drop event task=%s ev=%s", self.task_id, etype)

    def close(self) -> None:
        """生产端调用 — 标记完成,所有当前订阅者会收到 None 终止信号。"""
        if self._closed:
            return
        self._closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)  # sentinel
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def buffered_events(self) -> List[Dict]:
        return list(self._buffer)

    async def subscribe(self) -> AsyncIterator[Dict]:
        """订阅者 — 先 yield buffer 已有事件,再挂 live 流;通道 close 时 StopIteration。"""
        # 1) replay
        for ev in list(self._buffer):
            yield ev
        # 已经结束的任务 — buffer 里已经包含 finish 事件,直接退出
        if self._closed:
            return

        # 2) live
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield ev
        finally:
            self._subscribers.discard(q)


# ──────────────────────────────────────────────────────────────
# 全局 channel 注册表
# ──────────────────────────────────────────────────────────────


_channels: Dict[str, TaskChannel] = {}


def get_or_create_channel(task_id: str) -> TaskChannel:
    """同步函数 — 单线程 asyncio 下 dict 插入原子,不需要锁。

    必须是同步,以便 ``ensure_running`` 可以在 ``asyncio.create_task`` 之前
    保证通道存在,避免订阅者抢跑到 DB replay 路径(那时任务还在 pending,
    DB 行里没有真实事件)。
    """
    ch = _channels.get(task_id)
    if ch is None:
        ch = TaskChannel(task_id)
        _channels[task_id] = ch
    return ch


def get_channel(task_id: str) -> Optional[TaskChannel]:
    return _channels.get(task_id)


def drop_channel(task_id: str) -> None:
    """任务彻底完成且无订阅者时回收 — TaskManager 在 grace 期后调。"""
    ch = _channels.pop(task_id, None)
    if ch is not None and not ch.is_closed:
        ch.close()


def list_channel_ids() -> List[str]:
    return list(_channels.keys())
