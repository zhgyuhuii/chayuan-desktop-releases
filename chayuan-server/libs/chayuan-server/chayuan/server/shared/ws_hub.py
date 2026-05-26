"""开放平台 WebSocket 服务端的连接中枢（Inbound WS）。

设计目标：**高并发 + 背压可控 + 单连接阻塞不拖累全局**。

核心模型
--------
- ``Connection``：一条客户端长连接。持有：
    * starlette ``WebSocket`` 句柄；
    * 绑定的 ``app_id``；
    * ``asyncio.Queue``（有界）—— 广播只 put，不 await 发送；
    * 一个 writer task —— 从 queue 拿到消息就 ``ws.send_json``；慢客户端被它独自阻塞，
      与广播解耦；
    * 订阅表 ``subs: set[str]`` + 支持 ``kb.doc.*`` 前缀通配；
    * drop 计数，达到阈值直接踢掉（断给客户端一个「被踢」的 close 4003）。
- ``ConnectionHub``：进程级单例，``{app_id: set[Connection]}``。
    * ``broadcast(event, data)``：并行向每个匹配的 Connection 的 queue 做
      非阻塞 ``put_nowait``；满 → drop 计数 +1；
    * ``broadcast_sync``：从非 asyncio 线程（如 ``callback_dispatcher``）触发的
      同步入口，用 ``run_coroutine_threadsafe`` 投到主事件循环；
    * ``attach_loop``：API 启动时调一次，记录事件循环供 broadcast_sync 使用。

背压策略
--------
- 每连接 ``Queue(maxsize=256)``；满则丢弃该帧，**永不阻塞广播路径**；
- 单连接累计 drop ≥ ``MAX_DROP_BEFORE_KICK`` 即关闭（语义：「你拉了流跟不上，再见」）；
- 全局 / per-app 连接数限额在 accept 时检查；
- 单帧发送超时 5s，超时关闭该连接；
- 心跳：服务端 ``HEARTBEAT_EVERY`` 秒发 ``{"op":"ping"}``；
  客户端 ``IDLE_KICK`` 秒内无任何消息 → 服务端主动关（防死连接占资源）。

多 worker 扩展位
-----------------
hub 本身是进程内。在多 uvicorn worker 或多副本部署下，真正的 fan-out 需要接
Redis Pub/Sub：每个 worker 既订阅 channel（拉到消息 → 本地 broadcast），也在
``broadcast_sync`` 把消息 publish 到 channel。本文件预留 ``_remote_publisher``
钩子（None 则忽略），接入 Redis 时注入一个 async callable 即可，见 docstring。
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from starlette.websockets import WebSocket


logger = logging.getLogger("chayuan.ws_hub")


# ---- 可调参数（有需要可移到 basic_settings.yaml） ----
QUEUE_MAX = 256
MAX_DROP_BEFORE_KICK = 32
HEARTBEAT_EVERY = 30.0         # 服务端 ping 间隔
IDLE_KICK = 45.0               # 客户端多久没动静就踢
SEND_TIMEOUT = 5.0
GLOBAL_MAX_CONNECTIONS = 10_000
PER_APP_MAX_CONNECTIONS = 10
MAX_FRAME_BYTES = 1_000_000    # 接收单帧最大字节

# 关闭码（借用自定义区 4xxx）
CLOSE_OVERFLOW = 4003        # 慢连接背压丢太多
CLOSE_IDLE = 4004            # 心跳超时
CLOSE_RESOURCE = 4005        # 全局 / per-app 超限
CLOSE_SERVER_STOP = 4009     # 进程关停


@dataclass(eq=False)   # 用 identity 哈希 / 相等，允许放入 set；set / Queue 字段本身不可哈希
class Connection:
    ws: WebSocket
    app_id: str
    peer: str = ""
    subs: Set[str] = field(default_factory=set)
    # connect 时缓存的 App scopes；broadcast 不再临时查库
    app_scopes: Set[str] = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))
    last_activity: float = field(default_factory=time.monotonic)
    drops: int = 0
    sent: int = 0
    conn_id: str = ""

    def __hash__(self) -> int:  # noqa: D401
        return id(self)

    def matches(self, event: str) -> bool:
        """订阅匹配 + scope 二次校验；空 subs 表示全订阅（由上层 scope 过滤兜底）。"""
        # scope 闸门：事件对应的必需 scope 必须被本连接的 app_scopes 覆盖
        from chayuan.server.shared.scopes import covers, event_scope
        required = event_scope(event)
        if not covers(self.app_scopes, required):
            return False

        if not self.subs:
            return True
        for pat in self.subs:
            if pat == event or fnmatch.fnmatchcase(event, pat):
                return True
        return False


class ConnectionHub:
    """进程级单例。"""

    def __init__(self) -> None:
        self._per_app: Dict[str, Set[Connection]] = {}
        self._lock = asyncio.Lock()
        self._total = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 预留多 worker 扩展：注入 async def pub(event:str, payload:dict) -> None
        self._remote_publisher: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None

    # ---- lifecycle ----

    def attach_loop(self) -> None:
        """在 API 启动时调一次，记录事件循环供同步侧 broadcast_sync 使用。"""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def set_remote_publisher(
        self, fn: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]],
    ) -> None:
        """多 worker / 多副本场景下接 Redis Pub/Sub 时注入一个异步 publisher。"""
        self._remote_publisher = fn

    async def close_all(self, code: int = CLOSE_SERVER_STOP, reason: str = "server stopping") -> None:
        """进程关停前优雅关闭所有连接。"""
        async with self._lock:
            conns = [c for s in self._per_app.values() for c in s]
            self._per_app.clear()
            self._total = 0
        for c in conns:
            try:
                await c.ws.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001
                pass

    # ---- 注册 / 注销 ----

    async def add(self, conn: Connection) -> tuple[bool, str]:
        async with self._lock:
            if self._total >= GLOBAL_MAX_CONNECTIONS:
                return False, "global connection limit reached"
            bucket = self._per_app.setdefault(conn.app_id, set())
            if len(bucket) >= PER_APP_MAX_CONNECTIONS:
                return False, f"per-app connection limit reached ({PER_APP_MAX_CONNECTIONS})"
            bucket.add(conn)
            self._total += 1
        return True, ""

    async def remove(self, conn: Connection) -> None:
        async with self._lock:
            bucket = self._per_app.get(conn.app_id)
            if bucket and conn in bucket:
                bucket.discard(conn)
                self._total = max(0, self._total - 1)
                if not bucket:
                    self._per_app.pop(conn.app_id, None)

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "total": self._total,
                "apps": {aid: len(s) for aid, s in self._per_app.items()},
                "limits": {
                    "global": GLOBAL_MAX_CONNECTIONS,
                    "per_app": PER_APP_MAX_CONNECTIONS,
                    "queue": QUEUE_MAX,
                    "max_drops_before_kick": MAX_DROP_BEFORE_KICK,
                },
            }

    async def snapshot(self) -> List[Dict[str, Any]]:
        """把当前所有连接摘要化，供面板 / 运维 API 展示。"""
        out = []
        async with self._lock:
            for app_id, bucket in self._per_app.items():
                for c in bucket:
                    out.append({
                        "app_id": app_id,
                        "peer": c.peer,
                        "conn_id": c.conn_id,
                        "subs": sorted(c.subs),
                        "queue_len": c.queue.qsize(),
                        "sent": c.sent,
                        "drops": c.drops,
                        "idle_s": round(time.monotonic() - c.last_activity, 1),
                    })
        return out

    # ---- 广播：高频路径，不拿大锁 ----

    async def broadcast(self, event: str, data: Dict[str, Any]) -> Dict[str, int]:
        """把事件投递到所有匹配的连接的 queue；永远不 await 实际 send。

        返回投递摘要：``{"delivered": N, "dropped": M, "skipped": K}``。
        """
        payload = {"event": event, "ts": int(time.time()), "data": data}

        # 取连接快照（进出都带锁，快照过程不阻塞 send）
        async with self._lock:
            all_conns = [c for s in self._per_app.values() for c in s]

        delivered = 0
        dropped = 0
        skipped = 0
        kicks: List[Connection] = []

        for c in all_conns:
            if not c.matches(event):
                skipped += 1
                continue
            try:
                c.queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                c.drops += 1
                dropped += 1
                if c.drops >= MAX_DROP_BEFORE_KICK:
                    kicks.append(c)

        # 慢连接踢出（异步，不阻塞本次广播）
        for c in kicks:
            asyncio.create_task(_kick(c, CLOSE_OVERFLOW, "backpressure overflow"))

        # 多 worker 扩展位：也把事件丢给远端 publisher（Redis 等）
        if self._remote_publisher is not None:
            try:
                await self._remote_publisher(event, payload)
            except Exception:  # noqa: BLE001
                logger.exception("remote publisher failed")

        return {"delivered": delivered, "dropped": dropped, "skipped": skipped}

    def broadcast_sync(self, event: str, data: Dict[str, Any]) -> bool:
        """从非 asyncio 线程调用（如 ``callback_dispatcher``）。

        返回 True 表示任务已投递到事件循环；False 表示事件循环未就绪（API 还没启动）。
        """
        loop = self._loop
        if loop is None:
            return False
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(event, data), loop)
            return True
        except RuntimeError:
            return False


# 进程级单例
_HUB: Optional[ConnectionHub] = None


def get_hub() -> ConnectionHub:
    global _HUB
    if _HUB is None:
        _HUB = ConnectionHub()
    return _HUB


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

async def _kick(conn: Connection, code: int, reason: str) -> None:
    try:
        await conn.ws.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001
        pass
    await get_hub().remove(conn)


async def writer_task(conn: Connection) -> None:
    """每连接一个 writer。背压由 queue 自己吸收，send 失败直接结束。"""
    try:
        while True:
            payload = await conn.queue.get()
            try:
                await asyncio.wait_for(conn.ws.send_json(payload), timeout=SEND_TIMEOUT)
                conn.sent += 1
            except asyncio.TimeoutError:
                logger.warning("ws send timeout: app=%s peer=%s", conn.app_id, conn.peer)
                await _kick(conn, CLOSE_OVERFLOW, "send timeout")
                return
            except Exception as e:  # noqa: BLE001
                logger.info("ws send failed: app=%s peer=%s err=%r", conn.app_id, conn.peer, e)
                return
    except asyncio.CancelledError:
        return


async def heartbeat_task(conn: Connection) -> None:
    """服务端周期性 ping；顺带作为「长时间静默」的探测手段。"""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY)
            if time.monotonic() - conn.last_activity > IDLE_KICK:
                await _kick(conn, CLOSE_IDLE, "client idle timeout")
                return
            try:
                await conn.queue.put({"op": "ping", "ts": int(time.time())})
            except asyncio.QueueFull:
                # 队列都满了，早就该踢
                await _kick(conn, CLOSE_OVERFLOW, "queue full on heartbeat")
                return
    except asyncio.CancelledError:
        return
