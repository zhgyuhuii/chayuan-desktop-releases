"""统一 ChatGraph 子系统（P1-6）。

目的：把 `/chat/*` 下散落在 4 个文件里的编排逻辑（chat / kb_chat / file_chat /
search_engine_chat）收敛到一张 LangGraph StateGraph 上。

- 原子能力（retrieve / generate / tool）作为**节点**
- 流式输出统一走 `.astream_events(v2)` → SSE 适配器
- 治理 / 观测 / 审计 / guardrail 全部作为 graph 节点插入，不再散落在 iterator 代码里

对外 API（兼容）：
- ``run_chat_stream(request) -> AsyncIterator[SSE Dict]``：新管线
- ``run_chat_sync(request) -> dict``：非流式

老 handler `chat.py:chat()` 仍保留；路由层按 feature flag
``Settings.basic_settings.USE_CHAT_GRAPH`` 决定走哪条，便于灰度切换。
"""

from chayuan.server.chat.graph.state import ChatMode, ChatRequest, ChatState  # noqa: F401
from chayuan.server.chat.graph.runner import run_chat_stream, run_chat_sync  # noqa: F401
