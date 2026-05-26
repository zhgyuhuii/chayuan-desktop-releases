"""``service_config_page.render_readiness_banner_if_needed`` — **非阻塞合同**(99 题)。

根因
====

旧实现第一行 ``statuses = probe_all()`` — 同步等 5+ services × 3s timeout
ThreadPool 探活,主 event loop 阻塞 3-5s → NiceGUI socket.io 心跳超时 →
浏览器判定 client 死 → 整页 reload 回到 /dashboard → 用户报"点击模型厂商导致
connection lost"。

合同
====

* **同步部分必须 <50ms**(关键:不阻塞 NiceGUI WS 心跳)
* 缓存命中时同步渲染,**不再启 thread**
* 缓存未命中时,同步只 mount banner row(默认 hidden),probe 走 asyncio task
* probe_all() 在测试中可被 monkeypatch 替换,验证不被调到
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, List

import pytest

from chayuan.server.config_panel import service_config_page as scp


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Status:
    """模拟 ServiceStatus(只用到的属性)。"""
    def __init__(self, *, label: str, level: str, ok: bool):
        self.label = label
        self.level = level
        self.ok = ok


class _Element:
    def __init__(self):
        self.text_calls: List[str] = []
        self.style_calls: List[str] = []

    def classes(self, *a, **kw): return self
    def style(self, s: str = "", *a, **kw):
        if s:
            self.style_calls.append(s)
        return self
    def set_text(self, t: str): self.text_calls.append(t)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def tooltip(self, *a, **kw): return self


class _UI:
    def __init__(self):
        self.elements_created: List[_Element] = []
        self.timer_calls: List[tuple] = []

    def _new(self):
        e = _Element()
        self.elements_created.append(e)
        return e

    def row(self, *a, **kw): return self._new()
    def column(self, *a, **kw): return self._new()
    def icon(self, *a, **kw): return self._new()
    def label(self, *a, **kw): return self._new()
    def timer(self, delay, fn, once=False, **kw):
        self.timer_calls.append((delay, fn, once))


@pytest.fixture(autouse=True)
def _reset_cache():
    scp.invalidate_readiness_cache()
    with scp._PROBE_INFLIGHT_LOCK:
        scp._PROBE_INFLIGHT["running"] = False
    yield
    scp.invalidate_readiness_cache()
    with scp._PROBE_INFLIGHT_LOCK:
        scp._PROBE_INFLIGHT["running"] = False


# ---------------------------------------------------------------------------
# 同步部分必须快(<50ms),且不调 probe_all
# ---------------------------------------------------------------------------


def test_render_returns_quickly_without_calling_probe(monkeypatch):
    """缓存空 + 无 event loop 时,函数应立即返回(<50ms),不调 probe_all 同步。"""
    probe_called = {"sync": 0}

    def _fake_probe():
        probe_called["sync"] += 1
        time.sleep(2.0)  # 模拟慢 probe
        return []

    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        _fake_probe,
    )

    ui = _UI()
    t0 = time.time()
    scp.render_readiness_banner_if_needed(ui)
    elapsed = time.time() - t0

    assert elapsed < 0.05, \
        f"同步部分必须 <50ms,实际 {elapsed:.3f}s — 阻塞了 click handler"
    assert probe_called["sync"] == 0, \
        "probe_all 不应在同步路径上被调"


def test_render_does_not_block_when_no_event_loop(monkeypatch):
    """无运行的 event loop 时(测试场景),函数应静默退出,不抛。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        lambda: [],
    )

    ui = _UI()
    # 不在 event loop 内 — asyncio.create_task 会抛 RuntimeError,内部应吞掉
    scp.render_readiness_banner_if_needed(ui)
    # 不应抛


# ---------------------------------------------------------------------------
# 缓存命中:同步渲染,不启 thread
# ---------------------------------------------------------------------------


def test_cache_hit_renders_synchronously(monkeypatch):
    """30s 内缓存命中 → 同步直接 apply,不再启 task。"""
    probe_called = {"n": 0}

    def _probe():
        probe_called["n"] += 1
        return []

    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all", _probe,
    )

    # 预填缓存
    cached = [_Status(label="DB", level="critical", ok=False)]
    scp._set_cached_readiness(cached)

    ui = _UI()
    scp.render_readiness_banner_if_needed(ui)

    assert probe_called["n"] == 0, "缓存命中时不应再调 probe_all"

    # banner row 的 style 应被 set 为 display:flex(因为 critical 故障)
    banner_styles = ui.elements_created[0].style_calls
    assert any("display:flex" in s for s in banner_styles), \
        f"critical 故障时 banner 应展开,style_calls={banner_styles}"


