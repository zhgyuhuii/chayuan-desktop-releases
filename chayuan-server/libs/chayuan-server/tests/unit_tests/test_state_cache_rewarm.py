"""``state_cache.invalidate`` — 后台预热钩子(98 题)。

测试 ``invalidate(...)`` 后是否启动后台 thread 重新预热被清的槽,以及
``rewarm=False`` 关键字开关。

为了不依赖真实的 yaml/probe,monkeypatch 4 个 getter 内部依赖。
"""
from __future__ import annotations

import threading
import time

import pytest

from chayuan.server.config_panel.model_settings import state_cache


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """每个测试开始前清缓存,避免互相影响。"""
    state_cache.invalidate("all", rewarm=False)
    yield
    state_cache.invalidate("all", rewarm=False)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """忙等 predicate 为真;每 10ms 检查一次。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------------------
# 101 题:_AUTO_REWARM 默认 False — 默认不启动后台 thread,避免与
# defaults_subpage 内 invalidate+立即 get 的 race(yaml/Settings 并发读)
# ---------------------------------------------------------------------------


def test_default_invalidate_does_not_trigger_rewarm(monkeypatch):
    """101 题回归:默认 invalidate(无 rewarm 参数)不应启动后台 thread。"""
    call_count = {"n": 0}

    def _fake_build():
        call_count["n"] += 1
        return ["fake"]

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_config._build_initial_states",
        _fake_build,
    )

    state_cache.invalidate("states")  # 默认 rewarm=None → 走 _AUTO_REWARM=False

    # 给后台 thread 充分时间(若错误地启了)
    time.sleep(0.3)
    assert call_count["n"] == 0, \
        f"默认 invalidate 不应触发 rewarm,实际调了 {call_count['n']} 次"


def test_explicit_rewarm_true_triggers_background_thread(monkeypatch):
    """显式 rewarm=True 时,后台 thread 应再次调 _build_initial_states。"""
    call_count = {"n": 0}
    call_event = threading.Event()

    def _fake_build():
        call_count["n"] += 1
        call_event.set()
        return ["fake_state"]

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_config._build_initial_states",
        _fake_build,
    )

    state_cache.invalidate("states", rewarm=True)

    assert call_event.wait(timeout=2.0), "rewarm=True 时后台 thread 应跑"
    assert call_count["n"] == 1


def test_explicit_rewarm_all_runs_all_four(monkeypatch):
    """显式 rewarm=True 时,invalidate("all") 应并发预热 4 个 getter。"""
    counts = {"states": 0, "healths": 0, "grouped": 0, "defaults": 0}
    done_count = {"n": 0}
    done_event = threading.Event()

    def _make_fake(name, ret):
        def _f(*_a, **_kw):
            counts[name] += 1
            done_count["n"] += 1
            if done_count["n"] >= 4:
                done_event.set()
            return ret
        return _f

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_config._build_initial_states",
        _make_fake("states", []),
    )
    monkeypatch.setattr(
        "chayuan.server.config_panel.runtime_framework_panel.probe_all_frameworks",
        _make_fake("healths", {}),
    )
    monkeypatch.setattr(
        "chayuan.server.config_panel.runtime_framework_panel._capability_grouped",
        _make_fake("grouped", {}),
    )
    monkeypatch.setattr(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        _make_fake("defaults", {}),
    )

    state_cache.invalidate("all", rewarm=True)

    assert done_event.wait(timeout=3.0), \
        f"4 路 rewarm 未在 3s 内全部完成,实际 counts={counts}"
    assert all(v == 1 for v in counts.values()), f"counts={counts}"


# ---------------------------------------------------------------------------
# rewarm=False — 显式关闭(等价于默认行为,但保持 API 表达力)
# ---------------------------------------------------------------------------


def test_rewarm_false_skips_background_thread(monkeypatch):
    """rewarm=False 时不应启动后台 thread。"""
    call_count = {"n": 0}

    def _fake_build():
        call_count["n"] += 1
        return ["x"]

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_config._build_initial_states",
        _fake_build,
    )

    state_cache.invalidate("states", rewarm=False)

    time.sleep(0.3)
    assert call_count["n"] == 0


# ---------------------------------------------------------------------------
# rewarm=True 失败不影响 invalidate 本身
# ---------------------------------------------------------------------------


def test_rewarm_thread_exception_swallowed(monkeypatch):
    """rewarm=True 后台 thread 抛异常时:失效仍然成功,不向调用者外抛。"""
    def _broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "chayuan.server.config_panel.model_config._build_initial_states",
        _broken,
    )

    # 不应抛
    state_cache.invalidate("states", rewarm=True)

    # 等线程跑完
    time.sleep(0.3)

    # cache 已被失效;关键是 invalidate 调用本身没向上抛
