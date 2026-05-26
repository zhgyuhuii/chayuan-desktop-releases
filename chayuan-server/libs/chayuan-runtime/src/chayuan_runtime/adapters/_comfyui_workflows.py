"""ComfyUI workflow templates.

ComfyUI 把 "如何生成一张图" 表示为节点 DAG（每个节点一个 dict：``class_type``
+ ``inputs`` + ``id``）。OpenAI 的 ``/v1/images/generations`` 只给我们 ``prompt``
/ ``size`` / ``n``；要把它跑到 ComfyUI，必须把 OpenAI 字段渲染成节点 DAG。

这个模块给出两类模板：

* ``sd15`` / ``sdxl`` — 经典 T2I（CheckpointLoaderSimple → CLIPTextEncode ×2
  → EmptyLatentImage → KSampler → VAEDecode → SaveImage）
* ``svd`` — Stable Video Diffusion（ImageOnlyCheckpointLoader → VideoLinearCFG
  → SaveAnimatedWEBP）

模板以纯 dict 描述（**不依赖 ComfyUI 自身的 .json 文件**），使用 Python f-string
在调用时注入参数。这样：

1. 可以离线打包（不用塞一堆 ``workflow.json``）
2. 模板修改有 git diff
3. 单元测试不用启 ComfyUI 也能验证 DAG shape 是不是对的

公开 API：

* :func:`render_workflow` — 把 OpenAI payload 渲染成 ComfyUI ``/prompt`` body
* :data:`AVAILABLE_TEMPLATES`
"""
from __future__ import annotations

import logging
import os
import random
import re
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


def _parse_size(size: str | None, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    """``"1024x768"`` → ``(1024, 768)``。失败时返回 default。

    参考 OpenAI 兼容惯例：``size``='auto' / 缺失 → ``default``。
    """
    if not size or not isinstance(size, str):
        return default
    m = re.fullmatch(r"\s*(\d{2,5})\s*[x×*]\s*(\d{2,5})\s*", size, flags=re.I)
    if not m:
        return default
    w, h = int(m.group(1)), int(m.group(2))
    # ComfyUI 要求宽高 8 的倍数；自动向下取整到 8 的倍数避免 KSampler 报错
    w = max(64, (w // 8) * 8)
    h = max(64, (h // 8) * 8)
    return w, h


def _pick_seed(seed: Any) -> int:
    if isinstance(seed, int) and seed >= 0:
        return seed
    return random.SystemRandom().randint(0, 2**31 - 1)


# 经典 SD1.5 T2I 模板。节点 id 用字符串（ComfyUI 要求字符串 id）。
def _build_sd_classic(
    *,
    model_name: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    seed: int,
    batch_size: int,
    output_prefix: str,
) -> dict[str, dict[str, Any]]:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_size},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": output_prefix},
        },
    }


# SDXL：sampler 默认更适合 SDXL（dpmpp_2m + karras），cfg 稍低；其他与 SD 一致
def _build_sdxl(**kwargs: Any) -> dict[str, dict[str, Any]]:
    kwargs.setdefault("sampler_name", "dpmpp_2m")
    kwargs.setdefault("scheduler", "karras")
    kwargs.setdefault("cfg", 6.0)
    kwargs.setdefault("steps", 30)
    return _build_sd_classic(**kwargs)


# Stable Video Diffusion (SVD) — image-to-video，但 OpenAI ``/v1/videos/generations``
# 只给 prompt 时，我们退化为 "先 SD 生成 keyframe，再 SVD 动画化"。这里只
# 提供单纯 SVD 这一段（适配器层在调用前会先 t2i）。完整 graph 比较长，这里给
# 出 "image -> SVD -> SaveAnimatedWEBP" 的最短可用路径。
def _build_svd(
    *,
    model_name: str,
    init_image_node: list[Any],
    width: int,
    height: int,
    fps: int,
    motion_bucket_id: int,
    augmentation_level: float,
    video_frames: int,
    seed: int,
    output_prefix: str,
) -> dict[str, dict[str, Any]]:
    return {
        "10": {
            "class_type": "ImageOnlyCheckpointLoader",
            "inputs": {"ckpt_name": model_name},
        },
        "11": {
            "class_type": "SVD_img2vid_Conditioning",
            "inputs": {
                "clip_vision": ["10", 1],
                "init_image": init_image_node,
                "vae": ["10", 2],
                "width": width,
                "height": height,
                "video_frames": video_frames,
                "motion_bucket_id": motion_bucket_id,
                "fps": fps,
                "augmentation_level": augmentation_level,
            },
        },
        "12": {
            "class_type": "VideoLinearCFGGuidance",
            "inputs": {"model": ["10", 0], "min_cfg": 1.0},
        },
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": 2.5,
                "sampler_name": "euler",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["12", 0],
                "positive": ["11", 0],
                "negative": ["11", 1],
                "latent_image": ["11", 2],
            },
        },
        "14": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["13", 0], "vae": ["10", 2]},
        },
        "15": {
            "class_type": "SaveAnimatedWEBP",
            "inputs": {
                "images": ["14", 0],
                "filename_prefix": output_prefix,
                "fps": fps,
                "lossless": False,
                "quality": 80,
                "method": "default",
            },
        },
    }


