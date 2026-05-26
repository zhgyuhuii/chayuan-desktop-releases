"""本地模型识别（5 级回退）。

为什么需要"识别"？
====================

``model_registry/catalog.py`` 解决的是"云端 Hub 里有什么模型"，但服务真正能
跑什么取决于"开发者把什么塞到本地磁盘上"。本模块负责回答："给我一个目录或
文件，告诉我它是什么模型、什么 family、能用作哪种 capability"。

5 级回退（高 → 低）
====================

1. **HF 标准 ``config.json``**：``model_type``（``llama`` / ``qwen2`` / ``bert``
   / ``baichuan`` / ...）+ 部分关键字段（``hidden_size``、``vocab_size``）；
2. **HF Diffusers ``model_index.json``**：``_class_name``（``StableDiffusion
   PipelineXL`` / ``KandinskyPipeline`` / ...），用于 text-to-image / image-to-image；
3. **GGUF 单文件**：根据 magic 号 + 头部 metadata 中的 ``general.architecture``
   读出族系（``llama`` / ``qwen2`` / ``bge`` / ``whisper.cpp`` / ...）；
4. **HuggingFace ``pipeline_tag``**：来自 ``README.md`` front matter / 仓库
   metadata（如果开发者随包带了 ``cardData.json`` / ``card.json``）；
5. **路径片段启发**：从父目录名 / 文件名抽 ``llm`` / ``embedding`` / ``rerank``
   / ``asr`` / ``tts`` / ``image`` / ``ocr`` 关键字。

返回的统一结构 :class:`ModelIdentity` 给 :mod:`local_index` 写入 ``models.json``。

公开 API：
* :class:`ModelIdentity`
* :func:`identify_path` —— 主入口
* :func:`infer_capabilities` —— 仅做能力推断（不依赖文件 I/O）
"""
from __future__ import annotations

import json
import logging
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.model_registry.identifier")


# 标签 → 能力（这里的 capability 与 catalog.py 的 MODEL_TYPES 对齐）
_CAPABILITY_BY_PIPELINE: Dict[str, str] = {
    "text-generation": "chat",
    "text2text-generation": "chat",
    "conversational": "chat",
    "question-answering": "chat",
    "feature-extraction": "text-embedding",
    "sentence-similarity": "text-embedding",
    "text-embeddings-inference": "text-embedding",
    "image-feature-extraction": "image-embedding",
    "zero-shot-image-classification": "image-embedding",
    "image-to-text": "image-to-text",
    "visual-question-answering": "image-to-text",
    "image-text-to-text": "image-to-text",
    "text-to-image": "text-to-image",
    "image-to-image": "text-to-image",
    "text-to-video": "text-to-video",
    "text-to-speech": "text-to-audio",
    "audio-to-audio": "text-to-audio",
    "automatic-speech-recognition": "speech-to-text",
    "audio-classification": "speech-to-text",
}

_CAPABILITY_BY_MODELTYPE: Dict[str, str] = {
    "llama": "chat", "qwen2": "chat", "qwen": "chat", "qwen3": "chat",
    "mistral": "chat", "phi": "chat", "phi3": "chat", "gemma": "chat",
    "baichuan": "chat", "baichuan2": "chat", "chatglm": "chat", "glm": "chat",
    "internlm": "chat", "yi": "chat", "deepseek": "chat", "minicpm": "chat",
    "bert": "text-embedding", "xlm-roberta": "text-embedding",
    "bge": "text-embedding", "nomic_bert": "text-embedding",
    "whisper": "speech-to-text", "wav2vec2": "speech-to-text",
    "vit": "image-embedding", "clip": "image-embedding",
    "blip": "image-to-text", "llava": "image-to-text", "qwen2_vl": "image-to-text",
}

_DIFFUSERS_PIPELINE_KIND: Dict[str, str] = {
    # _class_name → capability
    "StableDiffusionPipeline": "text-to-image",
    "StableDiffusionXLPipeline": "text-to-image",
    "StableDiffusion3Pipeline": "text-to-image",
    "FluxPipeline": "text-to-image",
    "KandinskyPipeline": "text-to-image",
    "KandinskyV22Pipeline": "text-to-image",
    "ControlNetPipeline": "text-to-image",
    "AnimateDiffPipeline": "text-to-video",
    "VideoCrafterPipeline": "text-to-video",
    "StableAudioPipeline": "text-to-audio",
}

