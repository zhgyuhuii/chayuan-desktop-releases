"""统一的"能力 → 模型"路由层(capability_router)。

设计意图
========
默认模型选择器(UI 上的 9 类)是 **全局唯一真源**;业务代码取模型时
**必须** 通过 ``resolve_model(cap, ...)``,不再各自硬编码或 ad-hoc 读 settings。

能力分类
========
* 用户可选(USER_CHOSEN):chat / t2i / t2v
  - 调用方传 ``user_choice`` 时优先用,否则 fallback 到 default
  - 典型:聊天界面用户选 deepseek vs claude;创意生图选 SDXL vs DALL-E
* 工具型(TOOL):rerank / embedding / clip / ocr / asr / tts
  - **永远使用默认**,即使调用方传 user_choice 也忽略(并 log debug)
  - 典型:KB 检索后重排 / 向量索引 / OCR / 语音转写 / 朗读
  - 用户在 UI 配过后不再让前端用户每次重选 — "选过即用"

接入点(深度分析,见模块底部 docstring 列表)
================================
* RAG 检索后重排                  → resolve_model("rerank")
* 向量库 / KB 索引                → resolve_model("embedding")
* 图像向量检索                     → resolve_model("clip")
* 截图/文档 OCR                    → resolve_model("ocr")
* 录音转写 / 视频字幕              → resolve_model("asr")
* 系统朗读消息 / 语音通知          → resolve_model("tts")

为什么这层的设计很关键
============================
* 无此层 → 每个调用点要 ``Settings.kb_settings.RERANKER_MODEL`` 之类硬编码,
  用户在 UI 改默认后业务代码用的还是旧值
* 有此层 → UI 改 → yaml 改 → 下次 ``resolve_model`` 拿到新值,业务零修改
"""
from __future__ import annotations

import logging
from typing import Optional, Set

logger = logging.getLogger("chayuan.capability_router")

# 用户在每次调用时可以传 user_choice,优先于默认
USER_CHOSEN_CAPABILITIES: Set[str] = {"chat", "t2i", "t2v"}

# 工具型能力 — 永远用默认,user_choice 被忽略
TOOL_CAPABILITIES: Set[str] = {
    "rerank", "embedding", "clip", "ocr", "asr", "tts",
}

# 所有支持的 cap(应与 CAPABILITY_LABELS 一一对应)
ALL_CAPABILITIES: Set[str] = USER_CHOSEN_CAPABILITIES | TOOL_CAPABILITIES


def get_default_model(cap: str) -> Optional[str]:
    """读 ``model_settings.yaml`` 里的 ``DEFAULT_<CAP>_MODEL``。

    返回值:
        - 模型 id(str),如 "deepseek-chat" / "BAAI/bge-reranker-v2-m3"
        - 配置为空或读取失败 → None
    """
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _load_capability_defaults,
        )
        defaults = _load_capability_defaults()
        v = defaults.get(cap)
        return v if v else None
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_router] get_default(%s) failed: %r", cap, e)
        return None


def resolve_model(
    cap: str,
    *,
    user_choice: Optional[str] = None,
) -> Optional[str]:
    """统一路由:决定该用哪个 model id。

    Args:
        cap:         能力名(chat/embedding/rerank/clip/ocr/asr/tts/t2i/t2v)
        user_choice: 用户在调用层传入的覆盖值(如聊天面板里的模型下拉)

    返回:model_id,或 None(默认未配 + user_choice 也未传)。

    路由规则:
    - cap ∈ USER_CHOSEN:user_choice 非空则用 user_choice;否则 default
    - cap ∈ TOOL:**永远用 default**,user_choice 被忽略(log debug)
    """
    if cap in TOOL_CAPABILITIES:
        if user_choice:
            logger.debug(
                "[capability_router] tool cap %s ignores user_choice=%r",
                cap, user_choice,
            )
        return get_default_model(cap)
    # USER_CHOSEN 或未识别的 cap
    if user_choice:
        return user_choice
    return get_default_model(cap)


def set_default_model(cap: str, model_id: str) -> bool:
    """写入 ``model_settings.yaml`` 的 ``DEFAULT_<CAP>_MODEL``。"""
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _save_capability_default,
        )
        ok, _msg = _save_capability_default(cap, model_id)
        return ok
    except Exception as e:  # noqa: BLE001
        logger.exception("[capability_router] set_default(%s) failed: %r", cap, e)
        return False


def is_tool_capability(cap: str) -> bool:
    """判断该 cap 是否是"工具型"(用户没有自主选择权)。"""
    return cap in TOOL_CAPABILITIES


def is_user_chosen_capability(cap: str) -> bool:
    """判断该 cap 是否是"用户可选"(调用方可传 user_choice)。"""
    return cap in USER_CHOSEN_CAPABILITIES


# ============================================================================
# 接入点完整清单(深度分析)— 后续逐步迁移
# ============================================================================
#
# === 必接(影响搜索 / 索引 / 模态识别正确性)===
#
# 1. **rerank** — 知识检索后重排
#    - chayuan/server/chat/kb_chat.py(被注释,需启用)
#    - chayuan/server/retrieval/query/adapters/{document,vector}.py
#      → 实际是 use_rerank=bool 标志,需在 universe/service.py 内部
#        从 capability_router 拿模型并实际跑 rerank
#
# 2. **embedding** — 向量索引 / 检索 / 知识库分块
#    - chayuan/server/utils.py:get_default_embedding()
#      → 应先查 capability_router("embedding"),fallback Settings 默认
#    - chayuan/server/knowledge_source/vector_adapter.py
#    - chayuan/server/kb_query/schemas.py
#
# 3. **clip** — 图像向量检索
#    - chayuan/server/knowledge_source/connector.py(图像向量库)
#    - chayuan/server/modality/* 图像处理
#
# 4. **ocr** — 文档/截图 OCR
#    - chayuan/server/modality/__init__.py / video.py
#      → 多处硬编码 RapidOCR / PaddleOCR,统一走 router
#
# 5. **asr** — 录音转写 / 视频字幕
#    - chayuan/server/modality/audio.py:AudioPipeline.transcribe
#      → 已有 fail-soft 链(faster-whisper / openai-whisper / OpenAI API),
#        但模型名硬编码 "base" / "whisper-1";改用 router 决定
#
# 6. **tts** — 朗读 / 语音通知
#    - chayuan/server/modality/audio.py:AudioPipeline.synthesize
#      → voice 硬编码 "zh-CN-XiaoxiaoNeural",改用 router 决定
#
# === 可选接(用户在调用层有 user_choice 时优先)===
#
# 7. **chat** — 聊天主路径
#    - chayuan/server/utils.py:get_ChatOpenAI(model_name=...)
#      → 调用层默认传 user_choice(用户当前选的模型);未传时回 router default
#
# 8. **t2i / t2v** — 创意生图/生视频
#    - chayuan/server/api_server/openai_routes.py 多模态路径
#
# === 不需接(已有自己的配置体系)===
#
# - Function calling tools(由 chayuan-runtime 路由)
# - Code interpreter(走专用模型)
#
# 全部 9 类 cap 接入完成后,UI 上 9 个默认模型选择器就是后端业务模型选择
# 的**唯一真源**,用户配什么 → 业务用什么,无需改代码。

__all__ = [
    "ALL_CAPABILITIES",
    "USER_CHOSEN_CAPABILITIES",
    "TOOL_CAPABILITIES",
    "resolve_model",
    "get_default_model",
    "set_default_model",
    "is_tool_capability",
    "is_user_chosen_capability",
]
