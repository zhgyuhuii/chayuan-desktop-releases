"""Built-in signature rules used as the last resort before "unknown"."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signature:
    name: str
    category: str
    runtime: str
    format: str
    files: tuple[str, ...] = ()        # any of these filenames present (glob-ish, lowercase)
    file_suffix: tuple[str, ...] = ()  # any file ending with these suffixes
    config_arch: tuple[str, ...] = ()  # config.json `architectures` substrings
    pipeline_tag: tuple[str, ...] = ()


# Order matters: more specific signatures first.
BUILTIN_SIGNATURES: tuple[Signature, ...] = (
    # ---- Chat / completion --------------------------------------------------
    Signature(
        name="qwen-gguf",
        category="chat",
        runtime="ollama",
        format="gguf",
        file_suffix=(".gguf",),
    ),
    Signature(
        name="hf-causal-lm",
        category="chat",
        runtime="vllm",
        format="safetensors",
        file_suffix=(".safetensors",),
        config_arch=("ForCausalLM", "MistralFor", "QwenFor", "LlamaFor", "Yi"),
    ),
    Signature(
        name="hf-causal-lm-bin",
        category="chat",
        runtime="vllm",
        format="pytorch",
        file_suffix=("pytorch_model.bin", ".bin"),
        config_arch=("ForCausalLM",),
    ),

    # ---- Embedding ---------------------------------------------------------
    Signature(
        name="bge-embedding",
        category="embedding",
        runtime="infinity",
        format="safetensors",
        file_suffix=(".safetensors", ".bin"),
        pipeline_tag=("feature-extraction", "sentence-similarity"),
        config_arch=("BertModel", "XLMRoberta", "BGE"),
    ),

    # ---- Reranker ----------------------------------------------------------
    Signature(
        name="bge-reranker",
        category="rerank",
        runtime="infinity",
        format="safetensors",
        file_suffix=(".safetensors", ".bin"),
        pipeline_tag=("text-classification",),
    ),

    # ---- CLIP / image embedding -------------------------------------------
    Signature(
        name="clip-vit",
        category="clip",
        runtime="infinity",
        format="safetensors",
        file_suffix=(".safetensors", ".bin"),
        config_arch=("CLIPModel", "ChineseCLIP"),
    ),

    # ---- T2I / T2V (ComfyUI workflow models) ------------------------------
    Signature(
        name="diffusers-t2i",
        category="t2i",
        runtime="comfyui",
        format="diffusers",
        files=("model_index.json",),
    ),
    Signature(
        name="ckpt-t2i",
        category="t2i",
        runtime="comfyui",
        format="checkpoint",
        file_suffix=(".ckpt", ".safetensors"),
    ),

    # ---- TTS ---------------------------------------------------------------
    Signature(
        name="piper-onnx",
        category="tts",
        runtime="piper",
        format="onnx",
        file_suffix=(".onnx",),
    ),
    Signature(
        name="cosyvoice",
        category="tts",
        runtime="cosyvoice",
        format="pytorch",
        files=("cosyvoice.yaml", "cosyvoice2.yaml"),
    ),

    # ---- ASR ---------------------------------------------------------------
    Signature(
        name="whisper-cpp-gguf",
        category="asr",
        runtime="whispercpp",
        format="gguf",
        file_suffix=(".gguf",),
    ),
    Signature(
        name="whisper-cpp-bin",
        category="asr",
        runtime="whispercpp",
        format="ggml",
        file_suffix=(".bin",),
    ),
    Signature(
        name="funasr-paraformer",
        category="asr",
        runtime="funasr",
        format="onnx",
        files=("config.yaml", "model.onnx"),
    ),

    # ---- OCR ---------------------------------------------------------------
    Signature(
        name="paddleocr",
        category="ocr",
        runtime="paddleocr",
        format="paddle",
        file_suffix=(".pdiparams", ".pdmodel"),
    ),
    Signature(
        name="rapidocr",
        category="ocr",
        runtime="rapidocr",
        format="onnx",
        file_suffix=(".onnx",),
    ),
)
