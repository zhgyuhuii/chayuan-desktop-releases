"""统一模态路由层(modality router)。

把 chat / t2i / t2v / image_edit / tts / asr / vl / rerank 等多模态请求
收敛到**同一种事件流**(Vercel AI SDK v5 UI Message Stream Protocol),
按 ``(capability, platform_type)`` 路由到对应 Connector 实现。

设计文档:见 conversation thread + ``protocol.py`` docstring。

模块边界:
  - 本包(``modality.router``):**客户端连接器** —— 用我们当 caller 调上游/本地
    sidecar/HTTP 服务,产物落 artifacts,事件按 v5 协议吐 SSE。
  - 兄弟包 ``modality.audio`` / ``modality.funasr_server`` 等:**服务端 sidecar
    实现** —— 我们当 server 暴露 OpenAI 兼容端点给别人调。两者不混。

PR-1 范围(本次):
  - protocol / sse_v5 / artifacts / connectors.base / router.dispatch
  - artifacts HTTP route
  - **不动 openai_routes.py**,新路径仅在 ``CHAYUAN_MODALITY_NEW_PATH=1`` +
    显式 import 时启用,默认零行为变化。

PR-2 起逐步接 Connector:DashScope t2i / OpenAI DALL-E / 字节豆包 / ...
"""

from chayuan.server.modality.router.protocol import (
    Capability,
    GenerateReq,
    classify_capability,
)
from chayuan.server.modality.router.router import dispatch, dispatch_via_manager, is_enabled
from chayuan.server.modality.router.sse_v5 import (
    sse_encode,
    text_start,
    text_delta,
    text_end,
    file_part,
    data_part,
    error_event,
    finish_event,
    start_event,
)

__all__ = [
    "Capability",
    "GenerateReq",
    "classify_capability",
    "dispatch",
    "dispatch_via_manager",
    "is_enabled",
    "sse_encode",
    "text_start",
    "text_delta",
    "text_end",
    "file_part",
    "data_part",
    "error_event",
    "finish_event",
    "start_event",
]