_PATH_HINTS: List[Tuple[str, str]] = [
    # 顺序敏感：更具体的关键字在前。
    ("rerank", "rerank"),
    ("embedding", "text-embedding"),
    ("embed", "text-embedding"),
    ("speech-to-text", "speech-to-text"),
    ("speech_to_text", "speech-to-text"),
    ("asr", "speech-to-text"),
    ("whisper", "speech-to-text"),
    ("text-to-speech", "text-to-audio"),
    ("text_to_speech", "text-to-audio"),
    ("tts", "text-to-audio"),
    ("piper", "text-to-audio"),
    ("text-to-image", "text-to-image"),
    ("text_to_image", "text-to-image"),
    ("text2image", "text-to-image"),
    ("stable-diffusion", "text-to-image"),
    ("flux", "text-to-image"),
    ("text-to-video", "text-to-video"),
    ("text_to_video", "text-to-video"),
    ("ocr", "image-to-text"),
    ("vision", "image-to-text"),
    ("vlm", "image-to-text"),
    ("rag", "text-embedding"),
    ("llm", "chat"),
    ("chat", "chat"),
    ("instruct", "chat"),
]


@dataclass
class ModelIdentity:
    """识别结果的统一记录。

    Attributes:
        model_id: 唯一 id（默认走"目录名相对 models 根 / 文件名"），调用方可覆盖。
        family: 家族名（``llama`` / ``qwen`` / ``bge`` / ``stable-diffusion`` / ``whisper``）。
        capability: catalog.py MODEL_TYPES 之一（``chat`` / ``text-embedding``
            / ``rerank`` / ``image-to-text`` / ``text-to-image`` / ``text-to-video``
            / ``text-to-audio`` / ``speech-to-text`` / ``image-embedding`` / ``other``）
        format: ``hf_transformers`` / ``hf_diffusers`` / ``gguf`` / ``onnx`` /
            ``safetensors`` / ``ckpt`` / ``unknown``
        confidence: 0.0 ~ 1.0；越接近 1 表示识别越可靠。
        evidence: 触发的判据 + 命中的字段（用于 UI 提示"为什么识别成 X"）。
        meta: 额外字段（``hidden_size`` / ``vocab_size`` / ``revision`` / ``license``）。
    """

    model_id: str = ""
    family: str = ""
    capability: str = "other"
    format: str = "unknown"
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "family": self.family,
            "capability": self.capability,
            "format": self.format,
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence),
            "meta": dict(self.meta),
        }


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _identify_hf_config(p: Path) -> Optional[ModelIdentity]:
    """Level 1: HF transformers ``config.json``。"""
    cfg_path = p / "config.json" if p.is_dir() else (p if p.name == "config.json" else None)
    if not cfg_path or not cfg_path.is_file():
        return None
    cfg = _read_json(cfg_path)
    if not cfg:
        return None
    mt = str(cfg.get("model_type") or cfg.get("architectures", [""])[0] or "").lower()
    if not mt:
        return None
    cap = _CAPABILITY_BY_MODELTYPE.get(mt) or "other"
    family = mt
    confidence = 0.95 if cap != "other" else 0.7
    evidence = [f"config.json#model_type={mt}"]

    # cross-encoder reranker 修正:bge-reranker / jina-reranker 这类重排模型
    # 的 model_type 跟 embedding 模型同源(bge-reranker-v2-m3 是 xlm-roberta、
    # bge-reranker-base 是 bert)→ 只看 model_type 会把它们误判成
    # text-embedding。区别在 architectures:reranker 是 *ForSequenceClassification
    # (带分类头,num_labels=1 输出相关性分),embedder 是纯 *Model。
    # 有分类头 + 本来要落 text-embedding → 实际是 rerank。
    archs = [str(a).lower() for a in (cfg.get("architectures") or [])]
    if cap == "text-embedding" and any(
        "forsequenceclassification" in a for a in archs
    ):
        cap = "rerank"
        evidence.append("architectures=*ForSequenceClassification => rerank")

    # format 修正:目录里同时有 .gguf 时标 format=gguf。
    # chat/embedding/rerank 跑 llama-server 引擎只认 gguf;即便同目录有 HF
    # config.json(metadata)+ tokenizer,真正可加载的权重是 gguf。不标 gguf
    # 的话 llama-server 起 sidecar 时会拒:"format='hf_transformers' 不是 gguf"
    # → 对应线上 embedding sidecar failed。capability/family/meta 仍取自
    # config.json(那是更准的元数据),只有 format 跟着实际权重走。
    fmt = "hf_transformers"
    if p.is_dir() and any(p.glob("*.gguf")):
        fmt = "gguf"
        evidence.append("dir has .gguf => format=gguf")

    ident = ModelIdentity(
        family=family,
        capability=cap,
        format=fmt,
        confidence=confidence,
        evidence=evidence,
        meta={
            k: cfg[k] for k in ("hidden_size", "vocab_size", "torch_dtype", "model_type")
            if k in cfg
        },
    )
    # 进一步精修:vision 模型常见标志 → image-to-text(VL 看图说话)。
    # 但 CLIP / SigLIP / ViT 这类**图像嵌入**模型也带 vision_config,它们
    # 是 image-embedding 不是 image-to-text。已经被 _CAPABILITY_BY_MODELTYPE
    # 正确判成 image-embedding 的,不要被这条 vision 规则覆盖掉
    # → 对应线上 image-embedding sidecar "no local candidate"。
    if (("vision_config" in cfg or mt in ("llava", "qwen2_vl", "internvl"))
            and ident.capability != "image-embedding"):
        ident.capability = "image-to-text"
        ident.evidence.append("config.json has vision_config")
    return ident


