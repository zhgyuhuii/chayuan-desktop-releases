"""Agent 模式：调用工具 / MCP；生成走 LangGraph create_react_agent 优先。

retrieve 阶段不做检索（agent 自己在生成阶段按需调工具）；
generate_override 暂不接管 —— 沿用 ``node_generate`` 里 N-4 的 ReAct 路径，
保持单一实现位置。后续把 ReAct 流式整改为 ``astream_events`` 时再来这里覆盖。
"""
from __future__ import annotations

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler


class AgentHandler(BaseModeHandler):
    mode = "agent"
    needs_retrieval = False


register_handler(AgentHandler())
