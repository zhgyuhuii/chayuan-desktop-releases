"""resolve_whisper_args 分支测试。"""
from __future__ import annotations

import pytest

from chayuan.server.model_registry import process_args


def _fake_entry(model_id, fmt, path):
    return type("Entry", (), {
        "model_id": model_id,
        "format": fmt,
        "path": path,
        "capability": "asr",
    })()


def test_resolve_whisper_args_asr_default(monkeypatch):
    """capability=asr(默认)走 asr default,args 含 --model + ggml 路径。"""
    e = _fake_entry("whisper-tiny", "ggml", "/tmp/ggml-tiny.bin")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_whisper_args()
    assert r.process == "whispercpp"
    assert "--model" in r.args
    assert "/tmp/ggml-tiny.bin" in r.args
    assert r.resolved_models["asr"] == "whisper-tiny"


def test_resolve_whisper_args_unknown_capability_raises():
    """非 asr 的 capability 抛 ValueError。"""
    with pytest.raises(ValueError, match="capability"):
        process_args.resolve_whisper_args(capability="chat")  # type: ignore[arg-type]


def test_resolve_whisper_args_missing_model_reports_asr(monkeypatch):
    """模型未解到时 missing 列表里是 'asr'(不是硬编码 chat)。"""
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (None, "no asr candidate"))
    r = process_args.resolve_whisper_args()
    assert "asr" in r.missing
    assert r.reason == "no asr candidate"


def test_resolve_whisper_args_non_ggml_rejected(monkeypatch):
    """模型 format 不是 ggml 时拒绝。"""
    e = _fake_entry("whisper-tiny", "safetensors", "/tmp/something.safetensors")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_whisper_args()
    assert "asr" in r.missing
    assert "ggml" in r.reason
