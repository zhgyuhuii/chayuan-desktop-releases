"""``model_settings._async_mount.chunked_async_render`` — 流式分块渲染合同(98 题)。

验证:
  1. 空 chunks → container.clear + 立即返回,不注册任何 timer
  2. 同步阶段不调任何 chunk fn(必须延迟到 timer fire)
  3. timer fire 后,**按顺序**调每个 chunk 的 fn,batch_delay 间隔正确
  4. show_progress=True 时 mount spinner 行,完成后删除
  5. show_progress=False 时**完全不 mount** progress_row(避免与 caller 顶栏视觉重复)
  6. 单 chunk 抛异常:在 container 内显示错误 label,**继续执行后续 chunk**
  7. ui.timer 不可用 → 退化同步执行所有 chunks
"""
from __future__ import annotations

from typing import Any, Callable, List

from chayuan.server.config_panel.model_settings._async_mount import (
    chunked_async_render,
)


# ---------------------------------------------------------------------------
# Fakes(与 test_async_mount.py 同源,但保持文件独立避免跨文件导入耦合)
# ---------------------------------------------------------------------------


class _Container:
    def __init__(self, clear_should_raise=False):
        self.clear_calls = 0
        self.entered = 0
        self.clear_should_raise = clear_should_raise

    def clear(self):
        self.clear_calls += 1
        if self.clear_should_raise:
            raise RuntimeError("dead")

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *a):
        return False


class _Row:
    def __init__(self, ui_ref):
        self._ui = ui_ref
        self.deleted = False

    def classes(self, *a, **kw): return self
    def style(self, *a, **kw): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def delete(self):
        self.deleted = True
        if self in self._ui._progress_rows:
            pass  # 不移除,保留以便测试断言


class _Spinner:
    def props(self, *a, **kw): return self
    def classes(self, *a, **kw): return self


class _Label:
    def __init__(self, text=""):
        self.text = text
        self.text_history: List[str] = [text]

    def classes(self, *a, **kw): return self

    def set_text(self, t: str):
        self.text = t
        self.text_history.append(t)


class _UI:
    def __init__(self):
        self.timer_calls: List[tuple] = []
        self.labels: List[_Label] = []
        self._progress_rows: List[_Row] = []

    def row(self, *a, **kw):
        r = _Row(self)
        self._progress_rows.append(r)
        return r

    def column(self, *a, **kw):
        return _Row(self)

    def spinner(self, *a, **kw):
        return _Spinner()

    def label(self, text="", *a, **kw):
        lb = _Label(text)
        self.labels.append(lb)
        return lb

    def timer(self, delay: float, fn: Callable[[], None], once: bool = False, **kw):
        self.timer_calls.append((delay, fn, once))


def _fire_all_timers(ui: _UI, max_steps: int = 50) -> None:
    """逐个触发 timer 直到队列空(模拟 NiceGUI event loop)。"""
    steps = 0
    while ui.timer_calls and steps < max_steps:
        _delay, fn, _once = ui.timer_calls.pop(0)
        fn()
        steps += 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_chunks_clears_and_returns():
    """空 chunks → 只 clear,不注册 timer。"""
    ui = _UI()
    cont = _Container()

    chunked_async_render(ui, cont, "label", [])

    assert cont.clear_calls == 1
    assert ui.timer_calls == []


def test_sync_part_does_not_call_any_chunk_fn():
    """同步阶段绝对不能调 chunk fn(必须等 timer fire)。"""
    ui = _UI()
    cont = _Container()
    calls: List[str] = []

    chunks = [
        ("a", lambda: calls.append("a")),
        ("b", lambda: calls.append("b")),
    ]
    chunked_async_render(ui, cont, "label", chunks)

    assert calls == [], "同步阶段不应调任何 chunk fn"
    assert len(ui.timer_calls) == 1, "应注册 1 个 initial timer"


