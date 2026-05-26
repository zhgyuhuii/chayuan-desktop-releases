"""图像 KB upload 流水线:OCR ‖ CLIP 并行,默认文本向量模型软降级,状态机推进。

输入:store_name + item_id + image_bytes。流程:
    1. state=queued → state=ocr_and_embedding, progress=10
    2. asyncio.gather(OCR, CLIP)
    3. CLIP 失败 → state=failed, return
    4. 写 image vector, progress=70
    5. OCR 成功且 text 非空 → 写 ocr_*, progress=85;再调用户默认文本向量模型
       - 文本向量成功 → 写 text vector, has_text_vector=True
       - 文本向量失败/未配置 → has_text_vector=False(软降级)
    6. state=ready, progress=100, flush
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

import numpy as np

from chayuan.server.image_source.ocr_client import OCRResult, run_ocr
from chayuan.server.image_source.ocr_client import (
    resolve_port as _resolve_ocr_port_impl,
)
from chayuan.server.image_source.store import get_store
from chayuan.server.image_source.text_embed_client import (
    EmbedResult, embed_text,
)
from chayuan.server.image_source.text_embed_client import (
    resolve_endpoint as _resolve_text_embed_endpoint_impl,
)

logger = logging.getLogger("chayuan.image_source.pipeline")

_OCR_SEMAPHORE = asyncio.Semaphore(2)


# ---- 可 monkeypatch 的 seam ----

async def _run_ocr(image_bytes: bytes, *, port: int, timeout: float = 30.0) -> OCRResult:
    async with _OCR_SEMAPHORE:
        return await run_ocr(image_bytes, port=port, timeout=timeout)


async def _run_clip_embed(image_bytes: bytes, *, model_name: str = ""):
    """同步 BaseImageEmbedder.embed_image 放线程池跑。"""
    from chayuan.server.image_source.embedder import get_embedder

    def _sync():
        emb = get_embedder(model_name) if model_name else get_embedder(None)
        return np.asarray(emb.embed_image(image_bytes), dtype="float32").reshape(-1)
    return await asyncio.to_thread(_sync)


async def _run_text_embed(
    text: str, *, base_url: str, model: str,
    api_key: Optional[str] = None, timeout: float = 30.0,
) -> EmbedResult:
    return await embed_text(text, base_url=base_url, model=model,
                              api_key=api_key, timeout=timeout)


def _resolve_ocr_port() -> Optional[int]:
    return _resolve_ocr_port_impl()


def _resolve_text_embed_endpoint() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(base_url, model, api_key) 由 text_embed_client 用 utils.get_default_embedding 解析。"""
    return _resolve_text_embed_endpoint_impl()


# ---- 主流程 ----

# PyTorch 未安装 / 不可用 —— 图像向量化(image-embedding)硬依赖 PyTorch。
# 这个指引必须可执行:明确告诉用户去哪装、跟 PyTorch 有关。
_TORCH_MISSING_HINT = (
    "图像向量化失败:未安装 PyTorch。图像入知识库需要先把图片转成向量,"
    "这一步(image-embedding)依赖 PyTorch + 图像向量模型(CLIP / SigLIP 等)。"
    "请到「设置 → 本地模型服务 → PyTorch」安装 PyTorch(无 GPU 装 CPU 版,"
    "有 NVIDIA GPU 可装 GPU 版),装完重启 chayuan-server;"
    "再到「图像嵌入」处下载图像向量模型,然后重新上传图片。"
)

# torch 装了但 import 链断裂(多半 torchvision 跟 torch 版本不配对)。
_TORCH_BROKEN_HINT = (
    "图像向量化失败:PyTorch 已安装但加载失败(torch 与 torchvision 版本不配对)。"
    "请到「设置 → 本地模型服务 → PyTorch」点「重装」让两者版本同步,"
    "装完重启 chayuan-server 后重新上传图片。"
)

# transformers 缺失 —— 同样是 image-embedding 运行时依赖。
_TRANSFORMERS_MISSING_HINT = (
    "图像向量化失败:缺少 transformers 运行时依赖。"
    "image-embedding 依赖 PyTorch + transformers;"
    "请到「设置 → 本地模型服务 → PyTorch」安装 PyTorch(会一并装好依赖),"
    "或在 chayuan-server 的 Python 环境运行 `pip install -U transformers`,"
    "然后重启 chayuan-server。"
)

