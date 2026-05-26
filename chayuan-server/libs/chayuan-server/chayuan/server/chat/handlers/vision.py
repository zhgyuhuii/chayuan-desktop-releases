"""Vision 模式：多模态请求由 dispatcher 直接旁路 OpenAI；这里仅占位。

把 vision 作为独立 handler 注册的目的：让 ``list_handlers()`` 与配置面板能发现它，
便于前端做 UI 展示 / 灰度灰度策略；实际生成路径由 dispatcher 在 graph 之外完成。
"""
from __future__ import annotations

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class VisionHandler(BaseModeHandler):
    mode = "vision"
    needs_retrieval = False


register_handler(VisionHandler())
