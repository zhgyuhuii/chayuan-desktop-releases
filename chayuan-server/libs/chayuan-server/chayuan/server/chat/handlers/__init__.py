"""模式处理器（ModeHandler）插件包。

每个 ChatMode 在这里有一个独立模块（``llm.py`` / ``agent.py`` / ``kb.py`` / ...），
模块顶层调用 :func:`register_handler` 注册自己。``chat.graph.nodes`` 的 retrieve
节点不再写一长串 if/elif，而是 :func:`get_handler(mode).retrieve(state)`。

新增模式 = 在这里加一个文件 + 一个装饰器，0 改动其它文件。

加载策略：
- 包导入时 eager-load 所有内置 handler 模块（执行各自的 ``register_handler`` 调用）
- handler 失败注册（依赖缺失等）只打 debug，不影响其它模式可用性
"""
from __future__ import annotations

import importlib
import logging

from chayuan.server.chat.handlers.base import (
    BaseModeHandler,
    HandlerNotFound,
    get_handler,
    list_handlers,
    register_handler,
)

logger = logging.getLogger("chayuan.chat.handlers")


_BUILTIN_MODULES = (
    "chayuan.server.chat.handlers.llm",
    "chayuan.server.chat.handlers.agent",
    "chayuan.server.chat.handlers.kb",
    "chayuan.server.chat.handlers.file",
    "chayuan.server.chat.handlers.search_engine",
    "chayuan.server.chat.handlers.multi_source",
    "chayuan.server.chat.handlers.vision",
    "chayuan.server.chat.handlers.supervisor",
)


_CORE_MODULES = {
    "chayuan.server.chat.handlers.llm",
    "chayuan.server.chat.handlers.kb",
}


def _load_builtins() -> None:
    """import 所有内置 handler 模块，触发其 ``register_handler``。

    历史教训(2026-05-24):原来打 logger.debug 静默吞 import 异常,frozen 模式
    下 PyInstaller `collect_submodules("chayuan")` 漏抓 supervisor.py,kb
    handler 也跟着没注册,chat 选了 KB 返空 chunks 排查一整天。

    现在的策略:
    - 仅在 FAIL 时 print 到 stdout + logger.warning + 完整 traceback。
    - **核心 handler(llm / kb)注册失败直接 raise**,让服务 fail-loud
      退出,而不是 silent 降级让用户"以为能用"。这两个是日常对话的命脉,
      缺一个等于服务半残。
    - 其它 handler(file / search_engine / multi_source / vision / agent /
      supervisor)失败仍只 warn,允许降级运行。
    """
    import sys
    import traceback as _tb
    for mod in _BUILTIN_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            sys.stdout.write(
                f"[_load_builtins] FAIL  {mod}: {type(e).__name__}: {e}\n"
            )
            _tb.print_exc(file=sys.stdout)
            sys.stdout.flush()
            logger.warning(
                "[handlers] %s 注册失败 (%s: %s)",
                mod, type(e).__name__, e, exc_info=True,
            )
            if mod in _CORE_MODULES:
                raise RuntimeError(
                    f"core chat handler {mod!r} import failed; chayuan-server "
                    f"refuses to start in a half-functional state. Fix the "
                    f"underlying import (typical cause: PyInstaller "
                    f"collect_submodules missed this module — add it to "
                    f"chayuan-server.spec hidden_modules)."
                ) from e


_load_builtins()


__all__ = [
    "BaseModeHandler",
    "HandlerNotFound",
    "get_handler",
    "list_handlers",
    "register_handler",
]
