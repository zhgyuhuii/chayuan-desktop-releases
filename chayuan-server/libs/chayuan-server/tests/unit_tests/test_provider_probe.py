"""``provider_probe`` — 并发探活 + 缓存 + 后台执行(98 题)。

不依赖真实 HTTP — monkeypatch ``probe_one`` 控制每厂商返回什么。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List

import pytest

from chayuan.server.config_panel.model_settings import provider_probe
from chayuan.server.config_panel.model_settings.provider_probe import (
    ProbeResult,
    get_all_cached,
    get_cached,
    invalidate_cache,
    probe_all,
    probe_all_in_background,
    probe_one,
    set_cached,
)


@dataclass
class _FakeState:
    pid: str
    api_base_url: str = ""
    api_key: str = ""
    api_proxy: str = ""


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_cache()
    yield
    invalidate_cache()


# ---------------------------------------------------------------------------
# probe_one — 输入校验(不实际 HTTP,纯逻辑)
# ---------------------------------------------------------------------------


def test_probe_one_skips_when_api_base_empty():
    s = _FakeState(pid="empty", api_base_url="")
    r = probe_one(s, timeout=0.1)
    assert r.status == "skipped"
    assert "api_base_url 为空" in r.error


def test_probe_one_unreachable_returns_unreachable_status(monkeypatch):
    """连不上的端口 → status=unreachable / timeout。"""
    s = _FakeState(
        pid="bad", api_base_url="http://127.0.0.1:1",  # 几乎不可能开的端口
    )
    r = probe_one(s, timeout=0.3)
    # 1 端口实际多数会立刻 ConnectionRefused → unreachable;若慢则 timeout
    assert r.status in ("unreachable", "timeout")
    assert r.pid == "bad"


# ---------------------------------------------------------------------------
# 缓存 API
# ---------------------------------------------------------------------------


def test_set_and_get_cached():
    r = ProbeResult(pid="x", status="ok", latency_ms=42, http_status=200)
    set_cached(r)
    got = get_cached("x")
    assert got is not None
    assert got.pid == "x"
    assert got.online is True


def test_get_cached_returns_none_when_stale():
    """5 分钟前的 ProbeResult → fresh=False → get_cached 返回 None。"""
    r = ProbeResult(
        pid="stale", status="ok",
        checked_at=time.time() - 600.0,
    )
    set_cached(r)
    assert get_cached("stale") is None
    # 但 get_all_cached 仍包含
    assert "stale" in get_all_cached()


def test_invalidate_cache_specific_pid():
    set_cached(ProbeResult(pid="a", status="ok"))
    set_cached(ProbeResult(pid="b", status="ok"))
    invalidate_cache("a")
    assert get_cached("a") is None
    assert get_cached("b") is not None


def test_invalidate_cache_all():
    set_cached(ProbeResult(pid="a", status="ok"))
    set_cached(ProbeResult(pid="b", status="ok"))
    invalidate_cache()  # 无参数 = 清全部
    assert get_all_cached() == {}


# ---------------------------------------------------------------------------
# probe_all — 并发 + deadline + 写缓存
# ---------------------------------------------------------------------------


def test_probe_all_concurrent_runs_all_states(monkeypatch):
    """probe_all 应并发跑所有 states,每个调一次 probe_one。"""
    call_lock = threading.Lock()
    call_count = {"n": 0}

    def _fake_probe_one(s, timeout=1.5):
        with call_lock:
            call_count["n"] += 1
        time.sleep(0.05)  # 模拟 IO
        return ProbeResult(pid=s.pid, status="ok", latency_ms=50)

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        _fake_probe_one,
    )

    states = [_FakeState(pid=f"p{i}", api_base_url=f"http://host{i}") for i in range(10)]
    t0 = time.time()
    results = probe_all(states, max_workers=10, timeout=1.0, overall_deadline=3.0)
    elapsed = time.time() - t0

    assert call_count["n"] == 10
    assert len(results) == 10
    assert all(r.status == "ok" for r in results.values())
    # 10 个 0.05s 串行 = 0.5s;并发 10 worker ≈ 0.05-0.1s
    assert elapsed < 0.4, \
        f"并发应 < 0.4s,实际 {elapsed:.3f}s — 怀疑没真并发"


def test_probe_all_writes_cache_when_enabled(monkeypatch):
    """write_cache=True(默认)→ 结果写入模块级缓存。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        lambda s, timeout=1.5: ProbeResult(pid=s.pid, status="ok"),
    )
    states = [_FakeState(pid="cached_x", api_base_url="http://x")]
    probe_all(states, max_workers=2, write_cache=True)

    assert get_cached("cached_x") is not None


