"""处理器基类 + 注册表。

设计要点：
- 所有 handler 都是**无状态**的；可以多协程并发调用
- 输入是 ``ChatState`` / ``ChatRequest``，输出是节点 dict 增量（与 LangGraph
  约定一致）；这样可直接挂回 graph 节点，不需要中间转换层
- 所有 IO 必须 async；同步操作请显式 ``run_in_threadpool``
- 每个 handler 必须实现 :meth:`retrieve`；可选实现 :meth:`generate_override`
  来覆盖默认 LLM 生成逻辑（例如 agent 走 ReAct）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.chat.handlers.base")


class HandlerNotFound(KeyError):
    """请求的 mode 没有注册 handler。"""


class BaseModeHandler:
    """所有 ModeHandler 的接口。子类必须设置 ``mode`` 属性。"""

    mode: str = ""
    needs_retrieval: bool = False

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """检索阶段；返回节点增量（``retrieved_chunks`` / ``retrieved_sources_meta``）。

        默认实现：什么都不做。纯对话 / 多模态等模式直接继承默认实现。
        """
        return {
            "retrieved_chunks": [],
            "retrieved_sources_meta": [],
            "retrieval_elapsed_ms": 0,
        }

    async def generate_override(
        self, state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """可选的生成覆盖；返回 None 表示走默认 LLM 路径。

        - agent 模式可以在这里跑 ReAct，把 final answer 写进 state["answer"]
        - vision 由 dispatcher 直接旁路 OpenAI，不会进 generate
        - 其它模式继续返回 None，由 ``node_generate`` / runner 流式接管
        """
        return None


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, BaseModeHandler] = {}


def register_handler(handler: BaseModeHandler) -> BaseModeHandler:
    """注册一个 handler。同名后注册会覆盖前者，并 warn 一条日志。

    返回 handler 本身，方便用作装饰器:

    .. code-block:: python

        @register_handler
        class MyHandler(BaseModeHandler):
            mode = "custom"
    """
    if not handler.mode:
        raise ValueError(f"handler {handler.__class__.__name__} 必须设置 mode 属性")
    if handler.mode in _REGISTRY:
        logger.warning(
            "ModeHandler 覆盖注册：mode=%s old=%r new=%r",
            handler.mode, _REGISTRY[handler.mode].__class__.__name__,
            handler.__class__.__name__,
        )
    _REGISTRY[handler.mode] = handler
    return handler


def get_handler(mode: str) -> BaseModeHandler:
    """按 mode 取 handler；找不到则抛 :class:`HandlerNotFound`。"""
    if mode not in _REGISTRY:
        raise HandlerNotFound(f"no handler registered for mode={mode!r}")
    return _REGISTRY[mode]


def list_handlers() -> List[str]:
    """已注册的 mode 列表，按字母序。"""
    return sorted(_REGISTRY.keys())