def _identify_diffusers(p: Path) -> Optional[ModelIdentity]:
    """Level 2: HF diffusers ``model_index.json``。"""
    if not p.is_dir():
        return None
    mi = p / "model_index.json"
    if not mi.is_file():
        return None
    cfg = _read_json(mi)
    if not cfg:
        return None
    cls = str(cfg.get("_class_name") or "").strip()
    cap = _DIFFUSERS_PIPELINE_KIND.get(cls, "text-to-image")
    family = cls or "diffusers"
    return ModelIdentity(
        family=family,
        capability=cap,
        format="hf_diffusers",
        confidence=0.92,
        evidence=[f"model_index.json#_class_name={cls or '?'}"],
        meta={"diffusers_class": cls, "diffusers_version": cfg.get("_diffusers_version", "")},
    )


def _identify_gguf(p: Path) -> Optional[ModelIdentity]:
    """Level 3: GGUF 单文件 magic + header metadata。

    GGUF 头：``GGUF`` magic (4B) + version (uint32 LE) + tensor_count (uint64 LE)
    + kv_count (uint64 LE) + KV pairs。我们只读到能拿到 ``general.architecture``
    就停手；既保证识别精度，也避免对 GB 级文件做无谓 IO。
    """
    if not p.is_file() or p.suffix.lower() != ".gguf":
        return None
    try:
        with open(p, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            version_b = f.read(4)
            if len(version_b) < 4:
                return None
            (version,) = struct.unpack("<I", version_b)
            f.read(8)   # tensor_count
            kv_count_b = f.read(8)
            if len(kv_count_b) < 8:
                return None
            (kv_count,) = struct.unpack("<Q", kv_count_b)
            architecture = ""
            quant_type = ""
            for _ in range(min(int(kv_count), 256)):
                key_len_b = f.read(8)
                if len(key_len_b) < 8:
                    break
                (key_len,) = struct.unpack("<Q", key_len_b)
                if key_len > 4096:
                    break
                key = f.read(key_len).decode("utf-8", errors="replace")
                value_type_b = f.read(4)
                if len(value_type_b) < 4:
                    break
                (vtype,) = struct.unpack("<I", value_type_b)
                # value_type 8 = string；其它仅做粗略 skip 以避免读完整文件
                if vtype == 8:
                    vlen_b = f.read(8)
                    if len(vlen_b) < 8:
                        break
                    (vlen,) = struct.unpack("<Q", vlen_b)
                    if vlen > 4096:
                        break
                    value = f.read(vlen).decode("utf-8", errors="replace")
                    if key == "general.architecture":
                        architecture = value
                    elif key == "general.quantization":
                        quant_type = value
                    if architecture and quant_type:
                        break
                else:
                    # 简化：value_type != string 时只跳过 1-32 字节，避免吃太多
                    skip = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 9: 8, 10: 8, 11: 8, 12: 8}.get(vtype, 0)
                    if skip:
                        f.read(skip)
                    else:
                        # 不认识的 type → 放弃后续字段，但已读到的留着
                        break
    except OSError:
        return None
    arch_lower = architecture.lower()
    cap = _CAPABILITY_BY_MODELTYPE.get(arch_lower) or "chat"
    return ModelIdentity(
        family=arch_lower or "gguf",
        capability=cap,
        format="gguf",
        confidence=0.9 if arch_lower else 0.6,
        evidence=[f"gguf magic + general.architecture={architecture or '?'}"],
        meta={"gguf_version": version, "quantization": quant_type},
    )


