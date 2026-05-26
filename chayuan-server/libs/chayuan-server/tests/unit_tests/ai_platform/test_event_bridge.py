"""``chayuan.server.ai_platform.event_bridge`` 单元测试。

被测对象：把 chayuan-server ``local_index.scan_once`` 的 ``ScanDelta`` 转发到
``chayuan_core.events`` 全局总线，让 ``/v1/models/events`` SSE 能拿到。

测试要点：
1. enable 后，``scan_once`` 的返回不变 + 事件总线收到对应 ``model.added/updated/removed``；
2. disable 后，``scan_once`` 还原到原引用；
3. 双重 enable 是幂等的（不会嵌套包装）。
"""
from __future__ import annotations

from typing import List

import pytest

from chayuan_core.events import (
    TOPIC_MODEL_ADDED,
    TOPIC_MODEL_REMOVED,
    TOPIC_MODEL_UPDATED,
    Event,
    get_bus,
)


@pytest.fixture(autouse=True)
def _isolate_event_bridge_and_bus():
    """每个测试用例：彻底解开桥 + 重置全局事件总线。"""
    from chayuan.server.ai_platform import event_bridge as eb_mod
    from chayuan.server.model_registry import local_index as li_mod
    import chayuan_core.events as ev_mod

    # 反向：先把任何残留替身还原
    eb_mod.disable_event_bridge()
    # 重置总线 singleton
    ev_mod._GLOBAL_BUS = None
    yield
    eb_mod.disable_event_bridge()
    ev_mod._GLOBAL_BUS = None


class _StubDelta:
    """模拟 ScanDelta：只暴露 added/updated/removed 列表。"""
    def __init__(self, added: List, updated: List, removed: List[str]) -> None:
        self.added = added
        self.updated = updated
        self.removed = removed


def _entry(mid: str, capability: str = "chat"):
    from chayuan.server.model_registry.local_index import LocalModelEntry
    return LocalModelEntry(
        model_id=mid,
        path=f"/tmp/{mid}",
        relpath=mid,
        capability=capability,
        family="",
        format="gguf",
        size_bytes=1,
        mtime=1.0,
        confidence=0.9,
        evidence=[],
        meta={},
        source_tag="models",
    )


def test_enable_event_bridge_publishes_added_updated_removed(monkeypatch):
    from chayuan.server.ai_platform import event_bridge as eb_mod
    from chayuan.server.model_registry import local_index as li_mod

    captured_delta = _StubDelta(
        added=[_entry("a/chat")],
        updated=[_entry("b/chat")],
        removed=["c/chat"],
    )
    monkeypatch.setattr(li_mod, "scan_once", lambda *a, **kw: captured_delta)

    assert eb_mod.enable_event_bridge() is True
    received: List[Event] = []
    get_bus().subscribe(received.append, topics=[
        TOPIC_MODEL_ADDED, TOPIC_MODEL_UPDATED, TOPIC_MODEL_REMOVED,
    ])

    out = li_mod.scan_once()
    # 返回值原样透出，调用方代码不受影响
    assert out is captured_delta

    topics = [e.topic for e in received]
    assert topics.count(TOPIC_MODEL_ADDED)   == 1
    assert topics.count(TOPIC_MODEL_UPDATED) == 1
    assert topics.count(TOPIC_MODEL_REMOVED) == 1

    added_ev = next(e for e in received if e.topic == TOPIC_MODEL_ADDED)
    assert added_ev.payload["id"] == "a/chat"
    removed_ev = next(e for e in received if e.topic == TOPIC_MODEL_REMOVED)
    assert removed_ev.payload["id"] == "c/chat"


def test_disable_event_bridge_restores_original(monkeypatch):
    from chayuan.server.ai_platform import event_bridge as eb_mod
    from chayuan.server.model_registry import local_index as li_mod

    sentinel_delta = _StubDelta([], [], [])
    original = lambda *a, **kw: sentinel_delta
    monkeypatch.setattr(li_mod, "scan_once", original)

    eb_mod.enable_event_bridge()
    assert li_mod.scan_once is not original   # 已被替身包了

    assert eb_mod.disable_event_bridge() is True
    assert li_mod.scan_once is original        # 还原


def test_enable_event_bridge_idempotent(monkeypatch):
    from chayuan.server.ai_platform import event_bridge as eb_mod
    from chayuan.server.model_registry import local_index as li_mod

    monkeypatch.setattr(li_mod, "scan_once",
                        lambda *a, **kw: _StubDelta([], [], []))

    assert eb_mod.enable_event_bridge() is True
    first_wrapper = li_mod.scan_once
    assert eb_mod.enable_event_bridge() is False  # 已装过
    assert li_mod.scan_once is first_wrapper       # 没有"二度包装"


def test_publish_delta_handles_empty_delta(monkeypatch):
    """空 delta 不应触发任何事件，也不该抛异常。"""
    from chayuan.server.ai_platform import event_bridge as eb_mod
    from chayuan.server.model_registry import local_index as li_mod

    monkeypatch.setattr(li_mod, "scan_once",
                        lambda *a, **kw: _StubDelta([], [], []))

    eb_mod.enable_event_bridge()
    received: List[Event] = []
    get_bus().subscribe(received.append)

    li_mod.scan_once()
    assert received == []
