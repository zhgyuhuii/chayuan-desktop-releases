"""LLM 模式：纯对话；不检索、不接管生成。"""
from __future__ import annotations

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class LLMHandler(BaseModeHandler):
    mode = "llm"
    needs_retrieval = False


register_handler(LLMHandler())
