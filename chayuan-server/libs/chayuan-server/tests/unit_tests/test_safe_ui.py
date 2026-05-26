"""_safe_ui 防 client-deleted 警告的工具测试."""
from __future__ import annotations

import logging

import pytest

from chayuan.server.config_panel._safe_ui import (
    is_client_alive,
    safe_call,
    safe_run_javascript,
    safe_timer_cb,
)


def test_safe_timer_cb_passes_through_on_success():
    @safe_timer_cb
    def _ok():
        return 42
    assert _ok() == 42


def test_safe_timer_cb_swallows_exception(caplog):
    @safe_timer_cb
    def _fail():
        raise AttributeError("client deleted")

    with caplog.at_level(logging.DEBUG):
        result = _fail()
    assert result is None
    # 应当 debug log 一条 (而不是 propagate)
    assert any("safe_timer_cb" in r.message for r in caplog.records)


def test_safe_timer_cb_preserves_function_signature():
    @safe_timer_cb
    def _add(a, b):
        return a + b
    assert _add(1, 2) == 3


def test_safe_call_passes_through():
    assert safe_call(lambda: "ok", what="test") == "ok"


def test_safe_call_returns_none_on_exception():
    assert safe_call(lambda: 1 / 0, what="div") is None


def test_safe_run_javascript_doesnt_raise_when_ui_missing():
    """fake ui 抛 AttributeError → 静默吞掉。"""
    class _FakeUI:
        def run_javascript(self, code):
            raise AttributeError("client deleted")

    # 不应抛
    safe_run_javascript(_FakeUI(), "console.log('hi')")


def test_safe_run_javascript_calls_through_normally():
    """ui 正常时调用透传。"""
    class _RecordUI:
        def __init__(self):
            self.called = []
        def run_javascript(self, code):
            self.called.append(code)

    u = _RecordUI()
    safe_run_javascript(u, "alert(1)")
    assert u.called == ["alert(1)"]


# ---------------------------------------------------------------------------
# is_client_alive(56-2 题:NiceGUI Client.delete race 防护)
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, cid: str) -> None:
        self.id = cid


class _FakeElement:
    def __init__(self, client) -> None:
        self.client = client


def test_is_client_alive_no_nicegui(monkeypatch):
    """NiceGUI 不可用时,保守认为存活(不误杀业务流程)。"""
    import builtins
    real_import = builtins.__import__

    def _no_nicegui(name, *args, **kwargs):
        if name.startswith("nicegui"):
            raise ImportError("test: no nicegui")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_nicegui)
    elt = _FakeElement(_FakeClient("c1"))
    assert is_client_alive(elt) is True


def test_is_client_alive_element_without_client():
    """element.client 为 None 时,保守认为存活。"""
    elt = _FakeElement(None)
    assert is_client_alive(elt) is True


def test_is_client_alive_with_live_client(monkeypatch):
    """client.id 在 NiceGUI Client.instances 字典中 → 存活。"""
    import nicegui  # type: ignore[import-untyped]
    from nicegui import Client  # type: ignore[import-untyped]
    monkeypatch.setattr(Client, "instances", {"alive-id": object()}, raising=False)
    elt = _FakeElement(_FakeClient("alive-id"))
    assert is_client_alive(elt) is True


def test_is_client_alive_with_dead_client(monkeypatch):
    """client.id 不在 NiceGUI Client.instances → 已死。"""
    from nicegui import Client  # type: ignore[import-untyped]
    monkeypatch.setattr(Client, "instances", {"other-id": object()}, raising=False)
    elt = _FakeElement(_FakeClient("dead-id"))
    assert is_client_alive(elt) is False


def test_is_client_alive_client_without_id():
    """client 没 id 属性 → 保守认为存活。"""
    class _NoId:
        pass
    elt = _FakeElement(_NoId())
    assert is_client_alive(elt) is True
