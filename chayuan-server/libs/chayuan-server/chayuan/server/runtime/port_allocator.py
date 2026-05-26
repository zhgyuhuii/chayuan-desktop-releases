"""端口分配器（Port Allocator）。

为什么单写一个？
==================

察元在两类场景都要分配端口：

* **vendor 二进制 / docker 服务**（Postgres / Redis / MinIO / Milvus / Ollama
  …）—— 第一次启动时按 ``preferred_port`` 申请；端口被占就在指定范围
  里递增直到找到一个空闲的，结果落库到 ``runtime.json`` 让下次重启复用。
* **chayuan 自身的 API / 配置面板**—— 默认 62581 / 8502 已经写在
  ``settings.basic_settings``，但 dev 环境同时跑两套实例时也希望"端口被占自
  动让位"。

``socket(SO_REUSEADDR).bind`` 探活比"看 LISTEN 表"更可靠：能避开 docker /
vmware 等用 SO_EXCLUSIVE_ADDRUSE 占着但 LISTEN 表里看不到的端口。

设计要点
========

* **可注入的 ``bind_check``**：默认走真正的 ``socket.bind``；单测可注入一个
  ``set[int]`` 模拟"占用列表"，无需开真端口。
* **持久化由调用方负责**：本类只做"分配"。把分配到的端口写到 ``runtime.json``
  / yaml 是调用方的事，避免本模块直接耦合 Settings。
* **可重入**：``allocate(name=..., preferred=...)`` 多次调用会优先返回上次分到
  的端口（如果它仍空闲），让"重启不换端口"成为默认行为。

线程安全：内部锁保护已分配集合 ``self._taken``。

公开 API：``PortAllocator`` 类、``is_port_free(port)`` 工具函数、
``PortInUseError`` 异常。
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

logger = logging.getLogger("chayuan.runtime.port_allocator")


# 默认动态端口范围：刻意避开常见服务端口段（< 32768）+ Linux 临时端口段
# （32768-60999 通常被 outbound 临时连接抢用）。
# 选 [40000, 60999] 既离常见默认端口够远（容易识别），又留出余量。
DEFAULT_PORT_RANGE: Tuple[int, int] = (40000, 60999)


class PortInUseError(RuntimeError):
    """整个候选范围都没空闲端口。"""


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """实时探活：能 ``bind(host, port)`` 即认为空闲。

    显式 ``SO_REUSEADDR=0`` 避免被 ``TIME_WAIT`` 误判为可用；同时不会和
    后续要长占该端口的服务"打架"。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # SO_REUSEADDR=0 是默认值，但 Linux 上某些 distro 默认会开；显式置 0
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind((host, int(port)))
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass
    return True


