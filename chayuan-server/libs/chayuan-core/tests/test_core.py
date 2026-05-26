"""Smoke tests for chayuan_core."""
from __future__ import annotations

import os
import time

import pytest

from chayuan_core import (
    AppConfig,
    EventBus,
    ensure_dirs,
    get_bus,
    get_paths,
    get_platform_info,
    load_config,
)
from chayuan_core.events import TOPIC_MODEL_ADDED


def test_paths_creates_all(tmp_path, monkeypatch):
    p = ensure_dirs(tmp_path)
    for d in p.all():
        assert d.is_dir()
    for cat in ("chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr"):
        assert (p.models / cat).is_dir()


def test_platform_info_has_basics():
    info = get_platform_info()
    assert info.os in ("windows", "macos", "linux")
    assert info.cpu_count > 0
    assert info.memory_total_mb > 0


def test_event_bus_sync_dispatch():
    bus = EventBus()
    received: list = []
    bus.subscribe(lambda e: received.append(e), topics=[TOPIC_MODEL_ADDED])
    bus.publish(TOPIC_MODEL_ADDED, {"id": "x"})
    bus.publish("other.topic", {})
    assert len(received) == 1
    assert received[0].topic == TOPIC_MODEL_ADDED
    assert received[0].payload == {"id": "x"}


def test_event_bus_history():
    bus = EventBus()
    e1 = bus.publish("a", {"i": 1})
    bus.publish("a", {"i": 2})
    bus.publish("b", {"i": 3})
    assert len(bus.history()) == 3
    assert len(bus.history(since_id=e1.id)) == 2
    assert len(bus.history(topic="b")) == 1


def test_global_bus_is_singleton():
    assert get_bus() is get_bus()


def test_load_config_defaults_and_env(monkeypatch):
    monkeypatch.setenv("CHAYUAN_GATEWAY_PORT", "39999")
    monkeypatch.setenv("CHAYUAN_DISCOVERY_POLL_INTERVAL_SEC", "30")
    monkeypatch.setenv("CHAYUAN_LOG_LEVEL", "DEBUG")
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.gateway.port == 39999
    assert cfg.discovery.poll_interval_sec == 30
    assert cfg.log_level == "DEBUG"
    assert cfg.registry.url.startswith("sqlite:///")
    assert cfg.supervisor.spec_path.endswith("supervisor.yaml")
