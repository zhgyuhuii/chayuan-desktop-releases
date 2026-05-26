"""framework_wiring.py 单元测试.

策略:
* 不真调 ollama / 真创建 modelfile;只在 tmp_path 下验证文件落位
* HF cache / ComfyUI / llamacpp / whispercpp 等"软链/拷贝"型 wire 都能在
  纯文件系统层验证
* Ollama wire 必须对"无 GGUF"和"无二进制"两种情况都给出明确 ok=False
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from chayuan_runtime.framework_wiring import (
    WireOutcome,
    wire,
)


# ---------- 通用 fixture ----------

@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Qwen__Qwen2.5-0.5B-Instruct"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def chayuan_data(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "chayuan_data"
    root.mkdir(parents=True)
    monkeypatch.setenv("CHAYUAN_DATA", str(root))
    return root


# ---------- dispatcher ----------

def test_unknown_runtime_returns_failure(model_dir: Path) -> None:
    out = wire(str(model_dir), "chat", "no-such-runtime")
    assert isinstance(out, WireOutcome)
    assert out.ok is False
    assert "未知运行时" in out.detail


def test_missing_model_path_returns_failure(tmp_path: Path) -> None:
    out = wire(str(tmp_path / "doesnt-exist"), "chat", "ollama")
    assert out.ok is False
    assert "不存在" in out.detail


# ---------- HF cache / vLLM / Infinity / FunASR ----------

def test_wire_hf_cache_creates_snapshot_link(model_dir: Path, tmp_path: Path, monkeypatch) -> None:
    # 把 HF_HOME 指向 tmp 避免污染
    hf_home = tmp_path / "hf_home"
    monkeypatch.setenv("HF_HOME", str(hf_home))

    # 预置 manifest 让 _read_repo_id_from_manifest 走通
    (model_dir / "manifest.json").write_text(
        '{"repo_id": "Qwen/Qwen2.5-0.5B-Instruct"}', encoding="utf-8",
    )

    out = wire(str(model_dir), "chat", "hf-cache")
    assert out.ok is True
    assert "hf-cache" in out.runtime
    expected = hf_home / "hub" / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots" / "main"
    assert expected.exists() or expected.is_symlink()


def test_wire_vllm_uses_hf_cache_under_the_hood(model_dir: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))
    (model_dir / "manifest.json").write_text(
        '{"repo_id": "Qwen/Qwen2.5-0.5B-Instruct"}', encoding="utf-8",
    )

    out = wire(str(model_dir), "chat", "vllm")
    assert out.ok is True
    assert out.runtime == "vllm"


def test_wire_infinity_runtime_label(model_dir: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))
    (model_dir / "manifest.json").write_text(
        '{"repo_id": "BAAI/bge-m3"}', encoding="utf-8",
    )
    out = wire(str(model_dir), "text-embedding", "infinity")
    assert out.ok is True
    assert out.runtime == "infinity"


# ---------- llama.cpp ----------

def test_wire_llamacpp_no_gguf_fails(model_dir: Path, chayuan_data: Path) -> None:
    out = wire(str(model_dir), "chat", "llamacpp")
    assert out.ok is False
    assert ".gguf" in out.detail.lower() or "gguf" in out.detail.lower()


def test_wire_llamacpp_picks_largest_gguf(model_dir: Path, chayuan_data: Path) -> None:
    small = model_dir / "small.gguf"
    big = model_dir / "big.gguf"
    small.write_bytes(b"x" * 100)
    big.write_bytes(b"y" * 1000)

    out = wire(str(model_dir), "chat", "llamacpp")
    assert out.ok is True
    target = chayuan_data / "vendor-data" / "llamacpp" / "models" / "big.gguf"
    assert target.exists() or target.is_symlink()


# ---------- whisper.cpp ----------

def test_wire_whispercpp_finds_ggml_bin(model_dir: Path, chayuan_data: Path) -> None:
    (model_dir / "ggml-base.bin").write_bytes(b"z" * 200)
    out = wire(str(model_dir), "asr", "whispercpp")
    assert out.ok is True
    target = chayuan_data / "vendor-data" / "whispercpp" / "models" / "ggml-base.bin"
    assert target.exists() or target.is_symlink()


def test_wire_whispercpp_no_bin_fails(model_dir: Path, chayuan_data: Path) -> None:
    out = wire(str(model_dir), "asr", "whispercpp")
    assert out.ok is False


# ---------- ComfyUI ----------

@pytest.mark.parametrize("capability,expected_subdir", [
    ("text-to-image", "checkpoints"),
    ("t2i", "checkpoints"),
    ("text-to-video", "checkpoints"),
    ("image-embedding", "clip_vision"),
    ("clip", "clip_vision"),
])
def test_wire_comfyui_routes_capability_to_subdir(
    model_dir: Path, chayuan_data: Path, capability: str, expected_subdir: str,
) -> None:
    out = wire(str(model_dir), capability, "comfyui")
    assert out.ok is True
    assert out.extra.get("comfyui_subdir") == expected_subdir
    target = chayuan_data / "vendor-data" / "comfyui" / "models" / expected_subdir / model_dir.name
    assert target.exists() or target.is_symlink()


# ---------- Ollama ----------

def test_wire_ollama_skips_when_capability_mismatch(model_dir: Path, chayuan_data: Path) -> None:
    out = wire(str(model_dir), "ocr", "ollama")
    # 即使没有二进制, ocr 与 ollama 不匹配,直接返回 ok 跳过
    assert out.ok is True
    assert "跳过" in out.detail or "不匹配" in out.detail


def test_wire_ollama_no_binary_returns_install_hint(model_dir: Path, chayuan_data: Path, monkeypatch) -> None:
    # 强制 shutil.which 返回 None 模拟"未装 ollama"
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    out = wire(str(model_dir), "chat", "ollama")
    assert out.ok is False
    assert "ollama" in out.detail.lower()
    assert out.extra.get("installable") is True


def test_wire_ollama_no_gguf_warns_user(model_dir: Path, chayuan_data: Path, monkeypatch) -> None:
    # 模拟 ollama 已装
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/local/bin/ollama")
    # 但目录下没有 GGUF
    out = wire(str(model_dir), "chat", "ollama")
    assert out.ok is False
    assert ".gguf" in out.detail.lower() or "gguf" in out.detail.lower()