class PortAllocator:
    """有状态的"申请一个空闲端口"工具。

    Args:
        port_range: 候选范围（含两端）。``allocate`` 时会从 ``preferred``
            起线性向上探，到 high 后绕回 low 继续探。
        bind_check: 端口是否空闲的判定函数；默认走 :func:`is_port_free`。
            单测时可传一个用 ``set`` 实现的 mock。
        host: 默认绑定地址，仅用于真实 ``socket.bind`` 时；mock 场景不关心。
    """

    def __init__(
        self,
        port_range: Tuple[int, int] = DEFAULT_PORT_RANGE,
        *,
        bind_check: Optional[Callable[[int], bool]] = None,
        host: str = "127.0.0.1",
    ) -> None:
        lo, hi = int(port_range[0]), int(port_range[1])
        if lo < 1 or hi > 65535 or lo > hi:
            raise ValueError(f"invalid port range: {port_range}")
        self._lo, self._hi = lo, hi
        self._taken: Set[int] = set()
        self._bind_check = bind_check or (lambda p: is_port_free(p, host))
        self._host = host
        self._lock = threading.Lock()
        # name → 已分配端口；让重复 allocate(name=…) 默认稳定
        self._named: Dict[str, int] = {}

    # -- 注入已知占用的端口（如来自 runtime.json 的历史记录）--

    def reserve(self, port: int, *, name: Optional[str] = None) -> None:
        """声明 ``port`` 已被分配，allocate 不再返回它。

        典型用法：把上次启动写到 ``runtime.json`` 的端口先 reserve 进来，
        然后再 allocate 其它服务，避免新服务抢走老服务的端口。
        """
        with self._lock:
            self._taken.add(int(port))
            if name:
                self._named[name] = int(port)

    def release(self, port: int) -> None:
        """归还端口（仅维护状态，不做 socket 操作）。"""
        with self._lock:
            self._taken.discard(int(port))

    @property
    def taken(self) -> Set[int]:
        """已记账的端口集合（仅用于调试/测试 introspection）。"""
        with self._lock:
            return set(self._taken)

    # -- 主入口 --

    def allocate(
        self,
        *,
        preferred: Optional[int] = None,
        name: Optional[str] = None,
        skip: Optional[Iterable[int]] = None,
    ) -> int:
        """返回一个空闲端口，优先用 ``preferred`` / 同名历史端口。

        Args:
            preferred: 偏好端口；若空闲则直接返回。
            name: 给本次分配命个名；下次同名再 allocate 时会优先复用上次结果，
                让"同一个 vendor 服务跨进程稳定占同一端口"成为默认行为。
            skip: 本次额外跳过的端口（例如 MinIO 已经分给 API，不希望 console
                抢同一端口）。

        Returns:
            分配到的端口号。

        Raises:
            PortInUseError: 当整个范围都被占满 / skip 掉时。
        """
        skip_set: Set[int] = set(int(x) for x in (skip or ()))

        with self._lock:
            # 1) name 有历史 → 优先复用（仍探活一次防被外部抢占）
            if name and name in self._named:
                old = self._named[name]
                if old not in skip_set and self._bind_check(old):
                    self._taken.add(old)
                    return old

            # 2) preferred → 直接探一次。
            # 关键：无论 preferred 是不是在 [lo, hi]，**都先单独试一次**。
            # 这是过去的一个 bug：当 ``preferred=62581``（API 默认）但
            # ``port_range=(40000, 60999)`` 时，旧版本会在第 165 行把它过滤掉，
            # 然后日志谎报"62581 已被占用"。事实上根本没探过它。
            #
            # 现在：preferred 即使在 range 之外也尝试 bind；如果它确实空闲，
            # 直接返回；否则再走 range 内顺序探。
            preferred_was_outside_range = False
            preferred_was_busy = False
            if preferred is not None:
                p_pref = int(preferred)
                if p_pref not in skip_set and p_pref not in self._taken:
                    if self._bind_check(p_pref):
                        self._taken.add(p_pref)
                        if name:
                            self._named[name] = p_pref
                        return p_pref
                    preferred_was_busy = True
                if not (self._lo <= p_pref <= self._hi):
                    preferred_was_outside_range = True

            # 3) 范围内顺序探（从 preferred+1 开始绕一圈）
            candidates: list = []
            start = (
                max(self._lo, int(preferred) + 1)
                if preferred is not None and self._lo <= int(preferred) + 1 <= self._hi
                else self._lo
            )
            for p in range(start, self._hi + 1):
                candidates.append(p)
            if start > self._lo:
                for p in range(self._lo, start):
                    candidates.append(p)

            for p in candidates:
                if p in skip_set or p in self._taken:
                    continue
                if self._bind_check(p):
                    self._taken.add(p)
                    if name:
                        self._named[name] = p
                    if preferred is not None and p != preferred:
                        if preferred_was_outside_range:
                            logger.info(
                                "[port-allocator] %s preferred=%s 不在配置的"
                                "端口范围 %s-%s 内（且本机也没空闲在用），"
                                "自动改用 %s",
                                name or "(anon)", preferred,
                                self._lo, self._hi, p,
                            )
                        elif preferred_was_busy:
                            logger.info(
                                "[port-allocator] %s preferred=%s 已被占用，"
                                "自动改用 %s",
                                name or "(anon)", preferred, p,
                            )
                    return p

        raise PortInUseError(
            f"port range {self._lo}-{self._hi} exhausted "
            f"(preferred={preferred}, name={name})"
        )


__all__ = [
    "DEFAULT_PORT_RANGE",
    "PortAllocator",
    "PortInUseError",
    "is_port_free",
]