_CLIP_ERR_HINTS: tuple[tuple[str, str], ...] = (
    # ── PyTorch 运行时不可用 —— image-embedding 的根因 #1 ────────────────
    # torch 没装:import torch 抛 "No module named 'torch'"。
    ("No module named 'torch'", _TORCH_MISSING_HINT),
    ("No module named \"torch\"", _TORCH_MISSING_HINT),
    # torchvision 缺失 / ABI 不配:transformers image 链断在 torchvision。
    ("No module named 'torchvision'", _TORCH_MISSING_HINT),
    ("operator torchvision::nms does not exist", _TORCH_BROKEN_HINT),
    # transformers 缺失。
    ("No module named 'transformers'", _TRANSFORMERS_MISSING_HINT),
    # 模型未缓存 — hf_clip.py 默认 local_files_only=True 时抛的友好错误
    ("没在本地缓存",
     "图像向量模型尚未下载。到「设置 → AI 平台 → 图像嵌入」点击模型旁"
     "的「下载」按钮,完成后再次上传图片即可。"),
    # transformers 旧版没 AutoProcessor — 用户必装 transformers>=4.21 才有
    ("AutoProcessor",
     "图像向量化失败:当前 transformers 包没有 AutoProcessor。"
     "在 chayuan-server 的 Python 环境运行 `pip install -U transformers` 升级,"
     "然后重启 chayuan-server。"),
    # CUDA / GPU 类
    ("CUDA out of memory",
     "图像向量化失败:GPU 显存不足。换更小的 CLIP 模型,或在「设置 → "
     "本地模型服务 → 图像嵌入」配 device=cpu。"),
    # 网络下载模型
    ("HTTPSConnectionPool", "图像向量化失败:首次加载 CLIP 模型需要下载,网络不通。"
     "先在「设置 → AI 平台 → 图像嵌入」预下载模型,或挂代理。"),
)


def _friendly_clip_error(raw: str) -> str:
    """把底层 stack 翻译成产品级提示;命中规则返友好文案,**不匹配返空字符串**,
    让 caller 走默认 fallback("以图搜图不可用 ...")。

    特别处理 PyTorch:image-embedding 硬依赖 PyTorch,torch 没装 / 不可用是
    「未能向量化」最常见的根因。除字符串规则外,还会调
    :func:`pytorch_installer.torch_runtime_unavailable_reason` 主动探一遍
    torch 是否就绪,把「需要去设置页装 PyTorch」这条可执行指引补全。
    """
    if not raw:
        return ""
    for pattern, hint in _CLIP_ERR_HINTS:
        if pattern in raw:
            return hint
    # 字符串规则没命中,但失败可能仍是 torch 环境问题(loader 包装过异常文案)。
    # 主动探一次 torch 是否可用,可用才返空走默认 fallback。
    low = raw.lower()
    if "torch" in low or "tensor" in low or "embedder" in low or "torch/transformers" in low:
        try:
            from chayuan.server.runtime.pytorch_installer import (
                torch_runtime_unavailable_reason,
            )
            reason = torch_runtime_unavailable_reason()
            if reason:
                return reason
        except Exception:  # noqa: BLE001 — 探测失败不影响主流程
            pass
    return ""