def test_probe_all_skips_cache_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        lambda s, timeout=1.5: ProbeResult(pid=s.pid, status="ok"),
    )
    states = [_FakeState(pid="not_cached", api_base_url="http://x")]
    probe_all(states, max_workers=2, write_cache=False)

    assert get_cached("not_cached") is None


def test_probe_all_handles_probe_one_exceptions(monkeypatch):
    """单个 probe_one 抛异常 → 该项标 unreachable,**不影响其他厂商**。"""
    def _flaky(s, timeout=1.5):
        if s.pid == "bad":
            raise RuntimeError("oops")
        return ProbeResult(pid=s.pid, status="ok")

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        _flaky,
    )
    states = [
        _FakeState(pid="ok1", api_base_url="http://x"),
        _FakeState(pid="bad", api_base_url="http://x"),
        _FakeState(pid="ok2", api_base_url="http://x"),
    ]
    results = probe_all(states, max_workers=4)

    assert results["ok1"].status == "ok"
    assert results["ok2"].status == "ok"
    assert results["bad"].status == "unreachable"


# ---------------------------------------------------------------------------
# probe_all_in_background — 不阻塞 + on_done 回调
# ---------------------------------------------------------------------------


def test_probe_all_in_background_does_not_block(monkeypatch):
    """probe_all_in_background 必须立刻返回(<10ms),探活在后台 thread 跑。"""
    started = threading.Event()
    finish = threading.Event()

    def _slow_probe(s, timeout=1.5):
        started.set()
        finish.wait(timeout=2.0)
        return ProbeResult(pid=s.pid, status="ok")

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        _slow_probe,
    )

    states = [_FakeState(pid="bg_x", api_base_url="http://x")]
    t0 = time.time()
    th = probe_all_in_background(states, max_workers=2)
    elapsed = time.time() - t0

    assert elapsed < 0.05, f"应立即返回,实际 {elapsed:.3f}s"
    assert th.is_alive()
    started.wait(timeout=1.0)
    finish.set()
    th.join(timeout=2.0)
    assert not th.is_alive()


def test_probe_all_in_background_calls_on_done(monkeypatch):
    """on_done 回调在探活完成后被调,参数是 results dict。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        lambda s, timeout=1.5: ProbeResult(pid=s.pid, status="ok"),
    )

    callback_done = threading.Event()
    received_results: List[dict] = []

    def _on_done(results):
        received_results.append(dict(results))
        callback_done.set()

    states = [_FakeState(pid="cb_a", api_base_url="http://x")]
    probe_all_in_background(states, max_workers=2, on_done=_on_done)

    assert callback_done.wait(timeout=2.0)
    assert len(received_results) == 1
    assert "cb_a" in received_results[0]


def test_probe_all_in_background_on_done_exception_swallowed(monkeypatch):
    """on_done 抛错时 → 不向探活线程外抛(只是日志)。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.model_settings.provider_probe.probe_one",
        lambda s, timeout=1.5: ProbeResult(pid=s.pid, status="ok"),
    )

    def _broken_callback(results):
        raise ValueError("kaboom")

    states = [_FakeState(pid="cb_x", api_base_url="http://x")]
    th = probe_all_in_background(states, max_workers=2, on_done=_broken_callback)
    th.join(timeout=2.0)
    # 不应让本测试线程崩
    assert not th.is_alive()
