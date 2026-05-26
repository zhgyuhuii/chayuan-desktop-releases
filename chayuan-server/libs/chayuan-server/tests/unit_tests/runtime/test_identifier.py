"""5 级模型识别测试 — 用 tmp_path 构造各种"假模型仓库"。"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from chayuan.server.model_registry.identifier import identify_path


def _write(p: Path, content) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        p.write_text(json.dumps(content), encoding="utf-8")
    else:
        p.write_text(str(content), encoding="utf-8")


def test_level1_hf_config_chat(tmp_path: Path):
    repo = tmp_path / "Qwen2-7B-Instruct"
    _write(repo / "config.json", {"model_type": "qwen2", "hidden_size": 3584})
    ident = identify_path(repo)
    assert ident.capability == "chat"
    assert ident.format == "hf_transformers"
    assert ident.confidence >= 0.9
    assert ident.family == "qwen2"
    assert any("model_type=qwen2" in e for e in ident.evidence)


def test_level1_hf_config_bert_embedding(tmp_path: Path):
    repo = tmp_path / "bge-m3"
    _write(repo / "config.json", {"model_type": "bert", "hidden_size": 1024})
    ident = identify_path(repo)
    assert ident.capability == "text-embedding"
    assert ident.family == "bert"


def test_level1_hf_config_reranker_seqclassification(tmp_path: Path):
    """cross-encoder reranker:model_type 跟 embedding 同源(xlm-roberta),
    但 architectures 是 *ForSequenceClassification → 必须判 rerank 不是
    text-embedding。对应 bge-reranker-v2-m3 被误判进 embedding 的线上 bug。"""
    repo = tmp_path / "bge-reranker-v2-m3"
    _write(repo / "config.json", {
        "model_type": "xlm-roberta",
        "architectures": ["XLMRobertaForSequenceClassification"],
        "hidden_size": 1024,
    })
    ident = identify_path(repo)
    assert ident.capability == "rerank"
    assert ident.format == "hf_transformers"
    assert any("ForSequenceClassification" in e for e in ident.evidence)


def test_level1_hf_config_embedder_not_misjudged_as_rerank(tmp_path: Path):
    """对照组:纯 *Model 架构的 embedder 仍判 text-embedding,不被规则误伤。"""
    repo = tmp_path / "bge-m3"
    _write(repo / "config.json", {
        "model_type": "xlm-roberta",
        "architectures": ["XLMRobertaModel"],
        "hidden_size": 1024,
    })
    ident = identify_path(repo)
    assert ident.capability == "text-embedding"


def test_level1_hf_config_gguf_in_dir_marks_format_gguf(tmp_path: Path):
    """目录里有 .gguf 时 format 必须标 gguf —— config.json 在场也不例外。
    对应线上 embedding sidecar 因 format=hf_transformers 被 llama-server 拒。"""
    repo = tmp_path / "bge-m3"
    _write(repo / "config.json", {"model_type": "xlm-roberta", "hidden_size": 1024})
    (repo / "bge-m3-Q8_0.gguf").write_bytes(b"GGUF placeholder")
    ident = identify_path(repo)
    assert ident.capability == "text-embedding"
    assert ident.format == "gguf"  # 不是 hf_transformers


def test_level1_hf_config_clip_stays_image_embedding(tmp_path: Path):
    """CLIP 带 vision_config,但它是 image-embedding 不是 image-to-text;
    vision 规则不能把它覆盖掉。对应线上 image-embedding "no local candidate"。"""
    repo = tmp_path / "clip-vit-base-patch32"
    _write(repo / "config.json", {
        "model_type": "clip",
        "vision_config": {"hidden_size": 768},
    })
    ident = identify_path(repo)
    assert ident.capability == "image-embedding"


def test_level2_diffusers_text_to_image(tmp_path: Path):
    repo = tmp_path / "SDXL-1.0"
    _write(repo / "model_index.json", {"_class_name": "StableDiffusionXLPipeline", "_diffusers_version": "0.30.0"})
    ident = identify_path(repo)
    assert ident.capability == "text-to-image"
    assert ident.format == "hf_diffusers"
    assert ident.family == "StableDiffusionXLPipeline"


def test_level3_gguf_single_file(tmp_path: Path):
    """构造一个**最小**合法 GGUF 头：magic + version + tensor_count + 1 个 string KV。"""
    p = tmp_path / "qwen3-4b-q4.gguf"
    out = bytearray()
    out += b"GGUF"
    out += struct.pack("<I", 3)            # version
    out += struct.pack("<Q", 0)            # tensor_count
    out += struct.pack("<Q", 1)            # kv_count
    key = "general.architecture".encode()
    out += struct.pack("<Q", len(key))
    out += key
    out += struct.pack("<I", 8)            # value_type=string
    val = b"qwen2"
    out += struct.pack("<Q", len(val))
    out += val
    p.write_bytes(bytes(out))
    ident = identify_path(p)
    assert ident.format == "gguf"
    assert ident.family == "qwen2"
    assert ident.capability == "chat"


def test_level4_card_pipeline_tag(tmp_path: Path):
    repo = tmp_path / "weird-asr"
    # 没有 config.json / model_index.json / gguf；走 card.json
    _write(repo / "card.json", {"pipeline_tag": "automatic-speech-recognition"})
    ident = identify_path(repo)
    assert ident.capability == "speech-to-text"
    assert ident.format == "unknown" or ident.format.startswith("hf")


def test_level4_readme_front_matter(tmp_path: Path):
    repo = tmp_path / "tts-model"
    (repo).mkdir()
    (repo / "README.md").write_text(
        "---\n"
        "language: zh\n"
        "pipeline_tag: text-to-speech\n"
        "---\n"
        "# Foo\n",
        encoding="utf-8",
    )
    ident = identify_path(repo)
    assert ident.capability == "text-to-audio"


def test_level5_path_hint_only(tmp_path: Path):
    p = tmp_path / "embed_models" / "mystery"
    p.mkdir(parents=True)
    (p / "weights.safetensors").write_text("dummy")  # 不构成有效模型，但应触发 path hint
    ident = identify_path(p)
    assert ident.capability == "text-embedding"  # "embed" hint
    assert ident.format == "safetensors"


def test_no_match_returns_other(tmp_path: Path):
    p = tmp_path / "no_idea"
    p.mkdir()
    ident = identify_path(p)
    assert ident.capability == "other"
    assert ident.confidence <= 0.3


def test_default_id_used(tmp_path: Path):
    p = tmp_path / "Qwen3-A"
    _write(p / "config.json", {"model_type": "qwen3"})
    ident = identify_path(p, default_id="huggingface/Qwen3-A")
    assert ident.model_id == "huggingface/Qwen3-A"


def test_unknown_model_type_in_embedding_path_falls_back_to_path_hint(tmp_path: Path):
    """回归:HF config 看到 ``model_type=new``(GTE 系列)时 capability=other,
    confidence=0.7,会"赢"过 path_hint(confidence=0.3),错归到 other。

    修复:max 选出来的 winner 如果 capability=='other',且别的候选有具体
    capability,用具体的覆盖。

    场景对应用户机上的 ``D:\\chayuan_data\\models\\bundled\\embedding\\gte-multilingual-base``,
    那里 sidecar startup 时找不到 text-embedding 候选导致 cap 起不来。
    """
    # 模拟 bundled/embedding/<model> 路径
    repo = tmp_path / "bundled" / "embedding" / "gte-multilingual-base"
    _write(repo / "config.json", {
        "model_type": "new",   # GTE 自定义 model_type
        "hidden_size": 768,
        "vocab_size": 250048,
    })
    ident = identify_path(repo)
    assert ident.capability == "text-embedding", (
        f"path 含 embedding/ 且 model_type=new 时,应回落到 path_hint 的 text-embedding;"
        f"实际 cap={ident.capability!r}, evidence={ident.evidence!r}"
    )
    # format / family 仍应从 HF config 拿到(它的 confidence 仍是最高)
    assert ident.format == "hf_transformers"
    assert ident.family == "new"
    # evidence 要保留 HF config 的痕迹 + 标注 capability 被覆盖
    assert any("model_type=new" in e for e in ident.evidence), (
        f"应保留 HF config evidence;实际 {ident.evidence}"
    )
    assert any("overridden from 'other'" in e for e in ident.evidence), (
        f"应标注 capability 被覆盖,方便排查;实际 {ident.evidence}"
    )


def test_other_winner_no_concrete_candidate_stays_other(tmp_path: Path):
    """补充:winner=other 但没有别的候选给出具体 capability 时,保持 other。"""
    # 既无 path hint 也无 model_type 命中的目录
    p = tmp_path / "mystery_repo"
    _write(p / "config.json", {"model_type": "unknownarch"})  # 不在表里
    ident = identify_path(p)
    assert ident.capability == "other"
    # 不应该添加 overridden evidence
    assert not any("overridden" in e for e in ident.evidence)