async def process_item(
    store_name: str, item_id: str, image_bytes: bytes,
    *, model_name: str = "",
) -> None:
    """处理一个 upload item。永不抛异常,失败信息写到 item.state/error。

    图像 KB 的核心能力是 CLIP 图像向量(以图搜图),OCR 文字是附加:
      - CLIP 成功 → state=ready;文字向量缺失只挂 diag 备注,不降级。
      - CLIP 失败 → state=partial — **不算"成功"**:图像向量缺失、以图搜图找不到
                    这张图。即便 OCR 出了文字(仅文字可检索)也只是降级可用。
                    图已落盘、记录在库;用户修好环境(让 Infinity image-embedding
                    sidecar 起来 / 装升 transformers)后点 UI 上"重新索引"重跑。
                    (partial 让用户在图像 KB 列表仍看得到这张图、且一眼看出它没做
                     图像向量,而不是被误标成"成功"。)
    """
    store = get_store(store_name)

    store.update(item_id, state="ocr_and_embedding", progress=10)

    ocr_port = _resolve_ocr_port()
    ocr_coro = (
        _run_ocr(image_bytes, port=ocr_port)
        if ocr_port else _ocr_unavailable()
    )
    clip_coro = _run_clip_embed_safe(image_bytes, model_name=model_name)
    ocr_res, clip_res = await asyncio.gather(ocr_coro, clip_coro)

    has_image_vector = False
    if clip_res.vector is not None and not clip_res.error:
        store.add_image_vector(item_id, clip_res.vector)
        has_image_vector = True
        store.update(item_id, progress=50)
    else:
        logger.info("CLIP embed unavailable for %s: %s",
                    item_id, clip_res.error or "no vector")

    has_text_vector = False
    if ocr_res.text:
        store.update(
            item_id,
            ocr_text=ocr_res.text,
            ocr_lang=ocr_res.lang,
            ocr_confidence=ocr_res.confidence,
            progress=70,
        )
        base_url, model, api_key = _resolve_text_embed_endpoint()
        if base_url and model:
            txt_res = await _run_text_embed(
                ocr_res.text, base_url=base_url, model=model, api_key=api_key,
            )
            if txt_res.vector is not None:
                store.add_text_vector(item_id, txt_res.vector)
                has_text_vector = True
                store.update(item_id, progress=90)
            else:
                logger.info("text embed failed for %s (%s): %s",
                            item_id, model, txt_res.error)
        else:
            logger.info(
                "default text embedding model unresolved;"
                " soft-degrade %s (user must configure 默认模型选择 → 文本嵌入)",
                item_id,
            )
    elif ocr_res.error:
        logger.info("OCR failed for %s: %s", item_id, ocr_res.error)

    store.update(item_id, has_text_vector=has_text_vector)

    # 图像 KB 的核心能力是 CLIP 图像向量。只有 has_image_vector 才算真正"成功"
    # (ready);CLIP 失败的图即便 OCR 出了文字,也只是降级可用 → partial,
    # 让用户一眼看出"这张图没做图像向量、以图搜图找不到它",不被误标成"成功"。
    clip_friendly = _friendly_clip_error(clip_res.error or "")
    if has_image_vector:
        notes: list[str] = []
        if ocr_res.text and not has_text_vector:
            notes.append("OCR 文字向量缺失(默认文本嵌入未配)")
        if not ocr_res.text:
            notes.append("无 OCR 文本(空图/OCR 未跑)")
        diag = " · ".join(notes) or None
        store.update(item_id, state="ready", progress=100, error=diag)
    else:
        # CLIP 图像向量缺失 —— 核心能力没到位,不算成功。图保留可见,
        # 用户修完环境(Infinity sidecar 起来)后可点"重新索引"重跑。
        clip_part = clip_friendly or (clip_res.error or "CLIP 未装/失败")
        if has_text_vector:
            err = f"图像向量缺失(以图搜图不可用),仅文字可检索 —— CLIP: {clip_part}"
        elif ocr_res.text:
            err = f"图像向量 + OCR 文字向量均缺失 —— CLIP: {clip_part}"
        else:
            ocr_part = ocr_res.error or "no text"
            err = f"CLIP: {clip_part} · OCR: {ocr_part}"
        store.update(item_id, state="partial", error=err, progress=100)

    store.flush()


async def _ocr_unavailable() -> OCRResult:
    return OCRResult(error="ocr sidecar port unresolved")


class _ClipResult:
    __slots__ = ("vector", "error")
    def __init__(self, vector=None, error=None):
        self.vector = vector
        self.error = error


async def _run_clip_embed_safe(image_bytes: bytes, *, model_name: str) -> _ClipResult:
    try:
        vec = await _run_clip_embed(image_bytes, model_name=model_name)
        return _ClipResult(vector=vec)
    except Exception as e:  # noqa: BLE001
        logger.exception("CLIP embed failed")
        return _ClipResult(error=str(e))
