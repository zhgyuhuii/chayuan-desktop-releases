from __future__ import annotations

import pytest

from chayuan_registry.db import reset_for_tests, session_scope
from chayuan_registry.repository import ModelRepository
from chayuan_runtime import (
    AdapterRequest,
    get_registry,
    pick_adapter,
)


@pytest.fixture(autouse=True)
def _registry_reset():
    reset_for_tests("sqlite:///:memory:")
    yield


def _add_model(category: str, runtime: str, fmt: str = "safetensors", name: str = "test/m") -> int:
    with session_scope() as s:
        repo = ModelRepository(s)
        m, _ = repo.upsert({
            "repo": name, "category": category, "runtime": runtime, "format": fmt, "path": "/x"
        })
        return m.id


def _get(mid: int):
    with session_scope() as s:
        return ModelRepository(s).by_id(mid)


def test_registry_has_all_builtins():
    reg = get_registry(mock=True)
    names = {a.name for a in reg.all()}
    for required in ("ollama", "infinity", "vllm", "llamacpp",
                     "whispercpp", "funasr", "piper", "cosyvoice",
                     "rapidocr", "paddleocr", "comfyui"):
        assert required in names, f"missing adapter: {required}"


def test_pick_chat_ollama():
    mid = _add_model("chat", "ollama", "gguf")
    m = _get(mid)
    a = pick_adapter(m)
    assert a is not None and a.name == "ollama"


def test_mock_chat_returns_payload():
    mid = _add_model("chat", "ollama", "gguf")
    m = _get(mid)
    a = pick_adapter(m)
    resp = a.call(AdapterRequest(op="chat", model=m, payload={"messages": [{"role": "user", "content": "hi"}]}))
    assert "choices" in resp.body and resp.body["choices"][0]["message"]["content"].startswith("[mock:ollama]")


def test_pick_embedding_infinity():
    mid = _add_model("embedding", "infinity", "safetensors")
    m = _get(mid)
    a = pick_adapter(m)
    assert a is not None and a.name == "infinity"
    resp = a.call(AdapterRequest(op="embedding", model=m, payload={"input": ["a", "b"]}))
    assert len(resp.body["data"]) == 2
