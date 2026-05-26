from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from chayuan_core import ensure_dirs
from chayuan_discovery.poller import poll_once
from chayuan_discovery.watcher import ModelTreeWatcher
from chayuan_registry import ModelRepository, session_scope
from chayuan_registry.db import reset_for_tests


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_for_tests("sqlite:///:memory:")
    yield


def _drop_in(models_root: Path, category: str, repo: str, files: dict[str, bytes]) -> Path:
    safe = repo.replace("/", "--")
    d = models_root / category / safe
    d.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (d / k).write_bytes(v)
    return d


def test_poller_picks_up_dropin():
    paths = ensure_dirs()
    d = _drop_in(paths.models, "asr", "ggerganov/whisper-base-test", {"ggml-base.bin": b"x"})
    try:
        summary = poll_once()
        assert summary["added"] >= 1
        with session_scope() as s:
            repo = ModelRepository(s)
            assert repo.get("ggerganov/whisper-base-test") is not None
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    poll_once()
    with session_scope() as s:
        repo = ModelRepository(s)
        m = repo.get("ggerganov/whisper-base-test")
        assert m is None or m.status.value == "removed"


def test_watcher_event_loop():
    paths = ensure_dirs()
    w = ModelTreeWatcher(debounce_ms=100)
    w.start()
    try:
        d = _drop_in(paths.models, "tts", "rhasspy/piper-test-watcher", {"voice.onnx": b"x"})
        try:
            for _ in range(40):
                with session_scope() as s:
                    if ModelRepository(s).get("rhasspy/piper-test-watcher") is not None:
                        return
                time.sleep(0.2)
            pytest.fail("watcher did not register model in time")
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    finally:
        w.stop()
