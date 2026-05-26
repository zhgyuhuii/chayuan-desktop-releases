"""Capability enum + GenerateReq + 模型名 → capability 推断。

事件类型本身放 ``sse_v5.py``;本文件只放跨模态共享的输入/分类结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Capability(str, Enum):
    """与后端 ``config_panel.CAPABILITY_LABELS`` 一一对应,**字符串值就是 panel cap**。"""
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    T2I = "t2i"             # 文生图
    T2V = "t2v"             # 文生视频
    IMAGE_EDIT = "image_edit"
    TTS = "tts"             # 文字转语音
    ASR = "asr"             # 语音转文字
    VL = "vl"               # 视觉语言(多模态 chat)— 复用 CHAT 路径,但 content 必须 list
    OCR = "ocr"


# ──────────────────────────────────────────────────────────────────────
# 模型名 → capability 推断(同 chat.py 里 ``classify_non_chat_model`` 同一份真源)
# ──────────────────────────────────────────────────────────────────────
#
# 命名规则:阿里百炼官方文档 + 业界惯例:
#   qwen-image-*           → t2i
#   qwen-image-edit-*      → image_edit
#   wanx-*  / wanxiang-*   → t2i
#   qwen-vl-* / qvq-*      → vl (走 chat 路径,但要 multimodal content)
#   qwen-omni-* / qwen-audio-* → vl
#   qwen-asr-* / qwen3-asr-* / paraformer-* / whisper-* → asr
#   qwen-tts-* / cosyvoice-* / sambert-* → tts
#   text-embedding-* / embedding-* → embedding
#   gte-rerank-* / bge-reranker-* → rerank
#   gpt-image-* / dall-e-* → t2i (OpenAI)
#   sora-* → t2v
#   stable-video-diffusion / svd / runway / kling / wan-2.1 / wanxiang-t2v → t2v
#
# 命中前缀就强分类;命中不到一律视为 chat。
_PREFIX_TO_CAP: List[tuple[str, Capability]] = [
    # 顺序敏感:更精确的前缀写前面(qwen-image-edit 优先于 qwen-image)
    ("qwen-image-edit",        Capability.IMAGE_EDIT),
    ("qwen-image",             Capability.T2I),
    ("gpt-image",              Capability.T2I),
    ("dall-e",                 Capability.T2I),
    ("wanxiang-t2v",           Capability.T2V),
    ("wanx-t2v",               Capability.T2V),
    ("wanx",                   Capability.T2I),
    ("wanxiang",               Capability.T2I),
    ("sora",                   Capability.T2V),
    ("stable-video",           Capability.T2V),
    ("svd",                    Capability.T2V),
    ("runway",                 Capability.T2V),
    ("kling",                  Capability.T2V),
    ("wan-2",                  Capability.T2V),
    ("qvq-",                   Capability.VL),
    ("qwen-vl",                Capability.VL),
    ("qwen-omni",              Capability.VL),
    ("qwen-audio",             Capability.VL),
    ("paraformer",             Capability.ASR),
    ("whisper",                Capability.ASR),
    ("qwen-asr",               Capability.ASR),
    ("qwen3-asr",              Capability.ASR),
    ("funasr",                 Capability.ASR),
    ("gpt-4o-transcribe",      Capability.ASR),
    ("gpt-4o-mini-transcribe", Capability.ASR),
    ("qwen-tts",               Capability.TTS),
    ("qwen3-tts",              Capability.TTS),
    ("cosyvoice",              Capability.TTS),
    ("sambert",                Capability.TTS),
    ("voxcpm",                 Capability.TTS),
    # ★ rerank 必须放 embedding 前面 — bge-reranker 否则会被 bge- 吞
    ("bge-reranker",           Capability.RERANK),
    ("gte-rerank",             Capability.RERANK),
    ("cohere-rerank",          Capability.RERANK),
    ("jina-rerank",            Capability.RERANK),
    ("text-embedding-",        Capability.EMBEDDING),
    ("embedding-",             Capability.EMBEDDING),
    ("text-embedding",         Capability.EMBEDDING),
    ("bge-",                   Capability.EMBEDDING),   # bge-m3 / bge-small 等
    ("rapidocr",               Capability.OCR),
    ("paddleocr",              Capability.OCR),
    ("pp-ocr",                 Capability.OCR),
]


def classify_capability(model_name: str) -> Capability:
    """按模型名前缀推断 capability。

    匹配不到一律视为 ``CHAT``,保留对未知模型的最大兼容(老路径接得住)。
    bge-reranker / gte-rerank 这类专有 rerank 模型不会被前缀 ``bge-`` 误吞,
    因为 list 顺序把 reranker 前缀放在 ``bge-`` 之前。
    """
    if not model_name:
        return Capability.CHAT
    low = model_name.lower().strip()
    # 兼容 'provider/model' 写法
    if "/" in low:
        low = low.split("/", 1)[1]
    for prefix, cap in _PREFIX_TO_CAP:
        if low.startswith(prefix):
            return cap
    return Capability.CHAT


# ──────────────────────────────────────────────────────────────────────
# 模型名 → Connector vendor 推断
# ──────────────────────────────────────────────────────────────────────
#
# 为什么需要:用户 model_platform 表里 ``platform_type`` 可能是 ``openai``
# (因为 bailian 配的是 OpenAI 兼容 ``/compatible-mode/v1`` 端点用来跑 chat)。
# 但同一个 platform 上的 ``qwen-image-2.0`` / ``qwen3-tts-flash`` 这类 DashScope
# 专属模型并不在 compat-mode 上发布,必须走 native ``/api/v1/services/aigc/...``。
#
# 单看 ``platform_type`` 选 Connector 会跑偏(把 qwen-image-2.0 路由到
# OpenAIT2IConnector 然后撞 404)。**模型名前缀**才是上游协议的真源。
#
# 表填模型名族 → vendor;router pick_connector 时先看这个,再回退 platform_type。
_PREFIX_TO_VENDOR: List[tuple[str, str]] = [
    # DashScope 系
    ("qwen-image-edit",  "dashscope"),
    ("qwen-image",       "dashscope"),
    ("qwen-tts",         "dashscope"),
    ("qwen3-tts",        "dashscope"),
    ("qwen-asr",         "dashscope"),
    ("qwen3-asr",        "dashscope"),
    ("qwen-vl",          "dashscope"),
    ("qwen-omni",        "dashscope"),
    ("qwen-audio",       "dashscope"),
    ("qvq-",             "dashscope"),
    ("wanxiang-t2v",     "dashscope"),
    ("wanx-t2v",         "dashscope"),
    ("wanx",             "dashscope"),
    ("wanxiang",         "dashscope"),
    ("wan-2",            "dashscope"),
    ("paraformer",       "dashscope"),
    ("cosyvoice",        "dashscope"),
    ("sambert",          "dashscope"),
    # OpenAI 系(及 OpenAI-兼容代理:OpenRouter / Together / Fireworks)
    ("dall-e",           "openai"),
    ("gpt-image",        "openai"),
    ("gpt-4o-mini-tts",  "openai"),
    ("tts-1",            "openai"),
    ("whisper",          "openai"),
    ("gpt-4o-transcribe", "openai"),
    ("gpt-4o-mini-transcribe", "openai"),
    ("sora",             "openai"),
    # 其它专有平台 — 后续接入时补
    ("stable-video",     "stability"),
    ("svd",              "stability"),
    ("runway",           "runway"),
    ("kling",            "kling"),
]


def classify_vendor(model_name: str) -> Optional[str]:
    """按模型名前缀推断 Connector vendor;命中不到返 None,router 回退看 platform_type。

    例:
        qwen-image-2.0       → 'dashscope'
        qwen3-tts-flash      → 'dashscope'
        dall-e-3             → 'openai'
        gpt-4o-mini-tts      → 'openai'
        deepseek-chat        → None(让 router 看 platform_type)
    """
    if not model_name:
        return None
    low = model_name.lower().strip()
    if "/" in low:
        low = low.split("/", 1)[1]
    for prefix, vendor in _PREFIX_TO_VENDOR:
        if low.startswith(prefix):
            return vendor
    return None


# ──────────────────────────────────────────────────────────────────────
# GenerateReq:Connector.generate 的统一输入
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Attachment:
    """用户附带的图像/音频/文件 — 用于 image_edit / vl / asr 等场景。"""
    mime: str
    url: Optional[str] = None         # 已上传到 artifacts 的 URL(优先)
    data: Optional[bytes] = None      # 内存数据(兜底)
    name: Optional[str] = None


@dataclass
class GenerateReq:
    """各 Connector 收到的统一请求。

    capability 由 router 在 dispatch 前用 ``classify_capability(model)`` 算好。
    平台元信息(``platform_type`` / ``api_base`` / ``api_key``)由 router 从
    ``model_platform_repository`` 拉,Connector 不直接读 DB。
    """
    capability: Capability
    model: str
    platform_name: str
    platform_type: str
    api_base: str
    api_key: str
    prompt: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    # 取消信号:router 持有,Connector 在轮询/长循环里 check
    cancelled: Optional["Cancelled"] = None
    # PR-9 E:服务进程重启后,把以前已经提交给上游的异步任务接着轮询。
    # 这里填的是上游真实 task id(wanx t2v / paraformer-async 等);TaskManager
    # 检测到 != None 时,调 connector.resume(req, upstream_task_id) 跳过 submit。
    resume_upstream_task_id: Optional[str] = None


class Cancelled:
    """简易取消信号 — Connector 通过 ``req.cancelled.is_set()`` 检查。"""
    def __init__(self) -> None:
        self._flag = False

    def set(self) -> None:
        self._flag = True

    def is_set(self) -> bool:
        return self._flag
