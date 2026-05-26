"""厂商连通性并发探活 — 后台 ThreadPool 批量 ping 所有云厂商 ``/models``。

设计动机
========

UI 层 ``state_lookup(pid)`` 只读内存里的 ``_PlatformState``(api_key 是否填、
模型数),并不真的探活;但用户希望在 hero card 上看到"在线/离线"绿灯。

如果在主 event loop 用 ``httpx.Client.get`` 串行探活 30+ 厂商 × 1.5s timeout,
最坏 45s 阻塞 → 必然 connection lost。所以**必须并发 + 后台 + 缓存**。

模块结构
========

* ``ProbeResult``:单厂商探活结果(``status``、``latency_ms``、``error``)
* ``probe_one(state, timeout)``:探单个厂商,纯函数,无副作用,可单测
* ``probe_all(states, *, max_workers, timeout)``:并发探所有,返回 ``Dict[pid, ProbeResult]``
* ``probe_all_in_background(states, *, on_done)``:不阻塞调用方,后台跑;完成后调 ``on_done``
* ``get_cached(pid)`` / ``set_cached(pid, result)``:跨 tab/会话共享 ProbeResult 缓存
* ``invalidate_cache(*pids)``:保存厂商配置后调用

并发参数
========

* ``max_workers=32``:常见云厂商总数 ≤ 60,32 worker 足以一帧并发完成
* ``timeout=1.5s``:探活只为 UI 状态指示,不需要等长超时
* ``ProbeResult`` 5 分钟 TTL:避免每次切 tab 都重探

线程安全
========

* ``_CACHE`` + ``_CACHE_LOCK``:RLock 保护读写
* ``ThreadPoolExecutor`` 由 ``probe_all`` 创建并 ``shutdown(wait=True)``
* ``probe_all_in_background`` 用 daemon 线程 + 内嵌 ThreadPool,不污染全局
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(
    "chayuan.config_panel.model_settings.provider_probe"
)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """单厂商探活结果。"""

    pid: str
    status: str  # "ok" / "auth_fail" / "unreachable" / "skipped" / "timeout"
    latency_ms: int = 0
    http_status: Optional[int] = None
    error: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def online(self) -> bool:
        return self.status == "ok"

    @property
    def fresh(self) -> bool:
        """5 分钟内的结果视为新鲜,可直接用于 UI。"""
        return (time.time() - self.checked_at) < 300.0


# ---------------------------------------------------------------------------
# 缓存(跨 tab 共享,5 分钟 TTL)
# ---------------------------------------------------------------------------

_CACHE: Dict[str, ProbeResult] = {}
_CACHE_LOCK = threading.RLock()


def get_cached(pid: str) -> Optional[ProbeResult]:
    """读缓存;过期(>5min)返回 ``None``。"""
    with _CACHE_LOCK:
        r = _CACHE.get(pid)
        if r is None or not r.fresh:
            return None
        return r


def set_cached(result: ProbeResult) -> None:
    """写缓存。线程安全。"""
    with _CACHE_LOCK:
        _CACHE[result.pid] = result


def invalidate_cache(*pids: str) -> None:
    """失效缓存。无参数 → 清全部;有 pid → 清这些。"""
    with _CACHE_LOCK:
        if not pids:
            _CACHE.clear()
            return
        for pid in pids:
            _CACHE.pop(pid, None)


def get_all_cached() -> Dict[str, ProbeResult]:
    """快照所有缓存(过期项也包含,UI 可自己判断 ``fresh``)。"""
    with _CACHE_LOCK:
        return dict(_CACHE)


# ---------------------------------------------------------------------------
# 单厂商探活(纯函数,无副作用)
# ---------------------------------------------------------------------------


def probe_one(state: Any, timeout: float = 1.5) -> ProbeResult:
    """探单个厂商:``GET {api_base_url}/models``,记录延迟与错误。

    ``state`` 是 ``_PlatformState``(用 duck-typing,不强依赖 import,避免
    循环导入);需要的属性:``pid``、``api_base_url``、``api_key``、``api_proxy``。

    跳过条件:
        * ``api_base_url`` 为空 → ``status="skipped"``,不计入"离线"
        * ``api_key`` 为空且非 EMPTY 占位 → 也尝试探(部分厂商 health 端点不要 key)

    返回结果不会自动写缓存 — ``probe_all`` / ``probe_all_in_background`` 才写。
    """
    pid = str(getattr(state, "pid", "") or "")
    api_base = str(getattr(state, "api_base_url", "") or "").rstrip("/")
    api_key = str(getattr(state, "api_key", "") or "").strip()
    api_proxy = str(getattr(state, "api_proxy", "") or "").strip()

    if not api_base:
        return ProbeResult(pid=pid, status="skipped", error="api_base_url 为空")

    try:
        import httpx
    except Exception as e:  # noqa: BLE001
        return ProbeResult(pid=pid, status="skipped", error=f"httpx 不可用: {e}")

    headers: Dict[str, str] = {}
    if api_key and api_key != "EMPTY":
        headers["Authorization"] = f"Bearer {api_key}"

    kwargs: Dict[str, Any] = {"timeout": timeout, "headers": headers}
    if api_proxy:
        kwargs["proxy"] = api_proxy
    url = f"{api_base}/models"

    t0 = time.time()
    try:
        with httpx.Client(**kwargs) as c:
            resp = c.get(url)
        latency_ms = int((time.time() - t0) * 1000)

        if resp.status_code == 200:
            return ProbeResult(
                pid=pid, status="ok",
                latency_ms=latency_ms, http_status=200,
            )
        if resp.status_code in (401, 403):
            return ProbeResult(
                pid=pid, status="auth_fail",
                latency_ms=latency_ms, http_status=resp.status_code,
                error=f"鉴权失败(HTTP {resp.status_code})",
            )
        return ProbeResult(
            pid=pid, status="unreachable",
            latency_ms=latency_ms, http_status=resp.status_code,
            error=f"HTTP {resp.status_code}",
        )
    except Exception as e:  # noqa: BLE001
        latency_ms = int((time.time() - t0) * 1000)
        # httpx.TimeoutException / ConnectError 都归 timeout / unreachable
        cls = type(e).__name__
        if "Timeout" in cls or latency_ms >= int(timeout * 1000) - 50:
            return ProbeResult(
                pid=pid, status="timeout",
                latency_ms=latency_ms, error=f"{cls}: {e}",
            )
        return ProbeResult(
            pid=pid, status="unreachable",
            latency_ms=latency_ms, error=f"{cls}: {e}",
        )


# ---------------------------------------------------------------------------
# 批量并发探活
# ---------------------------------------------------------------------------


def probe_all(
    states: Iterable[Any],
    *,
    max_workers: int = 32,
    timeout: float = 1.5,
    overall_deadline: float = 5.0,
    write_cache: bool = True,
) -> Dict[str, ProbeResult]:
    """并发探活所有 states,deadline-bound。

    Args:
        states: ``_PlatformState`` 列表/生成器。
        max_workers: ThreadPool 并发度;default 32 应对 ~60 厂商绰绰有余。
        timeout: 单厂商 HTTP timeout(秒)。
        overall_deadline: 总超时(秒);超过后未完成的厂商视为 ``timeout``。
        write_cache: ``True`` 时把结果写到模块级缓存,``False`` 仅返回。

    Returns:
        ``{pid: ProbeResult}``,**包含所有 states 的 pid**(超时项 status=timeout)。
    """
    items: List[Any] = [s for s in states if getattr(s, "pid", None)]
    results: Dict[str, ProbeResult] = {}
    if not items:
        return results

    workers = min(max_workers, max(2, len(items)))
    deadline = time.time() + max(0.5, float(overall_deadline))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="provider-probe") as ex:
        future_to_pid = {
            ex.submit(probe_one, s, timeout): str(s.pid) for s in items
        }
        for fut in as_completed(future_to_pid, timeout=overall_deadline + 0.5):
            pid = future_to_pid[fut]
            remaining = deadline - time.time()
            if remaining < 0:
                # deadline 已过 — 取消余下 future,标记 timeout
                results[pid] = ProbeResult(
                    pid=pid, status="timeout",
                    error=f"超出总 deadline {overall_deadline}s",
                )
                continue
            try:
                results[pid] = fut.result(timeout=remaining)
            except Exception as e:  # noqa: BLE001
                results[pid] = ProbeResult(
                    pid=pid, status="unreachable",
                    error=f"future 异常: {type(e).__name__}: {e}",
                )

    # 把没跑完的 future 也填上 timeout
    for fut, pid in future_to_pid.items():
        if pid in results:
            continue
        results[pid] = ProbeResult(
            pid=pid, status="timeout", error="未在 deadline 内完成",
        )

    if write_cache:
        for r in results.values():
            set_cached(r)

    logger.info(
        "provider_probe.probe_all: %d 厂商探活完成 (%d ok / %d auth_fail / "
        "%d unreachable / %d timeout / %d skipped)",
        len(results),
        sum(1 for r in results.values() if r.status == "ok"),
        sum(1 for r in results.values() if r.status == "auth_fail"),
        sum(1 for r in results.values() if r.status == "unreachable"),
        sum(1 for r in results.values() if r.status == "timeout"),
        sum(1 for r in results.values() if r.status == "skipped"),
    )
    return results


def probe_all_in_background(
    states: Iterable[Any],
    *,
    max_workers: int = 32,
    timeout: float = 1.5,
    overall_deadline: float = 5.0,
    on_done: Optional[Callable[[Dict[str, ProbeResult]], None]] = None,
) -> threading.Thread:
    """非阻塞版:启 daemon 线程跑 ``probe_all``,完成后调 ``on_done``。

    Args:
        on_done: 可选回调,签名 ``(results) -> None``;在探活线程内调用,
            如要更新 NiceGUI UI 必须自行 marshal 到 client scope(``ui.timer(0, ...)``)。

    Returns:
        启动的 ``threading.Thread``(daemon),调用方可选地 ``join``。
    """
    states_list = list(states)

    def _run() -> None:
        try:
            results = probe_all(
                states_list,
                max_workers=max_workers,
                timeout=timeout,
                overall_deadline=overall_deadline,
                write_cache=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("probe_all_in_background failed: %s", e)
            results = {}
        if on_done is not None:
            try:
                on_done(results)
            except Exception as e:  # noqa: BLE001
                logger.debug("probe_all on_done callback failed: %s", e)

    t = threading.Thread(
        target=_run, daemon=True, name="provider-probe-batch",
    )
    t.start()
    return t


__all__ = [
    "ProbeResult",
    "probe_one",
    "probe_all",
    "probe_all_in_background",
    "get_cached", "set_cached", "invalidate_cache", "get_all_cached",
]
