"""Supervisor 模式（T11）：三 Agent 协作；retrieve 不做，让子 Agent 自行调工具。"""
from __future__ import annotations

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class SupervisorHandler(BaseModeHandler):
    mode = "supervisor"
    needs_retrieval = False


register_handler(SupervisorHandler())
