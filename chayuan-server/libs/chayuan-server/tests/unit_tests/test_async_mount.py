"""``model_settings._async_mount.lazy_async_render`` — 行为合同单测(98 题)。

不依赖 NiceGUI 真实 client/socket;用 Fake 替身验证关键流:
  1. **同步部分**:container.clear() + spinner mount(<10ms,几个组件)
  2. **异步部分**:ui.timer 注册了 ``once=True`` 的回调
  3. **触发回调**后:再次 clear → 调用真渲染 ``render_fn``
  4. **render_fn 抛错**:不外抛,在 container 内显示错误 label
  5. **render_fn 抛错且 container.clear() 也抛**:静默吞,不让进程崩
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

import pytest

from chayuan.server.config_panel.model_settings._async_mount import (
    lazy_async_render,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContainer:
    """模拟 NiceGUI container — 记录 clear / __enter__ / __exit__ 调用。"""

    def __init__(self, clear_should_raise: bool = False):
        self.clear_calls = 0
        self.entered = 0
        self.clear_should_raise = clear_should_raise

    def clear(self) -> None:
        self.clear_calls += 1
        if self.clear_should_raise:
            raise RuntimeError("client dead")

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        return False


class _FakeSpinner:
    def __init__(self, *a, **kw):
        pass

    def props(self, *a, **kw):
        return self

    def classes(self, *a, **kw):
        return self


class _FakeRow:
    def __init__(self):
        pass

    def classes(self, *a, **kw):
        return self

    def style(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeLabel:
    def __init__(self, text=""):
        self.text = text

    def classes(self, *a, **kw):
        return self


class _FakeTimer:
    """模拟 ui.timer — 记录注册的 callback,可手动 fire。"""

    def __init__(self, registry: List[tuple]):
        self.registry = registry

    def __call__(self, delay: float, fn: Callable[[], None],
                 once: bool = False, **kw):
        self.registry.append((delay, fn, once))
        return self


class _FakeUI:
    """最小 NiceGUI 替身。"""

    def __init__(self):
        self.timer_registry: List[tuple] = []
        self.labels: List[str] = []
        self.timer = _FakeTimer(self.timer_registry)

    def row(self, *a, **kw):
        return _FakeRow()

    def column(self, *a, **kw):
        return _FakeRow()

    def spinner(self, *a, **kw):
        return _FakeSpinner()

    def label(self, text="", *a, **kw):
        self.labels.append(str(text))
        return _FakeLabel(text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sync_part_mounts_spinner_and_registers_timer():
    """同步阶段:container.clear() + spinner mount + ui.timer 注册。"""
    ui = _FakeUI()
    cont = _FakeContainer()
    rendered_called = [False]

    def render_fn():
        rendered_called[0] = True

    lazy_async_render(ui, cont, "② 模型厂商", render_fn)

    assert cont.clear_calls == 1, "同步必须立刻 clear container"
    assert any("模型厂商" in s for s in ui.labels), \
        "spinner 旁应有 label 显示当前 tab 名"
    assert len(ui.timer_registry) == 1, "应注册 1 个 ui.timer"
    delay, fn, once = ui.timer_registry[0]
    assert once is True, "timer 必须 once=True 避免重复"
    assert 0.01 <= delay <= 0.5, f"timer delay 应在合理范围,实际 {delay}"
    assert rendered_called[0] is False, \
        "同步阶段绝对不能调 render_fn — 那是阻塞主帧的根因"


def test_async_part_clears_spinner_and_calls_render():
    """异步阶段(timer fire 后):清 spinner → 调 render_fn。"""
    ui = _FakeUI()
    cont = _FakeContainer()
    render_calls = [0]

    def render_fn():
        render_calls[0] += 1

    lazy_async_render(ui, cont, "label", render_fn)
    assert cont.clear_calls == 1

    # fire timer
    _, timer_fn, _ = ui.timer_registry[0]
    timer_fn()

    assert cont.clear_calls == 2, "异步阶段应再次 clear (移除 spinner)"
    assert render_calls[0] == 1, "render_fn 应被调用 1 次"


def test_render_fn_exception_does_not_propagate():
    """render_fn 抛错时:不外抛,容器内显示错误 label。"""
    ui = _FakeUI()
    cont = _FakeContainer()

    def render_fn():
        raise ValueError("boom")

    lazy_async_render(ui, cont, "② 模型厂商", render_fn)
    _, timer_fn, _ = ui.timer_registry[0]
    # 必须不外抛
    timer_fn()

    # 错误 label 应被 mount(包含异常类型 + 消息)
    error_labels = [s for s in ui.labels if "ValueError" in s or "boom" in s]
    assert error_labels, f"应有 ValueError/boom 字样的错误 label,实际 labels={ui.labels}"


def test_initial_clear_failure_short_circuits():
    """container.clear() 抛(client 已死)→ 静默退出,不注册 timer。"""
    ui = _FakeUI()
    cont = _FakeContainer(clear_should_raise=True)

    lazy_async_render(ui, cont, "label", lambda: None)

    assert len(ui.timer_registry) == 0, \
        "client 已死时不应继续注册 timer(浪费资源 + 可能 NiceGUI 警告)"


def test_async_clear_failure_does_not_call_render():
    """异步阶段 clear 失败(client 已死)→ 不调 render_fn,不抛。"""
    ui = _FakeUI()

    class _DeadOnSecondClear(_FakeContainer):
        def clear(self):
            self.clear_calls += 1
            if self.clear_calls >= 2:
                raise RuntimeError("client dead")

    cont = _DeadOnSecondClear()
    render_calls = [0]

    def render_fn():
        render_calls[0] += 1

    lazy_async_render(ui, cont, "label", render_fn)
    _, timer_fn, _ = ui.timer_registry[0]
    timer_fn()  # 不应抛

    assert render_calls[0] == 0, \
        "client 已死时不应调 render_fn(避免 NiceGUI 警告)"


def test_timer_unavailable_falls_back_to_sync():
    """ui.timer 抛错(client scope 外)→ 退化为同步执行 _real_mount。"""
    ui = _FakeUI()

    def _bad_timer(*a, **kw):
        raise RuntimeError("not in client scope")
    ui.timer = _bad_timer

    cont = _FakeContainer()
    render_calls = [0]

    def render_fn():
        render_calls[0] += 1

    lazy_async_render(ui, cont, "label", render_fn)

    assert render_calls[0] == 1, \
        "timer 不可用时必须退化同步,否则 render_fn 永不执行"
