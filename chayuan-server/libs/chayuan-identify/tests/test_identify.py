from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan_identify import identify


def _mk(tmp_path: Path, name: str, files: dict[str, str | bytes]) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        p = d / k
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(v, bytes):
            p.write_bytes(v)
        else:
            p.write_text(v, encoding="utf-8")
    return d


def test_gguf_chat(tmp_path):
    d = _mk(tmp_path / "chat", "Qwen--Qwen2.5-3B-Instruct-GGUF", {
        "qwen2.5-3b-instruct-q4_k_m.gguf": b"GGUF",
        "README.md": "Qwen 2.5 3B chat",
    })
    m = identify(d, models_root=tmp_path)
    assert m is not None
    assert m.category == "chat" and m.format == "gguf"
    assert m.quantization == "Q4_K_M"
    assert m.repo == "Qwen/Qwen2.5-3B-Instruct-GGUF"


def test_safetensors_causal_lm(tmp_path):
    d = _mk(tmp_path / "chat", "meta-llama--Llama-3-8B", {
        "config.json": json.dumps({"architectures": ["LlamaForCausalLM"]}),
        "model.safetensors": b"st",
    })
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "chat" and m.runtime == "vllm"


def test_bge_embedding(tmp_path):
    d = _mk(tmp_path / "embedding", "BAAI--bge-m3", {
        "config.json": json.dumps({"architectures": ["BertModel"]}),
        "pytorch_model.bin": b"bin",
        "README.md": "pipeline_tag: feature-extraction",
    })
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "embedding"


def test_diffusers_t2i(tmp_path):
    d = _mk(tmp_path / "t2i", "stabilityai--sdxl", {
        "model_index.json": json.dumps({"_class_name": "StableDiffusionXLPipeline"}),
        "unet/diffusion_pytorch_model.safetensors": b"x",
    })
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "t2i" and m.runtime == "comfyui"


def test_piper_tts(tmp_path):
    d = _mk(tmp_path / "tts", "rhasspy--piper-en", {"voice.onnx": b"onnx", "voice.onnx.json": "{}"})
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "tts" and m.runtime == "piper"


def test_whisper_cpp_asr(tmp_path):
    d = _mk(tmp_path / "asr", "ggerganov--whisper-base", {"ggml-base.bin": b"x"})
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "asr" and m.runtime == "whispercpp"


def test_paddleocr(tmp_path):
    d = _mk(tmp_path / "ocr", "paddle--ocr", {"inference.pdiparams": b"x", "inference.pdmodel": b"y"})
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "ocr"


def test_path_fallback(tmp_path):
    d = _mk(tmp_path / "rerank", "user--my-model", {"weights.zzz": b"unknown"})
    m = identify(d, models_root=tmp_path)
    assert m is not None and m.category == "rerank" and m.matched_rule == "path-fallback"


def test_no_match(tmp_path):
    d = _mk(tmp_path / "garbage", "junk", {"hello.txt": "world"})
    m = identify(d, models_root=tmp_path)
    assert m is None