def _identify_card(p: Path) -> Optional[ModelIdentity]:
    """Level 4: card front-matter / cardData.json。"""
    if not p.is_dir():
        return None
    candidates = [p / "cardData.json", p / "card.json", p / "README.md"]
    for c in candidates:
        if not c.is_file():
            continue
        if c.suffix.lower() == ".md":
            try:
                head = c.read_text(encoding="utf-8", errors="ignore").splitlines()[:80]
            except OSError:
                continue
            if not head or not head[0].strip().startswith("---"):
                continue
            tag = ""
            for ln in head[1:]:
                if ln.strip().startswith("---"):
                    break
                if ln.strip().lower().startswith("pipeline_tag:"):
                    tag = ln.split(":", 1)[1].strip().strip('"\'')
                    break
            if not tag:
                continue
        else:
            cfg = _read_json(c) or {}
            tag = str(cfg.get("pipeline_tag") or "")
            if not tag:
                continue
        cap = _CAPABILITY_BY_PIPELINE.get(tag) or "other"
        return ModelIdentity(
            family=tag.replace("-", "_"),
            capability=cap,
            format="hf_transformers",
            confidence=0.7 if cap != "other" else 0.4,
            evidence=[f"{c.name}#pipeline_tag={tag}"],
            meta={"pipeline_tag": tag},
        )
    return None


def _identify_path_hint(p: Path) -> Optional[ModelIdentity]:
    """Level 5: 路径关键字。"""
    parts = "/".join(p.parts).lower()
    fmt = _detect_format_from_files(p)
    for hint, cap in _PATH_HINTS:
        if hint in parts:
            return ModelIdentity(
                family=hint.replace("-", "_"),
                capability=cap,
                format=fmt,
                confidence=0.3,
                evidence=[f"path keyword={hint}"],
            )
    return None


def _detect_format_from_files(p: Path) -> str:
    """格式只看文件扩展名 / 标志文件（不做内容嗅探，速度优先）。"""
    if p.is_file():
        ext = p.suffix.lower()
        # 特殊:ggml-*.bin 是 whisper.cpp / llama.cpp 的旧式 ggml 权重(file magic 'ggml'),
        # 跟普通 pytorch .bin 完全不同。按文件名前缀识别,避免被通用 .bin 规则吃成 pytorch
        # → 导致 resolve_whisper_args 拿到 format='pytorch' 与期望的 'ggml' 不匹配而报错。
        if ext == ".bin" and p.name.lower().startswith("ggml"):
            return "ggml"
        return {
            ".gguf": "gguf",
            ".onnx": "onnx",
            ".safetensors": "safetensors",
            ".ckpt": "ckpt",
            ".pth": "pytorch",
            ".bin": "pytorch",
        }.get(ext, "unknown")
    if not p.is_dir():
        return "unknown"
    if (p / "model_index.json").is_file():
        return "hf_diffusers"
    if (p / "config.json").is_file():
        return "hf_transformers"
    # 看子文件
    has_safetensors = any(p.glob("*.safetensors"))
    if has_safetensors:
        return "safetensors"
    has_onnx = any(p.glob("*.onnx"))
    if has_onnx:
        return "onnx"
    has_gguf = any(p.glob("*.gguf"))
    if has_gguf:
        return "gguf"
    # ggml-*.bin 单独识别(同上,跟普通 .bin 区分);whisper.cpp 装机典型布局
    # asr/whisper.cpp/ggml-tiny.bin 在这里命中
    if any(p.glob("ggml-*.bin")) or any(p.glob("ggml-*.gguf")):
        return "ggml"
    return "unknown"


