"""resolve_llamacpp_args capability 分支测试。"""
from __future__ import annotations

import pytest

from chayuan.server.model_registry import process_args


def _fake_entry(model_id, fmt, path):
    return type("Entry", (), {
        "model_id": model_id,
        "format": fmt,
        "path": path,
        "capability": "chat",  # 不重要,_resolve 通过 local_cap 查
    })()


def test_resolve_llamacpp_args_chat_default(monkeypatch):
    """capability=chat (默认) 走 chat default。args 含 --model + --ctx-size。"""
    e = _fake_entry("qwen3-4b", "gguf", "/tmp/qwen.gguf")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_llamacpp_args(n_ctx=8192)
    assert r.process == "llamacpp"
    assert "--model" in r.args
    assert "/tmp/qwen.gguf" in r.args
    assert "--ctx-size" in r.args
    assert "8192" in r.args
    # chat 不带 --embedding / --reranking
    assert "--embedding" not in r.args
    assert "--reranking" not in r.args
    assert r.resolved_models["chat"] == "qwen3-4b"


def test_resolve_llamacpp_args_embedding(monkeypatch):
    """capability=embedding 走 embedding default。args 含 --embedding --pooling cls。"""
    e = _fake_entry("bge-small", "gguf", "/tmp/bge.gguf")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_llamacpp_args(capability="embedding")
    assert "--model" in r.args
    assert "/tmp/bge.gguf" in r.args
    assert "--embedding" in r.args
    assert "--pooling" in r.args
    assert "cls" in r.args
    assert "--reranking" not in r.args
    assert r.resolved_models["embedding"] == "bge-small"


def test_resolve_llamacpp_args_rerank(monkeypatch):
    """capability=rerank 走 rerank default。args 含 --reranking。"""
    e = _fake_entry("bge-rerank", "gguf", "/tmp/rerank.gguf")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_llamacpp_args(capability="rerank")
    assert "--reranking" in r.args
    assert "--embedding" not in r.args
    assert r.resolved_models["rerank"] == "bge-rerank"


def test_resolve_llamacpp_args_unknown_capability_raises():
    with pytest.raises(ValueError, match="capability"):
        process_args.resolve_llamacpp_args(capability="asr")  # type: ignore[arg-type]


def test_resolve_llamacpp_args_missing_model_reports_capability(monkeypatch):
    """模型未解到时 missing 列表里是 capability 名,不是 'chat' 硬编码。"""
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (None, "no candidate"))
    r = process_args.resolve_llamacpp_args(capability="embedding")
    assert "embedding" in r.missing
    assert "chat" not in r.missing


def test_expand_gguf_prefers_dirname_match_over_smallest(tmp_path, monkeypatch):
    """目录里多个 .gguf 时,选文件名跟目录名公共前缀最长的那个 ——
    用户偶尔把别的模型权重误丢进某个模型文件夹(线上诊断:
    rerank/bge-reranker-v2-m3/ 里塞了 gte-...gguf)。即便误入的更小,
    也要选目录名对得上的那个,不能简单取最小。"""
    d = tmp_path / "bge-reranker-v2-m3"
    d.mkdir()
    # 这文件夹本来要装的模型(故意做大)
    (d / "bge-reranker-v2-m3-Q8_0.gguf").write_bytes(b"x" * 6000)
    # 误丢进来的别的模型(更小 —— 旧逻辑"取最小"会错选它)
    (d / "gte-multilingual-reranker-base-Q8_0.gguf").write_bytes(b"y" * 3000)
    e = _fake_entry("bge-reranker-v2-m3", "gguf", str(d))
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_llamacpp_args(capability="rerank")
    model_arg = r.args[r.args.index("--model") + 1]
    assert model_arg.endswith("bge-reranker-v2-m3-Q8_0.gguf")
    assert "--reranking" in r.args


def test_expand_gguf_same_model_quant_variants_picks_smallest(tmp_path, monkeypatch):
    """同一模型多量化(Q4/Q8)→ 公共前缀相同 → 退回取最小(小量化更稳)。"""
    d = tmp_path / "Qwen3-4B-GGUF"
    d.mkdir()
    (d / "Qwen3-4B-Q8_0.gguf").write_bytes(b"x" * 9000)
    (d / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"y" * 4000)
    e = _fake_entry("Qwen3-4B-GGUF", "gguf", str(d))
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_llamacpp_args(capability="chat", n_ctx=4096)
    model_arg = r.args[r.args.index("--model") + 1]
    assert model_arg.endswith("Qwen3-4B-Q4_K_M.gguf")