def test_cache_hit_with_all_ok_keeps_banner_hidden(monkeypatch):
    """缓存命中且全 ok → banner 保持 hidden,不修改 display。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        lambda: [],
    )
    cached = [
        _Status(label="DB", level="critical", ok=True),
        _Status(label="Redis", level="recommended", ok=False),
    ]
    scp._set_cached_readiness(cached)

    ui = _UI()
    scp.render_readiness_banner_if_needed(ui)

    banner_styles = ui.elements_created[0].style_calls
    assert not any("display:flex" in s for s in banner_styles), \
        "全 critical ok 时不应展开 banner"


def test_cache_hit_only_recommended_failures_keeps_banner_hidden(monkeypatch):
    """非 critical 的失败不触发 banner — 只显示 critical 故障。"""
    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        lambda: [],
    )
    cached = [
        _Status(label="MinIO", level="recommended", ok=False),
        _Status(label="Redis", level="optional", ok=False),
    ]
    scp._set_cached_readiness(cached)

    ui = _UI()
    scp.render_readiness_banner_if_needed(ui)

    banner_styles = ui.elements_created[0].style_calls
    assert not any("display:flex" in s for s in banner_styles)


# ---------------------------------------------------------------------------
# TTL 失效
# ---------------------------------------------------------------------------


def test_cache_expires_after_ttl():
    cached = [_Status(label="x", level="critical", ok=True)]
    scp._set_cached_readiness(cached)
    # 命中
    assert scp._get_cached_readiness() is not None
    # 短 TTL 立即过期
    assert scp._get_cached_readiness(max_age=-1.0) is None


def test_invalidate_clears_cache():
    cached = [_Status(label="x", level="critical", ok=True)]
    scp._set_cached_readiness(cached)
    scp.invalidate_readiness_cache()
    assert scp._get_cached_readiness() is None


# ---------------------------------------------------------------------------
# 异步路径:probe_all 在 to_thread 内跑,完成后回填
# ---------------------------------------------------------------------------


def test_async_probe_runs_in_thread_pool_and_fills_banner(monkeypatch):
    """无缓存 + 有 event loop → 启 task,probe 在 to_thread 内跑,完成后填 banner。"""
    probe_thread = {"thread_name": ""}

    def _slow_probe():
        probe_thread["thread_name"] = threading.current_thread().name
        time.sleep(0.05)
        return [_Status(label="DB", level="critical", ok=False)]

    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        _slow_probe,
    )

    ui = _UI()

    async def _scenario():
        scp.render_readiness_banner_if_needed(ui)
        # 等 task 完成
        await asyncio.sleep(0.3)

    asyncio.run(_scenario())

    # probe 应在某个 worker thread 跑(不是 main)
    assert probe_thread["thread_name"], "probe_all 应被异步调到"
    main_name = threading.main_thread().name
    assert probe_thread["thread_name"] != main_name, \
        f"probe 不应在主线程跑;实际 {probe_thread['thread_name']}"

    # 完成后 banner 应被展开 + label 文本被设
    banner_styles = ui.elements_created[0].style_calls
    assert any("display:flex" in s for s in banner_styles), \
        "probe 完成后 banner 应展开"

    # 缓存应被填充
    assert scp._get_cached_readiness() is not None


def test_inflight_dedup_prevents_duplicate_probes(monkeypatch):
    """快速切页(2 次 render)时,同时只跑 1 个 probe thread。"""
    call_count = {"n": 0}
    started = threading.Event()
    finish = threading.Event()

    def _slow_probe():
        with threading.Lock():
            call_count["n"] += 1
        started.set()
        finish.wait(timeout=2.0)
        return []

    monkeypatch.setattr(
        "chayuan.server.config_panel.service_config_page.probe_all",
        _slow_probe,
    )

    async def _scenario():
        ui1 = _UI()
        ui2 = _UI()
        scp.render_readiness_banner_if_needed(ui1)
        # 第二次 render 时,第一次还在 in-flight
        scp.render_readiness_banner_if_needed(ui2)
        # 等到 probe 启动
        await asyncio.sleep(0.05)
        # 此时应该只有 1 个 probe 跑
        finish.set()
        await asyncio.sleep(0.3)

    asyncio.run(_scenario())

    assert call_count["n"] == 1, \
        f"in-flight 去重失败 — probe 被调 {call_count['n']} 次,应为 1"
