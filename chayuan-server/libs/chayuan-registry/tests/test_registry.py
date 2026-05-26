from __future__ import annotations

import pytest

from chayuan_core.events import EventBus
from chayuan_registry.db import reset_for_tests, session_scope
from chayuan_registry.models import ModelStatus
from chayuan_registry.repository import ModelRepository


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests("sqlite:///:memory:")
    yield


def _payload(repo="Qwen/Qwen2.5-3B", category="chat", path="/m/Qwen--Qwen2.5-3B"):
    return {
        "repo": repo,
        "category": category,
        "path": path,
        "runtime": "ollama",
        "format": "gguf",
        "size_bytes": 1024,
        "sha256": "abc",
        "capabilities": {"chat": True},
    }


def test_upsert_then_update_publishes_events():
    bus = EventBus()
    received = []
    bus.subscribe(lambda e: received.append(e.topic))
    with session_scope() as s:
        repo = ModelRepository(s, bus)
        m, created = repo.upsert(_payload())
        assert created and m.id is not None
        m2, created2 = repo.upsert({**_payload(), "size_bytes": 2048})
        assert not created2 and m2.id == m.id and m2.size_bytes == 2048
    assert "model.added" in received and "model.updated" in received


def test_soft_remove_marks_status_and_emits():
    bus = EventBus()
    received = []
    bus.subscribe(lambda e: received.append(e.topic))
    with session_scope() as s:
        repo = ModelRepository(s, bus)
        repo.upsert(_payload())
        m = repo.soft_remove_by_path(_payload()["path"])
        assert m is not None and m.status == ModelStatus.REMOVED
        assert "model.removed" in received


def test_alias_lookup_and_default():
    with session_scope() as s:
        repo = ModelRepository(s)
        repo.upsert(_payload())
        assert repo.add_alias("Qwen/Qwen2.5-3B", "qwen-mini")
        m = repo.get("qwen-mini")
        assert m is not None and m.repo == "Qwen/Qwen2.5-3B"
        repo.upsert(_payload(repo="Qwen/Qwen2.5-7B", path="/m/Qwen--Qwen2.5-7B"))
        repo.set_default("chat", "Qwen/Qwen2.5-7B")
        defaults = [m for m in repo.list(category="chat") if m.is_default]
        assert len(defaults) == 1 and defaults[0].repo == "Qwen/Qwen2.5-7B"
