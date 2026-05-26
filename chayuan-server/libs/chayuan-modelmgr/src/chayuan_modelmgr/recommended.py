"""Curated recommended models per capability.

设计目的
========

用户在桌面/Web 设置面板里选 "图像嵌入模型 / 文本嵌入模型 ..." 时，平台
要给他一个 **小而精的推荐清单** —— 不是把 hf-mirror 上几万个模型全塞过来。

清单维护策略：

* 每个 capability 只列 1-3 个；
* 优先选 hf-mirror 镜像里有、体积可控、社区使用最多的；
* 标 ``offline_friendly``：能不能在没有 GPU 的笔记本上跑（决定单机 lite 是否打入）；
* 标 ``size_mb``（粗略，给前端做体积预估）；
* 标 ``runtime``：哪个适配器认它（ollama / infinity / comfyui …）；
* 标 ``id_aliases``：识别用，方便用户已经手动 ``chayuan model pull`` 过同一份；
* 提供 ``hf_repo`` （拉取地址）+ ``intent``（一行口语化推荐理由）。

清单的 SSOT 在这里；前端 / CLI / `chayuan ai-platform recommend` 都通过
:func:`get_recommended` / :func:`get_default_for_capability` 读，避免散落。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecommendedModel:
    """一条推荐项。

    ``id`` 是平台内的稳定标识（与 ``ModelRepository.id`` / ``LocalModel.public_id``
    对齐），``hf_repo`` 是从 HF / hf-mirror 拉的真实 repo 名。两者**可以不同**，
    例如本地 ollama 模型用 ``qwen2.5:4b`` 而上游 HF 是 ``Qwen/Qwen2.5-4B``。
    """

    id: str
    capability: str  # chat / text-embedding / image-embedding / rerank / t2i / t2v / tts / asr / ocr
    runtime: str
    hf_repo: str
    size_mb: int
    intent: str
    offline_friendly: bool = True
    optional_for_lite: bool = False
    id_aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "id_aliases": list(self.id_aliases)}


# ---------------------------------------------------------------------------
# 实际清单。每条都务必经过：
# 1) hf-mirror.com 上能拉到（验证：curl -fsSI https://hf-mirror.com/<repo>/resolve/main/config.json）
# 2) 体积 < 8 GB （单机 standard 包能塞下）
# 3) 适配器目录里有对应 runtime
# ---------------------------------------------------------------------------

_RECOMMENDED: tuple[RecommendedModel, ...] = (
    # === 对话 chat ===========================================================
    RecommendedModel(
        id="qwen2.5:4b",
        capability="chat",
        runtime="ollama",
        hf_repo="Qwen/Qwen2.5-4B-Instruct-GGUF",
        size_mb=2_400,
        intent="单机首选；4B 参数 + GGUF Q4，CPU 也能跑通。",
        id_aliases=("qwen2.5", "qwen-2.5-4b", "Qwen/Qwen2.5-4B-Instruct"),
    ),
    RecommendedModel(
        id="qwen2:0.5b",
        capability="chat",
        runtime="ollama",
        hf_repo="Qwen/Qwen2-0.5B-Instruct-GGUF",
        size_mb=350,
        intent="极小测试模型；CI smoke / 本地拉环境时优先它。",
        offline_friendly=True,
    ),
    RecommendedModel(
        id="llama3.1:8b-instruct-q4",
        capability="chat",
        runtime="ollama",
        hf_repo="meta-llama/Meta-Llama-3.1-8B-Instruct",
        size_mb=4_900,
        intent="英文场景比 Qwen 更稳；中文一般。",
        optional_for_lite=True,
    ),
    # 90-2:Standard 套餐主推对话(中文办公场景)
    RecommendedModel(
        id="qwen2.5:7b",
        capability="chat",
        runtime="ollama",
        hf_repo="Qwen/Qwen2.5-7B-Instruct-GGUF",
        size_mb=4_500,
        intent="Standard 套餐默认;Q4_K_M 量化,中文好,16GB 内存可跑。",
        id_aliases=("Qwen/Qwen2.5-7B-Instruct", "qwen2.5-7b"),
    ),
    # 90-2:Lite 套餐主推 — 4GB 内存机也能跑
    RecommendedModel(
        id="qwen2.5:1.5b",
        capability="chat",
        runtime="ollama",
        hf_repo="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        size_mb=1_500,
        intent="Lite 套餐默认;1.5B 参数,8GB 内存绰绰有余。",
        id_aliases=("Qwen/Qwen2.5-1.5B-Instruct", "qwen2.5-1.5b"),
    ),
    # 90-2:代码助手场景
    RecommendedModel(
        id="qwen2.5-coder:7b",
        capability="chat",
        runtime="ollama",
        hf_repo="Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        size_mb=4_500,
        intent="代码助手专用;在本地仓库问答 / 代码补全场景比通用 chat 强。",
        optional_for_lite=True,
        id_aliases=("Qwen/Qwen2.5-Coder-7B-Instruct",),
    ),
    # 90-2:Pro 套餐 — 多并发企业部署
    RecommendedModel(
        id="qwen2.5-14b-awq",
        capability="chat",
        runtime="vllm",
        hf_repo="Qwen/Qwen2.5-14B-Instruct-AWQ",
        size_mb=9_000,
        intent="Pro 套餐;AWQ 量化,vLLM PagedAttention 多并发吞吐 5-10 倍。",
        offline_friendly=False,
        optional_for_lite=True,
        id_aliases=("Qwen/Qwen2.5-14B-Instruct-AWQ",),
    ),

    # === 文本嵌入 text-embedding ==============================================
    RecommendedModel(
        id="bge-small-zh-v1.5",
        capability="text-embedding",
        runtime="infinity",
        hf_repo="BAAI/bge-small-zh-v1.5",
        size_mb=130,
        intent="中文 RAG 默认；CPU 跑得动；512 维。",
        id_aliases=("BAAI/bge-small-zh-v1.5",),
    ),
    RecommendedModel(
        id="bge-m3",
        capability="text-embedding",
        runtime="infinity",
        hf_repo="BAAI/bge-m3",
        size_mb=2_300,
        intent="多语言 + 长文本 RAG 首选；1024 维。",
        optional_for_lite=True,
    ),

    # === 图像嵌入 image-embedding ============================================
    RecommendedModel(
        id="jina-clip-v1",
        capability="image-embedding",
        runtime="infinity",
        hf_repo="jinaai/jina-clip-v1",
        size_mb=900,
        intent="跨模态：文-图、图-图都能搜；体积适中。",
        id_aliases=("jinaai/jina-clip-v1",),
    ),
    RecommendedModel(
        id="siglip-base-patch16-224",
        capability="image-embedding",
        runtime="infinity",
        hf_repo="google/siglip-base-patch16-224",
        size_mb=380,
        intent="纯图嵌入；比 CLIP 在小图上更稳。",
        optional_for_lite=True,
    ),
    # 90-2:中文专用图像嵌入(Lite 套餐默认)
    RecommendedModel(
        id="chinese-clip-vit-base-patch16",
        capability="image-embedding",
        runtime="infinity",
        hf_repo="OFA-Sys/chinese-clip-vit-base-patch16",
        size_mb=400,
        intent="纯中文 prompt 检索专用;Lite 套餐默认。",
        id_aliases=("OFA-Sys/chinese-clip-vit-base-patch16",),
    ),
    # 90-2:SigLIP2 — Google 2024 升级版,小图更稳
    RecommendedModel(
        id="siglip2-base-patch16-224",
        capability="image-embedding",
        runtime="infinity",
        hf_repo="google/siglip2-base-patch16-224",
        size_mb=400,
        intent="SigLIP2 升级版;CPU 兜底也可用。",
        optional_for_lite=True,
        id_aliases=("google/siglip2-base-patch16-224",),
    ),

    # === 重排 rerank ==========================================================
    RecommendedModel(
        id="bge-reranker-v2-m3",
        capability="rerank",
        runtime="infinity",
        hf_repo="BAAI/bge-reranker-v2-m3",
        size_mb=2_300,
        intent="中英多语 cross-encoder rerank 首选。",
    ),
    # 90-2:轻量重排(Lite 套餐用,CPU 友好)
    RecommendedModel(
        id="bge-reranker-base",
        capability="rerank",
        runtime="infinity",
        hf_repo="BAAI/bge-reranker-base",
        size_mb=600,
        intent="Lite 套餐默认重排;比 v2-m3 弱但 CPU 也跑得动。",
        optional_for_lite=False,
        id_aliases=("BAAI/bge-reranker-base",),
    ),

    # === 文生图 t2i ===========================================================
    RecommendedModel(
        id="sd_v15.safetensors",
        capability="text-to-image",
        runtime="comfyui",
        hf_repo="runwayml/stable-diffusion-v1-5",
        size_mb=4_200,
        intent="经典 SD1.5；CPU 也能跑（25 步约 30 秒）。",
        optional_for_lite=True,
    ),
    RecommendedModel(
        id="sdxl_base_1.0.safetensors",
        capability="text-to-image",
        runtime="comfyui",
        hf_repo="stabilityai/stable-diffusion-xl-base-1.0",
        size_mb=6_900,
        intent="SDXL；需要 GPU；质量比 SD1.5 显著好。",
        offline_friendly=False,
        optional_for_lite=True,
    ),

    # === 文生视频 t2v =========================================================
    RecommendedModel(
        id="svd_xt.safetensors",
        capability="text-to-video",
        runtime="comfyui",
        hf_repo="stabilityai/stable-video-diffusion-img2vid-xt",
        size_mb=8_200,
        intent="SVD-XT；与 SD1.5 keyframe 串联出 14 帧短视频。",
        offline_friendly=False,
        optional_for_lite=True,
    ),

    # === 文生声音 tts =========================================================
    RecommendedModel(
        id="cosyvoice-base",
        capability="text-to-speech",
        runtime="cosyvoice",
        hf_repo="FunAudioLLM/CosyVoice2-0.5B",
        size_mb=1_400,
        intent="多语言 TTS；生成自然，零样本克隆可选。",
    ),
    RecommendedModel(
        id="piper-zh_CN-huayan-medium",
        capability="text-to-speech",
        runtime="piper",
        hf_repo="rhasspy/piper-voices",
        size_mb=85,
        intent="离线轻量 TTS；中文女声 huayan；适合 lite 单机。",
    ),

    # === 语音识别 asr =========================================================
    RecommendedModel(
        id="paraformer-zh",
        capability="asr",
        runtime="funasr",
        hf_repo="funasr/paraformer-zh",
        size_mb=820,
        intent="中文流式识别首选；FunASR 自带的 paraformer-large-zh。",
    ),
    RecommendedModel(
        id="whisper-base",
        capability="asr",
        runtime="whispercpp",
        hf_repo="ggerganov/whisper.cpp",
        size_mb=140,
        intent="多语言 + 离线小模型；whisper.cpp 子进程。",
        optional_for_lite=True,
    ),

    # === 图像识别文字 ocr =====================================================
    RecommendedModel(
        id="rapidocr-onnx",
        capability="ocr",
        runtime="rapidocr",
        hf_repo="RapidAI/RapidOCR",
        size_mb=20,
        intent="ONNX 全平台跑；中英混排首选；最小体积。",
    ),
    RecommendedModel(
        id="paddleocr-pp-ocrv4",
        capability="ocr",
        runtime="paddleocr",
        hf_repo="PaddlePaddle/PaddleOCR",
        size_mb=210,
        intent="PaddleOCR PP-OCRv4；准确率比 RapidOCR 略高，体积大些。",
        optional_for_lite=True,
    ),
)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def get_recommended(capability: str | None = None) -> list[RecommendedModel]:
    """按 capability 过滤推荐清单；不传 capability 时返回全部。"""
    if not capability:
        return list(_RECOMMENDED)
    cap = capability.replace("_", "-")
    return [m for m in _RECOMMENDED if m.capability == cap]


def get_default_for_capability(capability: str) -> RecommendedModel | None:
    """每个 capability 的"首选"——清单里的第一条。

    用于：
    * 单机 lite/standard 包打底（不带可选）；
    * 用户进设置面板第一次切到某 capability 时显示"未配置 → 推荐安装 X"。
    """
    items = get_recommended(capability)
    return items[0] if items else None


def list_capabilities() -> tuple[str, ...]:
    """所有有推荐项的 capability。"""
    return tuple(sorted({m.capability for m in _RECOMMENDED}))
