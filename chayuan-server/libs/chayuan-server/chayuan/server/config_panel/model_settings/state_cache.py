"""跨 tab 共享缓存 — states / healths / capability_grouped / capability_defaults。

设计动机:4 个 tab 共用同一份 ``MODEL_PLATFORMS`` yaml 解析 + framework
健康探活;切 tab 重复 IO 是 lost connection 的次因。

线程安全:所有 getter / invalidate 都用 ``_lock`` 保护;getter 之间互斥但不阻塞。

调用方式::

    states = state_cache.get_states()         # lazy 首次加载
    state_cache.invalidate("states")           # 保存厂商配置后调
    state_cache.prefetch_in_threads(timeout=3.0)  # 进入页面立刻并发预热
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.model_settings.state_cache")

_lock = threading.RLock()  # 可重入,避免 prefetch 调 getter 时死锁

# 缓存槽
_states: Optional[List[Any]] = None
_healths: Optional[Dict[str, Any]] = None
_capability_grouped: Optional[Dict[str, Any]] = None
_capability_defaults: Optional[Dict[str, Any]] = None


def get_states(force: bool = False) -> List[Any]:
    """模型平台 ``_PlatformState`` 列表(从 ``MODEL_PLATFORMS`` yaml 解析)。

    首次调用 lazy 加载;``force=True`` 重读。
    """
    global _states
    with _lock:
        if _states is None or force:
            from chayuan.server.config_panel.model_config import (
                _build_initial_states,
            )
            _states = _build_initial_states()
        return _states


def get_healths(force: bool = False) -> Dict[str, Any]:
    """``probe_all_frameworks`` 探活结果(deadline 3s)。"""
    global _healths
    with _lock:
        if _healths is None or force:
            try:
                from chayuan.server.config_panel.runtime_framework_panel import (
                    probe_all_frameworks,
                )
                _healths = probe_all_frameworks() or {}
            except Exception as e:  # noqa: BLE001
                logger.warning("probe_all_frameworks failed: %s", e)
                _healths = {}
        return _healths


def get_capability_grouped(force: bool = False) -> Dict[str, Any]:
    """capability 默认模型候选分组(各 capability 在哪些 framework/platform 可用)。"""
    global _capability_grouped
    with _lock:
        if _capability_grouped is None or force:
            try:
                from chayuan.server.config_panel.runtime_framework_panel import (
                    _capability_grouped as _impl,
                )
                _capability_grouped = _impl()
            except Exception as e:  # noqa: BLE001
                logger.warning("_capability_grouped failed: %s", e)
                _capability_grouped = {}
        return _capability_grouped


def get_capability_defaults(force: bool = False) -> Dict[str, Any]:
    """已选择的默认模型(从 model_settings.yaml 的 default_xx_model 读)。"""
    global _capability_defaults
    with _lock:
        if _capability_defaults is None or force:
            try:
                from chayuan.server.config_panel.runtime_framework_panel import (
                    _load_capability_defaults,
                )
                _capability_defaults = _load_capability_defaults()
            except Exception as e:  # noqa: BLE001
                logger.warning("_load_capability_defaults failed: %s", e)
                _capability_defaults = {}
        return _capability_defaults


# ---------------------------------------------------------------------------
# 失效
# ---------------------------------------------------------------------------

_VALID_KEYS = {"states", "healths", "grouped", "defaults", "all"}

# 98 题加了"invalidate 默认 rewarm"。但 101 题发现:
#   * defaults_subpage 进入 ④ tab 时调 ``invalidate("grouped","defaults")``
#     紧接着同步 ``get_capability_grouped() / get_capability_defaults()``
#   * 默认 rewarm=True 时,invalidate 启 2 个后台 thread,与主线程 get
#     同时调 ``runtime_framework_panel._capability_grouped()`` →
#     ``get_config_platforms()`` 读 yaml + Settings,触发并发 race,
#     ④ tab 的 select 选项偶发为空,用户感知"无法选择"。
#
# 改回默认 False — 显式传 ``rewarm=True`` 才预热。和 98 题之前的语义一致。
_AUTO_REWARM = False


def invalidate(*keys: str, rewarm: Optional[bool] = None) -> None:
    """细粒度失效 + 可选后台预热。``invalidate("all")`` 清全部。

    保存厂商配置后:``invalidate("states")``;
    安装/启停 framework 后:``invalidate("healths","grouped")``;
    保存默认模型后:``invalidate("defaults")``。

    Args:
        keys: 要失效的缓存槽,见 ``_VALID_KEYS``。
        rewarm: 失效后是否启动后台 thread 重新预热被清的槽。
            ``None``(默认)→ 用 ``_AUTO_REWARM``(101 题:默认 ``False``)。
            ``True`` → 启 daemon thread 后台调对应 getter 预热;调用方需保证
            自己接下来不会立即同步 get 同一个 key,否则可能与 rewarm thread
            竞争 yaml/Settings 读(④ tab connection bug 的根因)。
            ``False`` → 仅失效不预热(默认)。
    """
    global _states, _healths, _capability_grouped, _capability_defaults
    cleared: List[str] = []
    with _lock:
        for k in keys:
            if k not in _VALID_KEYS:
                logger.debug("state_cache.invalidate: unknown key %r", k)
                continue
            if k in ("states", "all"):
                _states = None
                cleared.append("states")
            if k in ("healths", "all"):
                _healths = None
                cleared.append("healths")
            if k in ("grouped", "all"):
                _capability_grouped = None
                cleared.append("grouped")
            if k in ("defaults", "all"):
                _capability_defaults = None
                cleared.append("defaults")
    # 出锁后再决定预热(避免 _rewarm_in_background 竞争同一把锁)
    do_rewarm = _AUTO_REWARM if rewarm is None else bool(rewarm)
    if do_rewarm and cleared:
        _rewarm_in_background(cleared)


def _rewarm_in_background(keys: List[str]) -> None:
    """后台并发预热被失效的缓存槽。

    每个 key 起独立 daemon 线程,异常吞掉(只是预热,失败不影响功能)。
    用户切回 tab 时再调 ``get_xxx()`` 大概率直接 cache 命中。
    """
    targets = {
        "states": get_states,
        "healths": get_healths,
        "grouped": get_capability_grouped,
        "defaults": get_capability_defaults,
    }

    def _run(name: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
            logger.debug("state_cache rewarm %s done", name)
        except Exception as e:  # noqa: BLE001
            logger.debug("state_cache rewarm %s failed: %s", name, e)

    for k in set(keys):
        fn = targets.get(k)
        if not fn:
            continue
        t = threading.Thread(
            target=_run, args=(k, fn),
            daemon=True, name=f"state-cache-rewarm-{k}",
        )
        t.start()


# ---------------------------------------------------------------------------
# 并发预取
# ---------------------------------------------------------------------------


def prefetch_in_threads(timeout: float = 3.0) -> Dict[str, bool]:
    """4 个 getter 并发跑,deadline-based join,总超时 ``timeout`` 秒。

    返回每项 ``True/False`` 表示是否在 deadline 内完成。

    用法:进入"模型设置"页时调一次,后续 4 个 tab 切换都是 cache 命中。
    """
    results: Dict[str, bool] = {
        "states": False, "healths": False,
        "grouped": False, "defaults": False,
    }

    targets: List[tuple] = [
        ("states", get_states),
        ("healths", get_healths),
        ("grouped", get_capability_grouped),
        ("defaults", get_capability_defaults),
    ]

    threads: List[threading.Thread] = []
    for name, fn in targets:
        def _run(n: str = name, f: Callable[[], Any] = fn) -> None:
            try:
                f()
                results[n] = True
            except Exception as e:  # noqa: BLE001
                logger.debug("prefetch %s failed: %s", n, e)
        t = threading.Thread(target=_run, daemon=True, name=f"state-cache-{name}")
        t.start()
        threads.append(t)

    deadline = time.time() + max(0.5, float(timeout))
    for t in threads:
        remaining = max(0.0, deadline - time.time())
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    completed = sum(1 for v in results.values() if v)
    logger.info(
        "state_cache.prefetch_in_threads: %d/4 完成 (timeout=%.1fs)",
        completed, timeout,
    )
    return results


__all__ = [
    "get_states", "get_healths",
    "get_capability_grouped", "get_capability_defaults",
    "invalidate", "prefetch_in_threads",
]
