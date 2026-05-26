"""图像向量化 · 门面（Facade）。

本模块**不再**自己实现具体加载逻辑——那部分已按家族拆到
:mod:`chayuan.server.image_source.loaders` 各子模块。

门面的职责：

1. 维护 :data:`SUPPORTED_MODELS` 目录（UI / KB / evaluator 都读这里）
2. 提供 :func:`get_embedder` 单例工厂（线程安全缓存）
3. 提供 :func:`is_model_ready` 的无副作用依赖探测（不下载、不加载权重）
4. 导出 :class:`ModelSpec` / :class:`EmbedderCapabilities` 让旧代码不破

扩展一个新模型家族，按 ``loaders/__init__.py`` 的说明注册 loader，然后在
``SUPPORTED_MODELS`` 里增条目即可；不再需要改本文件的任何函数。

2024–2026 模型目录（覆盖跨模态与仅视觉两大方向）：

| 家族 | 模型 | dim | 大小 | 跨模态 | 中文 |
|---|---|---:|---:|:---:|:---:|
| SigLIP2 | google/siglip2-base-patch16-224 | 768 | 0.4GB | ✅ | 中 |
| SigLIP2 | google/siglip2-large-patch16-384 | 1024 | 1.4GB | ✅ | 中 |
| SigLIP2 | google/siglip2-so400m-patch14-384 | 1152 | 3.7GB | ✅ | 中 |
| CLIP | openai/clip-vit-large-patch14 | 768 | 1.7GB | ✅ | 弱 |
| Chinese-CLIP | OFA-Sys/chinese-clip-vit-base-patch16 | 512 | 0.4GB | ✅ | **强** |
| Chinese-CLIP | OFA-Sys/chinese-clip-vit-large-patch14-336px | 768 | 1.4GB | ✅ | **强** |
| JinaCLIP | jinaai/jina-clip-v2 | 1024 | 1.0GB | ✅ | 强 |
| EVA-CLIP | QuanSun/EVA-CLIP | 1024 | 5.0GB | ✅ | 弱 |
| OpenCLIP | laion/CLIP-ViT-H-14-laion2B-s32B-b79K | 1024 | 3.9GB | ✅ | 弱 |
| DINOv2 | facebook/dinov2-base | 768 | 0.4GB | ❌ | — |
| DINOv2 | facebook/dinov2-large | 1024 | 1.2GB | ❌ | — |
| DINOv2 | facebook/dinov2-giant | 1536 | 4.4GB | ❌ | — |
| timm | timm/resnet50.a1_in1k | 2048 | 0.1GB | ❌ | — |
| timm | timm/convnext_base.fb_in22k_ft_in1k | 1024 | 0.4GB | ❌ | — |
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.server.image_source.embedder_base import (  # noqa: F401
    BaseImageEmbedder,
    TextEmbeddingNotSupported,
)
from chayuan.server.image_source.embedder_types import (
    CROSSMODAL, IMAGE_ONLY, EmbedderCapabilities, ModelSpec,
)

logger = logging.getLogger("chayuan.image_source.embedder")


# -----------------------------------------------------------------------
# 模型目录（UI / KB 下拉 / evaluator 共用）
# -----------------------------------------------------------------------

SUPPORTED_MODELS: Dict[str, ModelSpec] = {
    # ========================================================================
    # 跨模态 · SigLIP 2（Google 2024-2025 多语言首选）
    # ========================================================================
    "google/siglip2-base-patch16-224": ModelSpec(
        name="google/siglip2-base-patch16-224",
        family="siglip", dim=768, approx_size_mb=400,
        chinese_level="medium", languages="multilingual",
        description="SigLIP 2 base：平衡型默认推荐，400MB，多语言，速度快。",
        capabilities=CROSSMODAL, license="Apache-2.0",
    ),
    "google/siglip2-large-patch16-384": ModelSpec(
        name="google/siglip2-large-patch16-384",
        family="siglip", dim=1024, approx_size_mb=1400,
        chinese_level="medium", languages="multilingual",
        description="SigLIP 2 large：更高精度，1.4GB，384×384 分辨率。",
        capabilities=CROSSMODAL, license="Apache-2.0",
    ),
    "google/siglip2-so400m-patch14-384": ModelSpec(
        name="google/siglip2-so400m-patch14-384",
        family="siglip", dim=1152, approx_size_mb=3700,
        chinese_level="medium", languages="multilingual",
        description="SigLIP 2 so400m：2024 SOTA，3.7GB，14×14 patch，细粒度最强。",
        capabilities=CROSSMODAL, license="Apache-2.0",
    ),
    # ========================================================================
    # 跨模态 · Chinese-CLIP（中文场景最佳）
    # ========================================================================
    "OFA-Sys/chinese-clip-vit-base-patch16": ModelSpec(
        name="OFA-Sys/chinese-clip-vit-base-patch16",
        family="chinese_clip", dim=512, approx_size_mb=400,
        chinese_level="strong", languages="zh+en",
        description="Chinese-CLIP base：中文场景最佳，400MB，亿级中文图文对训练。",
        capabilities=CROSSMODAL, license="Apache-2.0",
    ),
    "OFA-Sys/chinese-clip-vit-large-patch14-336px": ModelSpec(
        name="OFA-Sys/chinese-clip-vit-large-patch14-336px",
        family="chinese_clip", dim=768, approx_size_mb=1400,
        chinese_level="strong", languages="zh+en",
        description="Chinese-CLIP large 336：中文最佳高精度，1.4GB。",
        capabilities=CROSSMODAL, license="Apache-2.0",
    ),
    # ========================================================================
    # 跨模态 · Jina CLIP（89 语言 + 长文）
    # ========================================================================
    "jinaai/jina-clip-v2": ModelSpec(
        name="jinaai/jina-clip-v2",
        family="jina_clip", dim=1024, approx_size_mb=1000,
        chinese_level="strong", languages="multilingual-89",
        description="JinaCLIP v2：89 语言 + 8k 长文本支持，1GB。",
        capabilities=CROSSMODAL, license="CC-BY-NC-4.0",
    ),
    # ========================================================================
    # 跨模态 · OpenAI CLIP（经典）
    # ========================================================================
    "openai/clip-vit-large-patch14": ModelSpec(
        name="openai/clip-vit-large-patch14",
        family="clip", dim=768, approx_size_mb=1700,
        chinese_level="weak", languages="en",
        description="OpenAI CLIP large：英语经典基线，1.7GB。",
        capabilities=CROSSMODAL, license="MIT",
    ),
    # ========================================================================
    # 跨模态 · OpenCLIP（laion 重训，开源 SOTA）
    # ========================================================================
    "laion/CLIP-ViT-H-14-laion2B-s32B-b79K": ModelSpec(
        name="laion/CLIP-ViT-H-14-laion2B-s32B-b79K",
        family="open_clip", dim=1024, approx_size_mb=3900,
        chinese_level="weak", languages="en",
        description="OpenCLIP ViT-H/14：laion-2B 预训练，zero-shot 超越 CLIP 5~10pp。",
        capabilities=CROSSMODAL, license="MIT",
        extra_deps=("open_clip_torch",),
    ),
    "laion/CLIP-ViT-L-14-laion2B-s32B-b82K": ModelSpec(
        name="laion/CLIP-ViT-L-14-laion2B-s32B-b82K",
        family="open_clip", dim=768, approx_size_mb=1700,
        chinese_level="weak", languages="en",
        description="OpenCLIP ViT-L/14：laion-2B 预训练，性能/成本平衡。",
        capabilities=CROSSMODAL, license="MIT",
        extra_deps=("open_clip_torch",),
    ),
    # ========================================================================
    # 仅视觉 · DINOv2（自监督 SOTA 视觉表征）
    # ========================================================================
    "facebook/dinov2-base": ModelSpec(
        name="facebook/dinov2-base",
        family="dinov2", dim=768, approx_size_mb=400,
        chinese_level="weak", languages="n/a",
        description="DINOv2 base：Meta 自监督纯视觉，细节匹配最佳，但不支持文本查图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
    ),
    "facebook/dinov2-large": ModelSpec(
        name="facebook/dinov2-large",
        family="dinov2", dim=1024, approx_size_mb=1200,
        chinese_level="weak", languages="n/a",
        description="DINOv2 large：自监督视觉 SOTA，1.2GB，不支持文本查图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
    ),
    "facebook/dinov2-giant": ModelSpec(
        name="facebook/dinov2-giant",
        family="dinov2", dim=1536, approx_size_mb=4400,
        chinese_level="weak", languages="n/a",
        description="DINOv2 giant：4.4GB 视觉巨模，细粒度检索首选，不支持文本查图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
    ),
    # ========================================================================
    # 仅视觉 · timm 经典 CNN（轻量、ImageNet 预训练）
    # ========================================================================
    "timm/resnet50.a1_in1k": ModelSpec(
        name="timm/resnet50.a1_in1k",
        family="timm_vision", dim=2048, approx_size_mb=100,
        chinese_level="weak", languages="n/a",
        description="ResNet-50 (timm a1_in1k)：100MB，通用视觉骨干，仅以图搜图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
        extra_deps=("timm",),
    ),
    "timm/resnet101.a1_in1k": ModelSpec(
        name="timm/resnet101.a1_in1k",
        family="timm_vision", dim=2048, approx_size_mb=180,
        chinese_level="weak", languages="n/a",
        description="ResNet-101 (timm)：180MB，比 resnet50 精度更高，仅以图搜图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
        extra_deps=("timm",),
    ),
    "timm/convnext_base.fb_in22k_ft_in1k": ModelSpec(
        name="timm/convnext_base.fb_in22k_ft_in1k",
        family="timm_vision", dim=1024, approx_size_mb=400,
        chinese_level="weak", languages="n/a",
        description="ConvNeXt base：Meta ImageNet-22k 预训练，现代 CNN，仅以图搜图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
        extra_deps=("timm",),
    ),
    "timm/efficientnet_b3.ra2_in1k": ModelSpec(
        name="timm/efficientnet_b3.ra2_in1k",
        family="timm_vision", dim=1536, approx_size_mb=50,
        chinese_level="weak", languages="n/a",
        description="EfficientNet-B3：50MB 极轻量，低延迟边缘部署首选，仅以图搜图。",
        capabilities=IMAGE_ONLY, license="Apache-2.0",
        extra_deps=("timm",),
    ),
}


def list_supported_models() -> List[Dict[str, Any]]:
    """扁平化列表；供 REST API 与老代码继续使用。"""
    return [spec.to_dict() for spec in SUPPORTED_MODELS.values()]


# -----------------------------------------------------------------------
# 默认模型解析
# -----------------------------------------------------------------------

def default_model_name() -> str:
    """按用户 settings 或环境变量选取默认模型；缺则回到 SigLIP 2 base。

    遗留入口(返字符串):仍被旧代码使用,行为不变。新代码请用
    :func:`resolve_default` 拿到 (model_id, platform_name) 元组。
    """
    model_id, _platform = resolve_default()
    return model_id


# -----------------------------------------------------------------------
# 89-4:三级配置优先 + Platform 解析
# -----------------------------------------------------------------------

def resolve_default() -> tuple[str, Optional[str]]:
    """89-4:解析图像嵌入默认模型 + 应该用哪个平台。

    返回 ``(model_id, platform_name | None)``:
      * platform_name = ``"infinity-..."``  → 走 :class:`InfinityHttpClient`
      * platform_name = None                → 走 :class:`InProcEmbedderClient`

    优先顺序(89 设计文档第 3.3.2 节):
      1. ``model_settings.yaml`` 的 ``capability_defaults.clip``(89-5 接通)
      2. ``Settings.kb_settings.IMAGE_EMBEDDER`` / ``CHAYUAN_IMAGE_EMBEDDER``
         (老兼容,platform 未知 → 走 in-proc)
      3. 内置 ``google/siglip2-base-patch16-224`` 兜底
    """
    # ---- 优先 1:capability_defaults.clip(89-5 接通) ----
    doc: Dict[str, Any] = {}
    try:
        from chayuan.server.config_panel import yaml_store
        result = yaml_store.load_yaml("model_settings.yaml")
        doc = result.doc if result and result.doc else {}
        cap_def = (doc.get("capability_defaults") or {}).get("clip") or {}
        mid = str(cap_def.get("model_id") or "").strip()
        plat = str(cap_def.get("platform_name") or "").strip() or None
        if mid:
            logger.debug("resolve_default: 优先 1 命中 cap_defaults clip=%s @ %s",
                         mid, plat)
            return mid, plat
    except Exception as e:  # noqa: BLE001
        logger.debug("resolve_default: 读 cap_defaults 失败 %s", e)

    # ---- 优先 1.5:顶层 DEFAULT_IMAGE_EMBEDDING_MODEL(④ tab 旧路径) ----
    # ④ tab 老用户保存时只写顶层 key,89-5 后才同步写 nested。这里兜一下,
    # 顺便反查 platform 让下次 get_client 能识别 Infinity。
    try:
        top_mid = str(doc.get("DEFAULT_IMAGE_EMBEDDING_MODEL") or "").strip()
        if top_mid:
            from chayuan.server.utils import get_config_models
            try:
                models = get_config_models(model_type="image2text")
                info = models.get(top_mid) or {}
                plat = (str(info.get("platform_name") or "").strip() or None)
            except Exception:  # noqa: BLE001
                plat = None
            logger.debug("resolve_default: 优先 1.5 命中 DEFAULT_IMAGE_EMBEDDING_MODEL=%s @ %s",
                         top_mid, plat)
            return top_mid, plat
    except Exception as e:  # noqa: BLE001
        logger.debug("resolve_default: top-key 解析失败 %s", e)

    # ---- 优先 2:老 Settings / 环境变量 ----
    try:
        from chayuan.settings import Settings
        v = str(getattr(Settings.kb_settings, "IMAGE_EMBEDDER", "") or "").strip()
        if v:
            logger.debug("resolve_default: 优先 2 命中 Settings.IMAGE_EMBEDDER=%s", v)
            return v, None
    except Exception:  # noqa: BLE001
        pass
    env_v = os.environ.get("CHAYUAN_IMAGE_EMBEDDER", "").strip()
    if env_v:
        logger.debug("resolve_default: 优先 2 命中 env CHAYUAN_IMAGE_EMBEDDER=%s",
                     env_v)
        return env_v, None

    # ---- 优先 2.5:Infinity 实际加载了哪个 image-embedding 模型,优先用它 ----
    # (用户报 "image-embedder 全部失败 ... siglip2 inproc healthcheck failed":
    #  之前默认 siglip2 但本机没装 → inproc fail。如果 Infinity 装了 chinese-clip
    #  / jina-clip 等,优先用它们,无需让用户主动去 ④ tab 配)
    try:
        from chayuan.server.config_panel.external_runtimes import (
            get_external_runtime,
        )
        from chayuan.server.config_panel.infinity_inventory import (
            get_infinity_models_by_capability,
        )
        # 优先外置 Infinity
        infinity_urls = []
        ext = get_external_runtime("infinity")
        if ext and ext.get("url"):
            infinity_urls.append((str(ext["url"]).rstrip("/"), "infinity-external"))
        # 再问本地 pip
        try:
            from chayuan.server.config_panel.local_infinity_pip import (
                _local_infinity_url, is_local_infinity_running,
            )
            if is_local_infinity_running():
                local = _local_infinity_url()
                if not any(b == local for b, _ in infinity_urls):
                    infinity_urls.append((local, "infinity-local-pip"))
        except Exception:  # noqa: BLE001
            pass
        for base_url, plat in infinity_urls:
            try:
                by_cap = get_infinity_models_by_capability(base_url)
                clip_models = by_cap.get("clip") or []
                if clip_models:
                    pick = clip_models[0]
                    logger.info(
                        "resolve_default: 优先 2.5 用 Infinity 已加载的 image "
                        "model=%s @ %s",
                        pick.model_id, plat,
                    )
                    return pick.model_id, plat
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        logger.debug("resolve_default: infinity inventory 探测失败 %s", e)

    # ---- 优先 3:兜底 SigLIP2(若本机有 PyTorch + 已装权重才能跑) ----
    logger.debug("resolve_default: 兜底 siglip2-base")
    return "google/siglip2-base-patch16-224", None


def is_infinity_platform(platform_name: Optional[str]) -> bool:
    """platform 名以 ``infinity`` 开头(可带后缀如 ``infinity-local``)→ 用 HTTP 客户端。"""
    if not platform_name:
        return False
    return platform_name.lower().startswith("infinity")


def _infinity_base_url(platform_name: str) -> str:
    """从 platform_name 解析 base_url。

    优先级:
      0. (93-3) ``platform_name == "infinity-local-pip"`` → 走本机 pip 默认端口
         (``CHAYUAN_LOCAL_INFINITY_PORT`` 或 7997)
      1. (92-4) ``external_runtimes.yaml`` 里 ``infinity`` 的 url
      2. 环境变量 ``CHAYUAN_INFINITY_BASE_URL``
      3. 约定端口 ``http://127.0.0.1:37997``(docker infinity.yaml 默认 host port)
    """
    plat_lower = (platform_name or "").lower()
    # 0. 本地 pip 平台(93-3)
    if plat_lower == "infinity-local-pip":
        try:
            from chayuan.server.config_panel.local_infinity_pip import (
                _local_infinity_url,
            )
            return _local_infinity_url()
        except Exception:  # noqa: BLE001
            return "http://127.0.0.1:7997"
    # 0.5. 外置(94-2)— 走 external_runtimes 配的 url
    if plat_lower == "infinity-external":
        try:
            from chayuan.server.config_panel.external_runtimes import (
                get_external_runtime,
            )
            ext = get_external_runtime("infinity")
            if ext and ext.get("url"):
                return str(ext["url"]).rstrip("/")
        except Exception:  # noqa: BLE001
            pass

    # 1. external_runtimes
    try:
        from chayuan.server.config_panel.external_runtimes import (
            get_external_runtime,
        )
        item = get_external_runtime("infinity")
        if item:
            base = (item.get("url") or "").strip().rstrip("/")
            if base:
                return base
    except Exception:  # noqa: BLE001
        pass
    return (
        os.environ.get("CHAYUAN_INFINITY_BASE_URL", "").strip()
        or "http://127.0.0.1:37997"
    )


def pick_client(model_id: str, platform_name: Optional[str]):
    """根据 platform 决定用 InfinityHttpClient 还是 InProcEmbedderClient。

    抛 :class:`EmbedderUnavailable` 表示该路径不可用,上层应该尝试 fallback。

    决策矩阵:
      * platform 以 ``infinity`` 开头  → InfinityHttpClient(HTTP)
      * platform 为 None / 空           → InProcEmbedderClient(本地权重)
      * platform 是其它云 API           → 抛 EmbedderUnavailable
        (例:bailian / dashscope / openai 的 qwen-vl-max 是视觉对话模型,
         不是图像嵌入,本期不支持。让上层走 fallback 或返 422 引导)
    """
    from chayuan.server.image_source.embedder_clients import EmbedderUnavailable
    if is_infinity_platform(platform_name):
        from chayuan.server.image_source.embedder_clients.infinity_http import (
            InfinityHttpClient,
        )
        cli = InfinityHttpClient(
            base_url=_infinity_base_url(platform_name or ""),
            model_id=model_id,
        )
        return cli
    if platform_name:
        # 非 infinity 的云 API platform → 当前不支持图像嵌入
        raise EmbedderUnavailable(
            f"云 API platform={platform_name} model={model_id} 不是图像嵌入模型;"
            "请在 ④ 默认模型 → 图像嵌入 选 jina-clip / siglip / chinese-clip 等"
        )
    from chayuan.server.image_source.embedder_clients.inproc import (
        InProcEmbedderClient,
    )
    return InProcEmbedderClient(model_id)


# 89-4:client 单例缓存(与老 _EMBEDDERS 不同名,不混淆)
_CLIENT_CACHE: Dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()


def _emit_client_metric(client_kind: str, status: str) -> None:
    """89-12:image_embedder_calls_total 计数。指标缺席时静默。"""
    try:
        from chayuan.server.observability.metrics import _ensure_metrics
        m = _ensure_metrics()
        c = m.get("image_embedder_calls_total")
        if c is not None:
            c.labels(client_kind=client_kind, status=status).inc()
    except Exception:  # noqa: BLE001
        pass


def _emit_fallback_metric(from_kind: str, to_kind: str) -> None:
    """89-12:fallback 计数。"""
    try:
        from chayuan.server.observability.metrics import _ensure_metrics
        m = _ensure_metrics()
        c = m.get("image_embedder_fallback_total")
        if c is not None:
            c.labels(from_kind=from_kind, to_kind=to_kind).inc()
    except Exception:  # noqa: BLE001
        pass


_NEGATIVE_CACHE: Dict[str, float] = {}   # cache_key → 失败时间戳
_NEGATIVE_CACHE_TTL = 60.0                # 60 秒内不再重试


def get_client():
    """89-4 主入口:返回当前生效的 :class:`ImageEmbedderClient`。

    97 题:**negative cache** — 失败也缓存 60 秒,这期间直接抛 EmbedderUnavailable,
    不再 try inproc + 不再打错误日志,避免无图像嵌入环境下日志刷屏。
    """
    from chayuan.server.image_source.embedder_clients import EmbedderUnavailable
    import time as _t

    model_id, platform = resolve_default()
    cache_key = f"{platform or 'inproc'}::{model_id}"

    with _CLIENT_LOCK:
        cached = _CLIENT_CACHE.get(cache_key)
        if cached is not None and cached.healthcheck():
            return cached

        # negative cache:60 秒内同 cache_key 已失败过 → 直接抛,不再 spam log
        neg_ts = _NEGATIVE_CACHE.get(cache_key)
        if neg_ts is not None and (_t.time() - neg_ts) < _NEGATIVE_CACHE_TTL:
            raise EmbedderUnavailable(
                f"图像嵌入暂不可用(60 秒前已尝试失败,model={model_id} "
                f"platform={platform});请在 ④ 默认模型选已上线的 image 模型"
            )

        # primary 路径
        primary_err: Optional[Exception] = None
        primary_kind = "infinity" if is_infinity_platform(platform) else "inproc"
        try:
            cli = pick_client(model_id, platform)
            if cli.healthcheck():
                _CLIENT_CACHE[cache_key] = cli
                logger.info(
                    "image-embedder primary client ready: kind=%s model=%s platform=%s",
                    cli.kind, cli.model_id, platform,
                )
                _emit_client_metric(cli.kind, "ok")
                return cli
            # 不健康 → 当作失败处理
            cli.close()
            primary_err = RuntimeError(f"{cli.kind} healthcheck failed")
        except EmbedderUnavailable as e:
            primary_err = e
        except Exception as e:  # noqa: BLE001
            primary_err = e

        _emit_client_metric(primary_kind, "error")

        # fallback to in-proc(若 primary 已经是 in-proc 则不用再降级)
        # 92 题修复:platform 不为空(无论 infinity 还是云 API 误配如 bailian),
        # 都尝试 fallback。但 fallback 用 **内置默认**(siglip2),不再用
        # 用户配置的 model_id —— 因为云 API 模型(qwen-vl-max)根本不是图像嵌入,
        # 用它建 InProcEmbedderClient 必抛 KeyError。
        if platform:
            fallback_model = (
                model_id if is_infinity_platform(platform)
                else "google/siglip2-base-patch16-224"
            )
            logger.warning(
                "image-embedder primary=%s 失败 → 降级 inproc(model=%s, "
                "原 platform=%s): %s",
                primary_kind, fallback_model, platform, primary_err,
            )
            try:
                from chayuan.server.image_source.embedder_clients.inproc import (
                    InProcEmbedderClient,
                )
                cli = InProcEmbedderClient(fallback_model)
                if cli.healthcheck():
                    # 同时按 fallback_model 和 primary cache_key 缓存,
                    # 让下次同 (model_id, platform) 输入直接命中,不再重新
                    # 走 fallback 路径反复打 warning。
                    _CLIENT_CACHE[f"inproc::{fallback_model}"] = cli
                    _CLIENT_CACHE[cache_key] = cli
                    _emit_fallback_metric(primary_kind, "inproc")
                    _emit_client_metric("inproc", "fallback")
                    return cli
            except EmbedderUnavailable as e:
                logger.warning("image-embedder fallback 也失败: %s", e)

        # 97 题:写 negative cache,60s 内不再重试 + 不再 spam log
        _NEGATIVE_CACHE[cache_key] = _t.time()
        logger.error(
            "image-embedder 全部失败 model=%s platform=%s err=%s "
            "(后续 60s 内同 key 不再重试)",
            model_id, platform, primary_err,
        )
        raise EmbedderUnavailable(
            f"图像嵌入不可用 — 主路径错误: {primary_err}; "
            "请在模型广场下载图像嵌入模型并启动 Infinity"
        )


def _invalidate_negative_cache() -> None:
    """配置变更后(④ tab 选了新模型 / Infinity 重启)清 negative cache。"""
    _NEGATIVE_CACHE.clear()


def _invalidate_client_cache(model_id: Optional[str] = None) -> None:
    """89-4:配置变更时清空 client 缓存,让下次 get_client 重建。
    97 题:同时清 negative cache,让重试立即生效。
    """
    with _CLIENT_LOCK:
        if model_id:
            for k in list(_CLIENT_CACHE.keys()):
                if k.endswith(f"::{model_id}"):
                    cli = _CLIENT_CACHE.pop(k)
                    try:
                        cli.close()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            for cli in _CLIENT_CACHE.values():
                try:
                    cli.close()
                except Exception:  # noqa: BLE001
                    pass
            _CLIENT_CACHE.clear()
        _NEGATIVE_CACHE.clear()


# -----------------------------------------------------------------------
# 结果类型（老代码留作公开符号）
# -----------------------------------------------------------------------

@dataclass
class ImageEmbeddingResult:
    model_name: str
    dim: int
    vector: Any
    ocr_text: str = ""


# -----------------------------------------------------------------------
# 单例工厂
# -----------------------------------------------------------------------

_EMBEDDERS: Dict[str, BaseImageEmbedder] = {}
_REG_LOCK = threading.Lock()


_BUILTIN_FALLBACK_MODEL = "google/siglip2-base-patch16-224"


class _HttpBackedEmbedder(BaseImageEmbedder):
    """把 :class:`ImageEmbedderClient`(HTTP 异步)包成同步
    :class:`BaseImageEmbedder`,让 connector 老代码 ``embed_image`` /
    ``embed_text`` 单条调用也能透传到 Infinity HTTP。

    维度懒填:首次 encode 后从响应推断;之前 ``store.py`` 维度校验会暂时跳过。
    """

    def __init__(self, client, model_name: str):
        super().__init__(model_name)
        self._client = client
        # CLIP/SigLIP/Jina-CLIP 都是跨模态,默认 capabilities = CROSSMODAL
        from chayuan.server.image_source.embedder_types import CROSSMODAL
        self.capabilities = CROSSMODAL
        # dim 等首次 encode 后从 client.dim 拿
        self.dim = max(int(getattr(client, "dim", 0) or 0), 0)

    def is_available(self) -> bool:
        try:
            return bool(self._client.healthcheck())
        except Exception:  # noqa: BLE001
            return False

    def missing_deps_hint(self) -> str:
        return "需要 Infinity 服务可达;检查 ① 运行时与服务 → Infinity 卡片"

    def _read_bytes(self, src) -> bytes:
        """src: path / bytes / PIL.Image → bytes(jpg/png/...)。"""
        import io
        if isinstance(src, bytes):
            return src
        if isinstance(src, str):
            with open(src, "rb") as f:
                return f.read()
        # PIL.Image
        try:
            buf = io.BytesIO()
            src.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            raise TypeError(f"unsupported image src type: {type(src).__name__}")

    def _post_sync(self, inputs: list) -> list:
        """同步 httpx.post 调 Infinity ``/embeddings``。

        改用同步 httpx 而非走 InfinityHttpClient.encode_*(async),原因:
        connector 是同步代码,如果上层(FastAPI handler)已经在 running event
        loop 中调用,用 ``asyncio.run()`` 会抛 RuntimeError;ThreadPoolExecutor
        切线程也有死锁风险。一次 POST 没必要 async。
        """
        import httpx
        base = self._client.base_url.rstrip("/")
        payload = {"model": self.name, "input": inputs}
        try:
            # 首次 embed 请求会触发 sidecar 懒加载 CLIP / SigLIP 模型(读权重 +
            # torch 初始化),CPU 机器上几十秒很常见,死 30s 必 ReadTimeout。
            # connect 给短(sidecar 真没起来就快速失败),read 给足模型加载时间;
            # 模型加载完后续请求都很快,长 read 不影响正常情况。
            r = httpx.post(
                f"{base}/embeddings", json=payload,
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0),
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Infinity 不可达 ({base}): {type(e).__name__}: {e}"
            ) from e
        if r.status_code != 200:
            raise RuntimeError(
                f"Infinity HTTP {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()["data"]
            return [d["embedding"] for d in data]
        except (KeyError, ValueError) as e:
            raise RuntimeError(
                f"Infinity 响应解析失败: {type(e).__name__}: {e}"
            ) from e

    # Infinity 0.0.x 对 ``/embeddings.input`` 单条字符串限制 122880 字符
    # (= 122KB)。base64 膨胀比 4/3,容纳 ~90KB 的图。
    # CLIP/SigLIP 模型实际只用 224×224 输入,大图必被服务端 resize,所以
    # 客户端提前缩到 512×512 不损失精度,同时保证不撞限制。
    _MAX_INPUT_CHARS = 120000  # 留点 buffer
    _SHRINK_LONG_EDGE = 512

    def _shrink_image(self, blob: bytes) -> bytes:
        """缩图到长边 _SHRINK_LONG_EDGE,JPEG q=85。

        PIL 失败时(非常罕见)→ 返回原 blob,让上层用原 base64 试一次。
        """
        try:
            from PIL import Image
        except ImportError:
            return blob
        import io
        try:
            img = Image.open(io.BytesIO(blob))
            # 转 RGB 处理透明 / palette
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            img.thumbnail(
                (self._SHRINK_LONG_EDGE, self._SHRINK_LONG_EDGE),
                Image.LANCZOS,
            )
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
        except Exception as e:  # noqa: BLE001
            logger.debug("[bridge] _shrink_image failed: %r", e)
            return blob

    def _b64_data_url(self, blob: bytes) -> str:
        """先缩图,再 base64。如果还超限制 → 二次更激进缩。"""
        import base64 as _b64

        # 大图先缩
        if len(blob) > 60_000:  # 60KB 以上才走压缩,小图直接发
            blob = self._shrink_image(blob)

        # 缩完后探测 mime — 几乎都是 jpeg(因为 _shrink_image 输出 JPEG)
        if blob.startswith(b"\x89PNG"):
            mime = "image/png"
        elif blob.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
            mime = "image/webp"
        elif blob.startswith(b"GIF8"):
            mime = "image/gif"
        else:
            mime = "image/jpeg"

        encoded = _b64.b64encode(blob).decode("ascii")
        # 二次保险:如果仍超 Infinity 限制 → 再缩一次更小
        if len(encoded) > self._MAX_INPUT_CHARS:
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(blob))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")
                img.thumbnail((256, 256), Image.LANCZOS)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=70, optimize=True)
                blob = out.getvalue()
                mime = "image/jpeg"
                encoded = _b64.b64encode(blob).decode("ascii")
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[bridge] image still > %d chars after shrink: %d (%r);"
                    " 将原样发送,可能被 Infinity 拒",
                    self._MAX_INPUT_CHARS, len(encoded), e,
                )
        return f"data:{mime};base64,{encoded}"

    def embed_image(self, src):
        blob = self._read_bytes(src)
        vectors = self._post_sync([self._b64_data_url(blob)])
        if not vectors:
            raise RuntimeError("Infinity 返回空向量")
        vec = vectors[0]
        if self.dim == 0:
            self.dim = len(vec)
        try:
            import numpy as _np
            arr = _np.asarray(vec, dtype=_np.float32)
            n = float(_np.linalg.norm(arr))
            return arr / n if n > 0 else arr
        except ImportError:
            return vec

    def embed_text(self, text: str):
        vectors = self._post_sync([str(text)])
        if not vectors:
            raise RuntimeError("Infinity 返回空向量")
        vec = vectors[0]
        if self.dim == 0:
            self.dim = len(vec)
        try:
            import numpy as _np
            arr = _np.asarray(vec, dtype=_np.float32)
            n = float(_np.linalg.norm(arr))
            return arr / n if n > 0 else arr
        except ImportError:
            return vec


def _try_http_backed_embedder(model_name: str):
    """如果 model_name 在 Infinity inventory 里能找到,返回一个 HTTP 桥接 embedder。

    检测顺序(与 _lookup_platform_for_model 一致):
      A. external_runtimes.yaml infinity URL → fetch /v1/models 或 /models
      B. local pip Infinity 默认端口

    任一命中即返 :class:`_HttpBackedEmbedder`;否则返 None 让原 PyTorch 路径处理。
    """
    if not model_name:
        return None
    try:
        from chayuan.server.config_panel.external_runtimes import (
            get_external_runtime,
        )
        from chayuan.server.config_panel.infinity_inventory import (
            fetch_infinity_models,
        )

        candidates = []  # [(base_url, platform_name)]
        ext = get_external_runtime("infinity")
        if ext and ext.get("url"):
            candidates.append((str(ext["url"]).rstrip("/"), "infinity-external"))

        try:
            from chayuan.server.config_panel.local_infinity_pip import (
                _local_infinity_url, is_local_infinity_running,
            )
            if is_local_infinity_running():
                local_base = _local_infinity_url()
                if not any(b == local_base for b, _ in candidates):
                    candidates.append((local_base, "infinity-local-pip"))
        except Exception:  # noqa: BLE001
            pass

        for base_url, platform in candidates:
            try:
                models = fetch_infinity_models(base_url)
            except Exception:  # noqa: BLE001
                continue
            for m in models:
                if m.model_id == model_name:
                    # 命中 → 实例化 InfinityHttpClient + HTTP 桥接
                    from chayuan.server.image_source.embedder_clients.infinity_http import (
                        InfinityHttpClient,
                    )
                    cli = InfinityHttpClient(
                        base_url=base_url, model_id=model_name,
                    )
                    if cli.healthcheck():
                        return _HttpBackedEmbedder(cli, model_name)
                    cli.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[get_embedder] HTTP probe failed: %r", e)
    return None


def _detect_image_family(model_dir: Path) -> str:
    """读 config.json 的 model_type + 目录名,推断 image embedder family。"""
    mt = ""
    try:
        import json as _json
        cfg = _json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        mt = str(cfg.get("model_type") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    nm = (mt + " " + model_dir.name).lower()
    if "chinese" in nm and "clip" in nm:
        return "chinese_clip"
    if "jina" in nm and "clip" in nm:
        return "jina_clip"
    if "siglip" in nm:
        return "siglip"
    # openai/clip-vit-* 的 config model_type 就是 "clip";未知也按 clip 试
    return "clip"


def _resolve_local_model_spec(name: str) -> Optional[ModelSpec]:
    """``name`` 指向一个**本地模型目录**(bundled 自带 / 用户放的)时,构造一个
    ModelSpec 直接按路径加载 —— 不要求它出现在 SUPPORTED_MODELS。

    name 形如 ``models/bundled/image/clip-vit-base-patch32``(相对 CHAYUAN_ROOT)
    或绝对路径。目录里有 config.json 才算可加载的 HF 模型;family 按
    config.json 的 model_type / 目录名推断,交给对应 loader 按路径加载。
    """
    if not name:
        return None
    cands: List[Path] = []
    p = Path(name)
    if p.is_absolute():
        cands.append(p)
    else:
        try:
            from chayuan.settings import CHAYUAN_ROOT
            cands.append(Path(CHAYUAN_ROOT) / name)
        except Exception:  # noqa: BLE001
            pass
        cands.append(p)
    for d in cands:
        try:
            if d.is_dir() and (d / "config.json").is_file():
                return ModelSpec(
                    name=str(d), family=_detect_image_family(d), dim=512,
                    approx_size_mb=0, chinese_level="medium",
                    languages="multilingual",
                    description=f"本地模型目录 {d.name}",
                    capabilities=CROSSMODAL,
                )
        except Exception:  # noqa: BLE001
            continue
    return None


def _get_inproc_embedder(model_name: Optional[str] = None) -> BaseImageEmbedder:
    """Plan 1 时期的 in-process loader 路径(原 get_embedder 函数体)。

    Plan 3D 起新代码请用 :func:`get_embedder`;它会先探 sidecar(Plan 3D),
    sidecar 不 ready 时落回这里。

    92 hotfix:旧 fallback 链(``default_model_name()``)在用户 ④ tab 选了
    云 API 视觉模型(如 ``qwen-vl-max`` / ``openai/clip-vit-base-patch32`` 不在
    SUPPORTED_MODELS)时,会无限拿到同一个错误名字 → ``SUPPORTED_MODELS[name]``
    KeyError 死循环。改为:
      * 第一选 model_name(参数)
      * 不在表 → 试 ``default_model_name()``(④ tab 用户选的)
      * 仍不在表 → 用 **内置 SigLIP2** 作为最终兜底
    """
    from chayuan.server.image_source.loaders import create_embedder

    name = (model_name or default_model_name()).strip()

    # 0. 优先 HTTP 桥接 — 模型在 Infinity inventory 里能找到 → 走 HTTP,
    # 完全不需要本地 PyTorch 加载。这样云端 vendor 模型如 openai/clip-vit-base-patch32
    # 也能用(只要 Infinity 容器加载了它)。
    bridge = _try_http_backed_embedder(name)
    if bridge is not None:
        with _REG_LOCK:
            _EMBEDDERS[name] = bridge
        logger.info("get_embedder: %s → HTTP 桥接(%s)", name, bridge._client.base_url)
        return bridge

    # 本地模型目录(bundled 自带 / 用户放的)—— 直接按路径加载,不要求在
    # SUPPORTED_MODELS。修:bundled image 模型(如 clip-vit-base-patch32)的
    # model_id 是 'models/bundled/image/...' 路径,不在 SUPPORTED_MODELS,
    # 之前被误判"未知模型"→ 回退 SigLIP2 → SigLIP2 没下载 → 失败。
    local_spec = _resolve_local_model_spec(name)
    if local_spec is not None:
        with _REG_LOCK:
            cached = _EMBEDDERS.get(name)
            if cached is not None:
                return cached
            emb = create_embedder(local_spec)
            _EMBEDDERS[name] = emb
        logger.info(
            "get_embedder: %s → 本地模型目录加载(%s, family=%s)",
            name, local_spec.name, local_spec.family,
        )
        return emb

    if name not in SUPPORTED_MODELS:
        logger.warning(
            "未知图像模型 %s,该模型不在本地 SUPPORTED_MODELS 列表"
            "(也不是本地模型目录、Infinity inventory 也没找到);"
            "回退到内置默认 %s 做图像嵌入",
            name, _BUILTIN_FALLBACK_MODEL,
        )
        name = _BUILTIN_FALLBACK_MODEL
    if name not in SUPPORTED_MODELS:
        # 真兜底也找不到 — 抛一个明确错误给上层 422
        from chayuan.server.image_source.embedder_clients.base import (
            EmbedderUnavailable,
        )
        raise EmbedderUnavailable(
            f"内置兜底模型 {name} 也不在 SUPPORTED_MODELS;"
            f"可选: {list(SUPPORTED_MODELS.keys())[:5]}"
        )
    with _REG_LOCK:
        cached = _EMBEDDERS.get(name)
        if cached is not None:
            return cached
        emb = create_embedder(SUPPORTED_MODELS[name])
        _EMBEDDERS[name] = emb
        return emb


def get_embedder(model_name: Optional[str] = None) -> BaseImageEmbedder:
    """Plan 3D: sidecar 首选 + in-process fallback。

    流程:
      1) 试 local_runtime_registry.get('image-embedding').status,若 state=ready
         返回 InfinityHttpClient(base_url=endpoint, model_id=model_name or default)
      2) registry 不可用 / state != ready → fallback _get_inproc_embedder(Plan 1 原路径:
         先试 Infinity inventory HTTP 桥接,再走 in-process loader)
    """
    # 1) sidecar 优先
    try:
        from chayuan.server.model_registry.local_runtime_registry import get_registry
        mgr = get_registry().get("image-embedding")
        if mgr.status.state == "ready" and mgr.status.endpoint:
            from chayuan.server.image_source.embedder_clients.infinity_http import InfinityHttpClient
            name = (model_name or default_model_name()).strip()
            client = InfinityHttpClient(
                base_url=mgr.status.endpoint,
                model_id=name,
            )
            # 关键:InfinityHttpClient 实现的是 ImageEmbedderClient(async encode_*) 协议,
            # 没有 connector / pipeline 老代码需要的同步 embed_image / embed_text。
            # 必须套 _HttpBackedEmbedder 包装层(已有的 BaseImageEmbedder 实现),
            # 它内部用同步 httpx 调 /embeddings,暴露 embed_image / embed_text。
            # 历史 bug:之前直接 return client → 'InfinityHttpClient' object has no attribute 'embed_image'。
            wrapped = _HttpBackedEmbedder(client, name)
            logger.info("get_embedder: sidecar 命中 %s @ %s", name, mgr.status.endpoint)
            return wrapped
    except Exception as e:  # noqa: BLE001
        logger.debug("[get_embedder] sidecar 路径不可用,fallback in-process: %r", e)

    # 2) Plan 1 原 in-process 路径
    return _get_inproc_embedder(model_name)


def _invalidate_embedder_cache(name: Optional[str] = None) -> None:
    """供 model_manager 在 download / delete 时调用，强制下次重新加载。"""
    with _REG_LOCK:
        if name:
            _EMBEDDERS.pop(name, None)
        else:
            _EMBEDDERS.clear()


# -----------------------------------------------------------------------
# 依赖探测（无副作用、不联网、不加载权重）
# -----------------------------------------------------------------------

def _model_cache_root() -> Path:
    """优先用户配置的本地模型目录，否则 HF 默认缓存。"""
    base = os.environ.get("CHAYUAN_ROOT")
    if base:
        p = Path(base) / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))


def is_model_ready(model_name: str) -> Dict[str, Any]:
    """无副作用地探测模型是否可用。

    返回 ``{"available": bool, "reason": str, "install_hint": str, "cached": bool}``；
    兼容老签名（老代码只看 ``available`` 与 ``reason``）。

    这里不直接加载权重——只问 loader"你具备依赖吗"，并用 HF cache 规则检查磁盘。
    """
    from chayuan.server.image_source.loaders import create_embedder

    spec = SUPPORTED_MODELS.get(model_name)
    if spec is None:
        return {"available": False, "reason": f"未知模型 {model_name}",
                "install_hint": "", "cached": False}

    # 依赖探测（不加载权重）
    try:
        emb = create_embedder(spec)
        ok = bool(emb.is_available())
        hint = emb.missing_deps_hint() if not ok else ""
    except Exception as e:  # noqa: BLE001
        ok, hint = False, ""
        return {
            "available": False, "reason": f"loader 初始化失败：{e}",
            "install_hint": hint, "cached": False,
        }

    # 磁盘缓存探测（HF 规范）
    cache_root = _model_cache_root()
    cached = False
    try:
        cached = any(
            cache_root.rglob(f"**/*{model_name.split('/')[-1]}*/snapshots")
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "available": ok,
        "reason": "" if ok else (
            f"缺少运行时依赖，请执行：{hint}" if hint else
            "运行时依赖不满足"
        ),
        "install_hint": hint,
        "cached": bool(cached),
    }


__all__ = [
    "SUPPORTED_MODELS",
    "ModelSpec",
    "EmbedderCapabilities",
    "BaseImageEmbedder",
    "TextEmbeddingNotSupported",
    "ImageEmbeddingResult",
    "default_model_name",
    # 89-4 新入口
    "resolve_default",
    "is_infinity_platform",
    "pick_client",
    "get_client",
    "_invalidate_client_cache",
    "list_supported_models",
    "get_embedder",
    "is_model_ready",
    "_invalidate_embedder_cache",
]