# 给 doctor / discovery 列出可用模板
AVAILABLE_TEMPLATES: tuple[str, ...] = ("sd15", "sd", "sdxl", "svd")


def _detect_template(model_id: str, fmt: str | None, extra: dict[str, Any]) -> str:
    """没有显式 ``extra.workflow_template`` 时，根据 ``model_id`` / 格式启发式推断。"""
    explicit = extra.get("workflow_template") if extra else None
    if explicit and explicit in AVAILABLE_TEMPLATES:
        return explicit
    mid = (model_id or "").lower()
    if "svd" in mid or "video" in mid or "animatediff" in mid:
        return "svd"
    if "xl" in mid or "sdxl" in mid:
        return "sdxl"
    return "sd15"


def render_workflow(
    *,
    op: str,
    model_id: str,
    model_format: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """把 OpenAI 风格 payload → ComfyUI ``/prompt`` body 的 ``prompt`` 字段。

    返回值是 dict（节点 id → 节点定义），调用方负责再包一层
    ``{"prompt": <返回值>, "client_id": ..., "extra_data": ...}`` 发给 ComfyUI。
    """
    extra = payload.get("extra") or {}
    if isinstance(extra, str):
        extra = {}
    template = _detect_template(model_id, model_format, extra)

    width, height = _parse_size(payload.get("size"), default=(1024, 1024))
    steps = int(payload.get("steps", extra.get("steps", 25)))
    cfg = float(payload.get("cfg", extra.get("cfg", 7.0)))
    sampler_name = payload.get("sampler", extra.get("sampler", "dpmpp_2m"))
    scheduler = payload.get("scheduler", extra.get("scheduler", "karras"))
    seed = _pick_seed(payload.get("seed", extra.get("seed", -1)))
    batch_size = int(payload.get("n", extra.get("n", 1)))
    prompt = payload.get("prompt") or ""
    negative = payload.get("negative_prompt") or extra.get("negative_prompt", "")

    output_prefix = (
        os.environ.get("CHAYUAN_COMFYUI_OUTPUT_PREFIX") or "chayuan/output"
    )

    common = dict(
        model_name=model_id,
        prompt=prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        seed=seed,
        batch_size=batch_size,
        output_prefix=output_prefix,
    )

    if template == "svd":
        # T2V：当用户只给了 prompt（没有 init image），我们退回 sd15 + svd 的两段
        # workflow，把 SD 输出当作 SVD 的输入。这是 ComfyUI 社区常见做法。
        sd_part = _build_sd_classic(**common)
        # 用 SD 的 VAEDecode 输出（节点 6）作为 SVD 的 init_image
        svd_part = _build_svd(
            model_name=model_id,
            init_image_node=["6", 0],
            width=width,
            height=height,
            fps=int(payload.get("fps", extra.get("fps", 8))),
            motion_bucket_id=int(extra.get("motion_bucket_id", 127)),
            augmentation_level=float(extra.get("augmentation_level", 0.1)),
            video_frames=int(payload.get("frames", extra.get("frames", 14))),
            seed=seed,
            output_prefix=output_prefix,
        )
        merged = deepcopy(sd_part)
        merged.update(svd_part)
        return merged

    if template == "sdxl":
        return _build_sdxl(**common)

    return _build_sd_classic(**common)