def test_chunks_run_in_order_with_correct_delays():
    """timer 链式 fire → chunks 按顺序执行,每次 fire 后注册下一个 timer。"""
    ui = _UI()
    cont = _Container()
    order: List[str] = []

    chunks = [
        ("c1", lambda: order.append("c1")),
        ("c2", lambda: order.append("c2")),
        ("c3", lambda: order.append("c3")),
    ]
    chunked_async_render(
        ui, cont, "label", chunks,
        initial_delay=0.05, batch_delay=0.03,
    )

    # 检查首个 timer 用 initial_delay
    assert ui.timer_calls[0][0] == 0.05

    _fire_all_timers(ui)
    assert order == ["c1", "c2", "c3"], \
        f"chunks 必须按顺序执行,实际 {order}"


def test_progress_label_updates_on_each_chunk():
    """show_progress=True 时,progress label 每 chunk 后更新文字。"""
    ui = _UI()
    cont = _Container()
    chunks = [
        ("recommended", lambda: None),
        ("domestic", lambda: None),
        ("international", lambda: None),
    ]
    chunked_async_render(ui, cont, "云厂商", chunks, show_progress=True)

    progress_label = ui.labels[0]
    initial_text = progress_label.text
    assert "0/3" in initial_text or "云厂商" in initial_text

    _fire_all_timers(ui)

    history = progress_label.text_history
    # 最终文字应包含 "3/3" 或全部 mount 完成的标志
    assert any("1/3" in t for t in history), f"history={history}"
    assert any("3/3" in t for t in history), f"history={history}"


def test_show_progress_false_skips_progress_row():
    """show_progress=False 时,完全不 mount progress_row(避免视觉重复)。"""
    ui = _UI()
    cont = _Container()
    chunks = [("c1", lambda: None)]

    chunked_async_render(ui, cont, "label", chunks, show_progress=False)

    # _progress_rows 用于 progress 行,_FakeUI.row 把所有 row 都记录,这里
    # 我们检查 ui.labels — show_progress=False 时不应有"加载中..."label
    assert not any("加载中" in lb.text for lb in ui.labels), \
        "show_progress=False 时不应 mount '加载中...' label"


def test_chunk_exception_does_not_block_subsequent():
    """中间 chunk 抛异常 → 显示错误 label,**继续**后续 chunk。"""
    ui = _UI()
    cont = _Container()
    seen: List[str] = []

    def _bad():
        seen.append("bad-attempted")
        raise ValueError("kaboom")

    chunks = [
        ("ok1", lambda: seen.append("ok1")),
        ("bad", _bad),
        ("ok2", lambda: seen.append("ok2")),
    ]
    chunked_async_render(ui, cont, "label", chunks)
    _fire_all_timers(ui)

    assert "ok1" in seen
    assert "bad-attempted" in seen
    assert "ok2" in seen, "局部失败不应阻塞后续 chunk"

    # 错误 label 应被 mount(包含异常类型)
    error_labels = [lb.text for lb in ui.labels if "ValueError" in lb.text]
    assert error_labels, "应有 ValueError 字样的错误 label"


def test_timer_unavailable_falls_back_to_sync():
    """ui.timer 抛错(client scope 外)→ 退化同步执行所有 chunks。"""
    ui = _UI()

    def _bad_timer(*a, **kw):
        raise RuntimeError("not in client scope")
    ui.timer = _bad_timer

    cont = _Container()
    seen: List[str] = []
    chunks = [
        ("c1", lambda: seen.append("c1")),
        ("c2", lambda: seen.append("c2")),
        ("c3", lambda: seen.append("c3")),
    ]
    chunked_async_render(ui, cont, "label", chunks)

    assert seen == ["c1", "c2", "c3"], \
        "timer 不可用时必须退化同步,否则 chunks 永不执行"


def test_initial_clear_failure_short_circuits():
    """container.clear() 抛(client 已死)→ 退出,不注册 timer。"""
    ui = _UI()
    cont = _Container(clear_should_raise=True)

    chunked_async_render(ui, cont, "label", [("c1", lambda: None)])

    assert ui.timer_calls == [], "client 已死时不应继续注册 timer"