def identify_path(path: os.PathLike, *, default_id: Optional[str] = None) -> ModelIdentity:
    """对一个目录或文件做 5 级识别。

    Args:
        path: 一个仓库目录（HF/Diffusers/ModelScope 风格）或单文件（gguf/onnx）。
        default_id: 如果识别后调用方还想用一个固定 id（如相对 models 根的路径），
            可在这里传入；为空时回落到目录名/文件名。

    Returns:
        :class:`ModelIdentity`，``capability`` 至少为 ``"other"``。
    """
    p = Path(path)

    # 5 级回退
    candidates: List[ModelIdentity] = []
    for fn in (_identify_hf_config, _identify_diffusers, _identify_gguf, _identify_card, _identify_path_hint):
        try:
            r = fn(p)
        except Exception as e:  # noqa: BLE001
            logger.debug("identifier %s failed on %s: %s", fn.__name__, p, e)
            r = None
        if r:
            candidates.append(r)
            if r.confidence >= 0.9:
                break

    if candidates:
        ident = max(candidates, key=lambda c: c.confidence)
        # capability=="other" 说明这个候选**只识别了格式/家族,没识别能力**(例如
        # HF config 看到 model_type=new 但 _CAPABILITY_BY_MODELTYPE 不认识"new"
        # → cap=other, confidence=0.7)。这种情况下,如果有其它候选给出**具体**
        # capability(不是 other),用它的 capability 覆盖,但保留赢家的 format /
        # family / meta / evidence(那些信息是赢家更可信的)。
        # 这条规则修了 gte-multilingual-base 这类 model_type=new 的 GTE 系列被
        # 错归到 'other' 的 bug:path 里有 embedding/ → path_hint 正确给了
        # text-embedding,但因 confidence 0.3 < 0.7 被丢弃。
        if ident.capability == "other":
            concrete = [c for c in candidates if c.capability != "other"]
            if concrete:
                # 选 confidence 最高的具体 capability 来覆盖
                best_concrete = max(concrete, key=lambda c: c.confidence)
                ident.capability = best_concrete.capability
                ident.evidence = list(ident.evidence) + [
                    f"capability overridden from 'other' by {best_concrete.evidence[0] if best_concrete.evidence else 'lower-confidence candidate'}"
                ]
    else:
        ident = ModelIdentity(
            family="",
            capability="other",
            format=_detect_format_from_files(p),
            confidence=0.1,
            evidence=["no rule matched"],
        )

    # model_id：优先 default_id，否则取目录/文件相对名
    if default_id:
        ident.model_id = default_id
    else:
        ident.model_id = p.name

    return ident


def infer_capabilities(family: str, capability: str) -> List[str]:
    """同一个模型可能担多种 capability（如 chat 模型也能 embedding），返回拓展列表。

    用于把 ``MODEL_PLATFORMS`` 自动 detect 时的列表填得更全。
    """
    cap_set = {capability}
    fam = family.lower()
    if cap_set == {"chat"} and fam in ("qwen2", "qwen", "llama", "mistral"):
        # chat 模型一般不直接做 embedding，这里**不**自动补，避免误导。
        pass
    if capability == "image-to-text":
        cap_set.add("image-embedding")
    return sorted(cap_set)


__all__ = [
    "ModelIdentity",
    "identify_path",
    "infer_capabilities",
]
