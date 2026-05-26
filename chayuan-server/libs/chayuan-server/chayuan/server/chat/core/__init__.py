"""统一聊天调度核（chat.core）。

把散落在 ``chat_routes.chat_completions`` / ``kb_chat`` / ``file_chat`` /
``multi_source_chat`` / ``openapi.chat_complete`` 五条路径里的：
- 用户/游客/App 三态鉴权
- 多种 body 形态（OpenAI / 自家 / 多源）
- mode 解析（vision / agent / kb / file / search_engine / multi_source / llm）
- SSE 协议
- 治理 / 血缘 / 配额 / 语义缓存

收敛到一组无状态对象上。**对外只暴露 3 个符号**：

- :class:`AuthContext`     —— 三态身份的统一描述（principal_id / role / app_id）
- :class:`UnifiedChatRequest` —— 各端 body 的归一 DTO
- :func:`dispatch_chat`    —— 单一入口；返回 SSE 响应或非流式 dict

路由层只负责做协议适配（HTTP body → DTO），不再包含编排逻辑。

设计原则：
1. 完全无状态；所有可变状态进 ``ChatGraph`` state
2. 不引新依赖；纯组合现有 ``chat.graph`` + ``utils`` 子系统
3. 统一调度为默认且唯一链路（T5 已完成灰度收敛）；运维可通过
   ``CHAYUAN_DISABLE_CHAT_DISPATCHER=1`` 环境变量临时旁路
"""

from chayuan.server.chat.core.auth_context import AuthContext, AuthMode
from chayuan.server.chat.core.dispatcher import dispatch_chat, is_dispatcher_enabled
from chayuan.server.chat.core.request_dto import (
    UnifiedChatRequest,
    from_openai_body,
    from_kb_body,
    from_file_body,
    from_multi_source_body,
)

__all__ = [
    "AuthContext",
    "AuthMode",
    "UnifiedChatRequest",
    "dispatch_chat",
    "is_dispatcher_enabled",
    "from_openai_body",
    "from_kb_body",
    "from_file_body",
    "from_multi_source_body",
]
