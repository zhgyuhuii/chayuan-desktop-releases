"""``process_args`` 解析层测试 —— 纯逻辑，不真启动进程。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)
from chayuan.server.model_registry.process_args import (
    Resolution,
    resolve_all,
    resolve_infinity_args,
    resolve_llamacpp_args,
    resolve_ollama_env,
)


def _entry(model_id: str, capability: str, fmt: str = "gguf",
           path: str = "") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=path or f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format=fmt,
        family=capability.replace("-", "_"),
        size_bytes=1024 * 1024 * 250,
    )


def _idx_with(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-pa-test-"))
    p = td / "local_models.json"
    doc = {"version": 1, "items": [e.to_dict() for e in entries]}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return LocalModelIndex(p)


@pytest.fixture
def patch_index_and_defaults():
    """同时 patch local_index + capability defaults loader。

    返回 ``(set_idx, set_defaults)`` 两个 setter,测试用例直接调即可。
    """
    idx_holder = {"idx": _idx_with([])}
    defaults_holder = {"defaults": {}}

    def _get_idx():
        return idx_holder["idx"]

    def _load_defaults():
        return dict(defaults_holder["defaults"])

    with mock.patch(
        "chayuan.server.model_registry.process_args.get_local_index",
        side_effect=_get_idx,
    ), mock.patch(
        "chayuan.server.model_registry.process_args._load_defaults",
        side_effect=_load_defaults,
    ):
        def set_idx(entries: list[LocalModelEntry]) -> None:
            idx_holder["idx"] = _idx_with(entries)

        def set_defaults(d: dict) -> None:
            defaults_holder["defaults"] = dict(d)

        yield set_idx, set_defaults


# ───────────────────────── llamacpp ─────────────────────────


def test_llamacpp_resolves_from_default(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("qwen3-4b", "chat", fmt="gguf", path="/m/qwen3-4b.gguf")])
    set_defaults({"chat": "qwen3-4b"})
    r = resolve_llamacpp_args()
    assert r.ok
    assert "--model" in r.args
    assert "/m/qwen3-4b.gguf" in r.args
    assert "--ctx-size" in r.args
    assert r.resolved_models == {"chat": "qwen3-4b"}
    assert r.missing == []


def test_llamacpp_falls_back_when_default_missing(patch_index_and_defaults):
    """default 没设也得能挑到一个本地 GGUF。"""
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("qwen3-4b", "chat", fmt="gguf", path="/m/q.gguf")])
    set_defaults({})  # 没设 default
    r = resolve_llamacpp_args()
    assert r.ok
    assert "/m/q.gguf" in r.args
    assert "fallback" in r.reason


def test_llamacpp_rejects_non_gguf(patch_index_and_defaults):
    """default 指向非 GGUF(如 hf_transformers)时不应硬塞给 llama-server。"""
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("bge-m3", "chat", fmt="hf_transformers", path="/m/bge-m3")])
    set_defaults({"chat": "bge-m3"})
    r = resolve_llamacpp_args()
    # 缺 chat = 没找到合适的 GGUF
    assert not r.ok
    assert "chat" in r.missing


def test_llamacpp_threads_and_gpu_layers(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("qwen3-4b", "chat", fmt="gguf", path="/m/q.gguf")])
    set_defaults({"chat": "qwen3-4b"})
    r = resolve_llamacpp_args(n_threads=8, n_gpu_layers=35, n_ctx=8192)
    assert "--threads" in r.args
    assert "8" in r.args
    assert "--n-gpu-layers" in r.args
    assert "35" in r.args
    assert "8192" in r.args


def test_llamacpp_no_local_returns_missing(patch_index_and_defaults):
    """什么本地模型都没有时,返回 missing,不抛。"""
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([])
    r = resolve_llamacpp_args()
    assert not r.ok
    assert r.missing == ["chat"]
    assert r.args == []


# ───────────────────────── infinity ─────────────────────────


def test_infinity_picks_embed_and_rerank(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([
        _entry("bge-m3", "text-embedding", fmt="hf_transformers", path="/m/bge-m3"),
        _entry("bge-rerank", "rerank", fmt="hf_transformers", path="/m/rerank"),
    ])
    set_defaults({"embedding": "bge-m3", "rerank": "bge-rerank"})
    r = resolve_infinity_args()
    # 两次 --model-id
    assert r.args.count("--model-id") == 2
    assert "/m/bge-m3" in r.args
    assert "/m/rerank" in r.args
    assert r.resolved_models == {"embedding": "bge-m3", "rerank": "bge-rerank"}
    assert r.missing == []


def test_infinity_missing_one_still_returns_other(patch_index_and_defaults):
    """只有 embed 没有 rerank 时,起码 embed 参数应该被填上。"""
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("bge-m3", "text-embedding", fmt="hf_transformers", path="/m/x")])
    set_defaults({"embedding": "bge-m3"})
    r = resolve_infinity_args()
    assert "--model-id" in r.args
    assert "/m/x" in r.args
    assert "rerank" in r.missing


def test_infinity_empty_when_nothing_resolves(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([])
    r = resolve_infinity_args()
    assert r.args == []
    assert set(r.missing) == {"embedding", "rerank"}


# ───────────────────────── ollama ─────────────────────────


def test_ollama_explicit_dir():
    r = resolve_ollama_env(models_dir="/opt/ollama/models")
    assert r.env["OLLAMA_MODELS"] == "/opt/ollama/models"
    assert r.missing == []


def test_ollama_derives_from_local_index():
    """不传 models_dir 时,从 local_index.models_dir() 推导。"""
    with mock.patch(
        "chayuan.server.model_registry.local_index.models_dir",
        return_value=Path("/srv/chayuan/models"),
    ):
        r = resolve_ollama_env()
    assert r.env.get("OLLAMA_MODELS", "").endswith("chat/_ollama")
    # 应该是 /srv/chayuan/models/chat/_ollama 风格
    assert "/srv/chayuan/models" in r.env["OLLAMA_MODELS"]


def test_ollama_handles_derive_failure_gracefully():
    """models_dir 函数抛异常时不应炸,返回 missing。"""
    with mock.patch(
        "chayuan.server.model_registry.local_index.models_dir",
        side_effect=RuntimeError("no root"),
    ):
        r = resolve_ollama_env()
    assert r.env == {}
    assert "ollama_models_dir" in r.missing


# ───────────────────────── 集成 ─────────────────────────


def test_resolve_all_returns_three_processes(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([])
    set_defaults({})
    out = resolve_all()
    assert set(out.keys()) == {"llamacpp", "infinity", "ollama"}
    # 都应该是 Resolution 实例
    for r in out.values():
        assert isinstance(r, Resolution)


def test_resolution_to_dict_is_json_safe(patch_index_and_defaults):
    set_idx, set_defaults = patch_index_and_defaults
    set_idx([_entry("qwen3-4b", "chat", fmt="gguf", path="/m/q.gguf")])
    set_defaults({"chat": "qwen3-4b"})
    r = resolve_llamacpp_args()
    d = r.to_dict()
    s = json.dumps(d)
    assert '"process"' in s
    assert '"args"' in s
    assert '"ok"' in s
    assert d["ok"] is True
