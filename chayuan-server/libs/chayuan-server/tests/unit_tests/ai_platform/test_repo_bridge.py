"""``chayuan.server.ai_platform.repo_bridge`` 单元测试。

被测对象：把 chayuan-server ``local_index`` 适配为 chayuan_gateway
``ModelRepository``-鸭子类型的桥。

测试要点：
1. capability → category 映射全部 9 类对得上；
2. runtime 选择规则：family 优先 → format 推断 → 默认表；
3. ``LocalIndexRepository.list / get / get_by_path`` 正确 + 过滤；
4. ``set_enabled / hard_remove / set_default`` 改变 list 的 ``enabled`` /
   ``is_default`` 但不动磁盘；
5. ``to_public()`` 返回 OpenAI 协议字段。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from chayuan.server.ai_platform.repo_bridge import (
    LocalIndexRepository,
    LocalModel,
    _entry_to_model,
    _runtime_from,
    local_repo_factory,
)
from chayuan.server.model_registry.local_index import LocalModelEntry, LocalModelIndex


def _entry(mid: str, capability: str, **kw) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=mid,
        path=kw.get("path", f"/tmp/{mid}"),
        relpath=kw.get("relpath", mid),
        capability=capability,
        family=kw.get("family", ""),
        format=kw.get("format", "gguf"),
        size_bytes=int(kw.get("size_bytes", 1024)),
        mtime=float(kw.get("mtime", 1_700_000_000)),
        confidence=float(kw.get("confidence", 0.9)),
        evidence=list(kw.get("evidence", [])),
        meta=dict(kw.get("meta", {})),
        source_tag=kw.get("source_tag", "models"),
    )


def _seed_index(tmp_path: Path, entries: List[LocalModelEntry]) -> None:
    """把 entries 写入一个临时 LocalModelIndex 并替换全局 singleton。

    由于 ``get_local_index()`` 会检查 singleton.path == ``local_index_path()``，
    我们把 index 文件写到 ``CHAYUAN_ROOT/model_registry/local_models.json`` 的
    "正式位置"，让 LocalIndexRepository 在 __init__ 时不被 reset。
    """
    import os
    os.environ["CHAYUAN_ROOT"] = str(tmp_path)
    # chayuan.settings 里的常量是 import 期取到的，需要直接改
    import chayuan.settings as st
    setattr(st, "CHAYUAN_ROOT", str(tmp_path))
    p = tmp_path / "model_registry" / "local_models.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    idx = LocalModelIndex(path=p)
    idx.replace_all(entries)
    import chayuan.server.model_registry.local_index as li_mod
    li_mod._SINGLETON = idx


# ---------------------------------------------------------------------------
# 1) capability → category 映射 + runtime 推断
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("capability", "expected_category"), [
    ("chat",            "chat"),
    ("text-embedding",  "embedding"),
    ("image-embedding", "clip"),
    ("rerank",          "rerank"),
    ("text-to-image",   "t2i"),
    ("text-to-video",   "t2v"),
    ("text-to-speech",  "tts"),
    ("text-to-audio",   "tts"),
    ("asr",             "asr"),
    ("image-to-text",   "ocr"),
    ("ocr",             "ocr"),
])
def test_capability_to_category(capability: str, expected_category: str):
    e = _entry("acme/foo", capability=capability)
    m = _entry_to_model(e)
    assert m.category == expected_category, f"{capability} → {m.category}"


@pytest.mark.parametrize(("category", "fmt", "family", "expected"), [
    # 1) family 关键词命中
    ("chat",  "safetensors", "vLLM",       "vllm"),
    ("chat",  "gguf",        "ollama",     "ollama"),
    ("chat",  "gguf",        "llama.cpp",  "llamacpp"),
    ("ocr",   "onnx",        "rapidocr",   "rapidocr"),
    ("tts",   "onnx",        "piper",      "piper"),
    ("tts",   "onnx",        "cosyvoice",  "cosyvoice"),
    ("asr",   "bin",         "whisper-cpp", "whispercpp"),
    # 2) family 空：format 推断
    ("chat",  "gguf",        "",           "llamacpp"),
    # 3) 都空：默认表
    ("embedding", "safetensors", "",       "infinity"),
    ("clip",      "safetensors", "",       "infinity"),
    ("t2i",       "diffusers",   "",       "comfyui"),
    ("t2v",       "diffusers",   "",       "comfyui"),
])
def test_runtime_from(category: str, fmt: str, family: str, expected: str):
    assert _runtime_from(category, fmt, family) == expected


# ---------------------------------------------------------------------------
# 2) LocalIndexRepository CRUD
# ---------------------------------------------------------------------------


def test_repo_list_and_get(tmp_path: Path):
    _seed_index(tmp_path, [
        _entry("hf/qwen3-4b",    "chat",          family="ollama"),
        _entry("bge/small-zh",   "text-embedding"),
        _entry("rapidocr/v4",    "ocr",           family="rapidocr"),
    ])
    repo = LocalIndexRepository()

    listed = repo.list()
    assert {m.repo for m in listed} == {"hf/qwen3-4b", "bge/small-zh", "rapidocr/v4"}

    chats = repo.list(category="chat")
    assert [m.repo for m in chats] == ["hf/qwen3-4b"]
    assert chats[0].runtime == "ollama"

    one = repo.get("bge/small-zh")
    assert one is not None and one.category == "embedding"
    assert one.runtime == "infinity"  # 默认嵌入引擎
    assert one.public_id == "bge/small-zh"

    by_path = repo.get_by_path("/tmp/rapidocr/v4")
    assert by_path is not None and by_path.category == "ocr"

    assert repo.get("does/not-exist") is None


def test_repo_set_enabled_filters_list(tmp_path: Path):
    _seed_index(tmp_path, [
        _entry("a/chat", "chat"),
        _entry("b/chat", "chat"),
    ])
    repo = LocalIndexRepository()

    assert repo.set_enabled("a/chat", False) is True
    assert repo.set_enabled("ghost", False) is False

    enabled = repo.list(enabled=True)
    disabled = repo.list(enabled=False)
    assert [m.repo for m in enabled] == ["b/chat"]
    assert [m.repo for m in disabled] == ["a/chat"]

    # 也反映在 get() 上
    a = repo.get("a/chat")
    assert a is not None and a.enabled is False


def test_repo_set_default_marks_one_per_category(tmp_path: Path):
    _seed_index(tmp_path, [
        _entry("a/chat", "chat"),
        _entry("b/chat", "chat"),
    ])
    repo = LocalIndexRepository()
    assert repo.set_default("chat", "a/chat") is True
    assert repo.set_default("chat", "ghost") is False
    # 改变 category 不接受
    assert repo.set_default("ocr", "a/chat") is False

    listing = {m.repo: m for m in repo.list(category="chat")}
    assert listing["a/chat"].is_default is True
    assert listing["b/chat"].is_default is False


def test_repo_hard_remove_marks_disabled(tmp_path: Path):
    _seed_index(tmp_path, [
        _entry("a/chat", "chat"),
    ])
    repo = LocalIndexRepository()
    assert repo.hard_remove("a/chat") is True
    a = repo.get("a/chat")
    assert a is not None and a.enabled is False


# ---------------------------------------------------------------------------
# 3) to_public()  ↔ OpenAI 协议字段
# ---------------------------------------------------------------------------


def test_to_public_has_openai_fields(tmp_path: Path):
    _seed_index(tmp_path, [_entry("hf/qwen", "chat", format="gguf")])
    m = LocalIndexRepository().get("hf/qwen")
    assert m is not None
    pub = m.to_public()
    assert pub["id"] == "hf/qwen"
    assert pub["object"] == "model"
    assert pub["owned_by"] == "chayuan"
    assert pub["category"] == "chat"
    assert pub["capability"] == "chat"
    assert pub["enabled"] is True
    assert pub["size_bytes"] == 1024
    assert "runtime" in pub and "format" in pub
    # OpenAI 必填的 created
    assert isinstance(pub["created"], int)


# ---------------------------------------------------------------------------
# 4) FastAPI dependency factory
# ---------------------------------------------------------------------------


def test_local_repo_factory_yields_one(tmp_path: Path):
    _seed_index(tmp_path, [_entry("a/chat", "chat")])
    gen = local_repo_factory()
    repo = next(gen)
    assert isinstance(repo, LocalIndexRepository)
    # generator 应该只产一份
    with pytest.raises(StopIteration):
        next(gen)


def test_local_model_dataclass_basics():
    m = LocalModel(repo="x/y", name="y", category="chat")
    assert m.public_id == "x/y"
    assert m.enabled is True
    assert m.is_default is False
