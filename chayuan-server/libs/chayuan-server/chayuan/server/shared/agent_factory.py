"""统一 Agent 工厂（N-4 / N-7 基础）。

四种工厂函数，返回 LangGraph 编译后的 runnable，可直接 ``.astream_events(v2)``：

- ``build_react_agent(tools, model)``：LangGraph prebuilt ReAct；替代老 PlatformToolsRunnable
- ``build_supervisor_agent(sub_agents, model)``：监督者模式（N-7）；按 spec 分派
- ``build_plain_agent(model)``：不带工具，直接 LLM；用于 ChatGraph.generate 的统一封装
- ``build_tool_calling_agent(tools, model)``：OpenAI function calling 风格，并发 tool

失败降级：LangGraph 未装 / 模型不支持 function calling → 回退 ``build_plain_agent``。

各函数都接受 ``callbacks``；会自动注入 Langfuse handler（单例，离线 / 断网安全）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.shared.agent_factory")


def _llm_with_callbacks(model: Optional[str], **kw):
    from chayuan.server.observability.langfuse_integration import (
        inject_into_callbacks,
    )
    from chayuan.server.utils import get_ChatOpenAI, get_default_llm
    m = (model or get_default_llm()).strip()
    cbs = inject_into_callbacks([])
    # 调用方(如 build_plain_agent 在 nodes.generate 回退路径)可能显式传 streaming=False;
    # 用 setdefault 让调用方优先,避免 "got multiple values for keyword argument 'streaming'"。
    kw.setdefault("streaming", True)
    return get_ChatOpenAI(
        model_name=m,
        callbacks=cbs or None, **kw,
    )


def build_plain_agent(*, model: Optional[str] = None, temperature: float = 0.3, **kw):
    """最小：返回 LLM 自身；``.astream_events`` 原生支持。"""
    return _llm_with_callbacks(model, temperature=temperature, **kw)


def build_react_agent(
    *, tools: List[Any], model: Optional[str] = None, temperature: float = 0.2,
    system_prompt: Optional[str] = None,
    checkpointer: Optional[Any] = None,
    interrupt_before: Optional[List[str]] = None,
    interrupt_after: Optional[List[str]] = None,
    **kw,
):
    """用 langgraph.prebuilt.create_react_agent 建 ReAct agent。

    相比老 ``PlatformToolsRunnable``：
    - 原生 parallel tool calls
    - 原生 Human-in-the-loop interrupt（配合 checkpointer + interrupt_before）
    - .astream_events(v2) 统一流式（无需自定义 callback）

    - ``checkpointer``：传入 LangGraph checkpointer 实例（内存 / Postgres / SQLite），
      开启会话恢复 + HIL；未传且 ``CHAYUAN_AGENT_CHECKPOINTER=postgres|memory`` 时
      自动懒建
    - ``interrupt_before / interrupt_after``：节点名（如 ``['tools']``）触发中断，
      由上层 UI 显示"待审批"并通过 ``graph.update_state`` + ``astream`` 续跑

    未装 langgraph 时回退到 build_plain_agent（无 tool 能力）。
    """
    try:
        from langgraph.prebuilt import create_react_agent
    except Exception as e:  # noqa: BLE001
        logger.warning("langgraph 未装，ReAct agent 回退为 plain LLM：%r", e)
        return build_plain_agent(model=model, temperature=temperature, **kw)

    llm = _llm_with_callbacks(model, temperature=temperature, **kw)
    if checkpointer is None:
        checkpointer = _default_checkpointer()
    kwargs: Dict[str, Any] = {
        "model": llm, "tools": tools or [],
        "prompt": system_prompt or None,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    if interrupt_before:
        kwargs["interrupt_before"] = list(interrupt_before)
    if interrupt_after:
        kwargs["interrupt_after"] = list(interrupt_after)
    try:
        return create_react_agent(**kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("create_react_agent 构建失败，回退 plain LLM：%r", e)
        return build_plain_agent(model=model, temperature=temperature, **kw)


# ---------------------------------------------------------------------------
# Checkpointer：进程单例，支持 memory / postgres
# ---------------------------------------------------------------------------

_CHECKPOINTER_SINGLETON: Optional[Any] = None
_CHECKPOINTER_TRIED: bool = False


def _default_checkpointer() -> Optional[Any]:
    """按 ``CHAYUAN_AGENT_CHECKPOINTER`` 懒建一次。

    - ``off`` / 未配置      → None（不开 HIL / 会话恢复）
    - ``memory``           → InMemorySaver（单进程；重启丢）
    - ``postgres``         → PostgresSaver（读 ``CHAYUAN_AGENT_CHECKPOINTER_DSN`` 或
                              ``DATABASE_URL``；失败降级 memory）
    - ``sqlite:<path>``    → SqliteSaver（单机持久化）

    任何失败都 fail-soft 返回 None。
    """
    global _CHECKPOINTER_SINGLETON, _CHECKPOINTER_TRIED
    if _CHECKPOINTER_SINGLETON is not None:
        return _CHECKPOINTER_SINGLETON
    if _CHECKPOINTER_TRIED:
        return None
    _CHECKPOINTER_TRIED = True
    import os
    mode = (os.environ.get("CHAYUAN_AGENT_CHECKPOINTER", "") or "").strip().lower()
    if not mode or mode in ("off", "none", "disabled"):
        return None
    try:
        if mode == "memory":
            from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
            _CHECKPOINTER_SINGLETON = InMemorySaver()
        elif mode == "postgres":
            dsn = (os.environ.get("CHAYUAN_AGENT_CHECKPOINTER_DSN")
                   or os.environ.get("DATABASE_URL") or "").strip()
            if not dsn:
                logger.warning("postgres checkpointer 缺 DSN，降级 memory")
                from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
                _CHECKPOINTER_SINGLETON = InMemorySaver()
            else:
                try:
                    from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
                    cp = PostgresSaver.from_conn_string(dsn)
                    try:
                        cp.setup()
                    except Exception:  # noqa: BLE001
                        pass
                    _CHECKPOINTER_SINGLETON = cp
                except Exception as e:  # noqa: BLE001
                    logger.warning("PostgresSaver 构建失败，降级 memory：%r", e)
                    from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
                    _CHECKPOINTER_SINGLETON = InMemorySaver()
        elif mode.startswith("sqlite"):
            path = mode.split(":", 1)[1] if ":" in mode else "chayuan_checkpoints.sqlite"
            try:
                from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
                _CHECKPOINTER_SINGLETON = SqliteSaver.from_conn_string(path)
            except Exception as e:  # noqa: BLE001
                logger.warning("SqliteSaver 构建失败，降级 memory：%r", e)
                from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
                _CHECKPOINTER_SINGLETON = InMemorySaver()
        else:
            logger.warning("未知 CHAYUAN_AGENT_CHECKPOINTER=%r，禁用", mode)
    except Exception as e:  # noqa: BLE001
        logger.warning("checkpointer 构建失败：%r", e)
        _CHECKPOINTER_SINGLETON = None
    return _CHECKPOINTER_SINGLETON


def build_tool_calling_agent(
    *, tools: List[Any], model: Optional[str] = None, temperature: float = 0.2, **kw,
):
    """纯 function calling（不 ReAct、不思考循环）。

    适合"LLM 只需判断调用哪个 tool"场景；开销比 ReAct 低。
    """
    # OpenAI tool calling 通过 bind_tools 暴露；不需要 create_react_agent
    llm = _llm_with_callbacks(model, temperature=temperature, **kw)
    try:
        return llm.bind_tools(tools or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("bind_tools 失败，回退 plain LLM：%r", e)
        return llm


def build_supervisor_agent(
    *,
    sub_agents: Dict[str, Any],
    model: Optional[str] = None,
    temperature: float = 0.1,
    system_prompt: Optional[str] = None,
):
    """N-7 Supervisor 模式：一个调度 LLM + N 个子 Agent。

    sub_agents 格式：{"researcher": agent_runnable, "writer": agent_runnable, ...}

    架构：
        START → supervisor（LLM 决定 next）
                     │
              ┌──────┴──────┐
              ▼             ▼
         researcher     writer ...
              │             │
              └─────┬───────┘
                    ▼
              supervisor 再判断是否继续
                    │
                    ▼
                   END

    未装 langgraph_supervisor 时，回退为简单串联：依次跑每个 sub_agent。
    """
    # langgraph-supervisor 是独立 pkg；优先用
    try:
        from langgraph_supervisor import create_supervisor  # type: ignore
        llm = _llm_with_callbacks(model, temperature=temperature)
        supervisor = create_supervisor(
            agents=list(sub_agents.values()),
            model=llm,
            prompt=system_prompt or _default_supervisor_prompt(sub_agents),
        )
        return supervisor.compile()
    except Exception as e:  # noqa: BLE001
        logger.info("langgraph_supervisor 未装，使用简单串联回退：%r", e)
        return _fallback_chain(sub_agents)


def _default_supervisor_prompt(sub_agents: Dict[str, Any]) -> str:
    names = ", ".join(sub_agents.keys())
    return (
        f"你是一个任务调度协调者。可用子代理：{names}。根据用户问题，决定下一个应该"
        f"调用哪个子代理；任务全部完成时回复 FINISH。"
    )


def _fallback_chain(sub_agents: Dict[str, Any]):
    """langgraph_supervisor 不可用时的朴素串联 runnable。"""
    agents = list(sub_agents.values())

    class _Chain:
        def invoke(self, messages, **kw):
            state = messages
            for a in agents:
                try:
                    state = a.invoke(state)
                except Exception as e:  # noqa: BLE001
                    logger.debug("sub agent 异常（跳过）：%r", e)
            return state

        async def astream_events(self, messages, version="v2", **kw):
            # 让外部消费方不崩：yield 一个 llm_end
            yield {"event": "on_chain_start", "name": "fallback_supervisor",
                   "data": {}}
            for a in agents:
                try:
                    async for ev in a.astream_events(messages, version=version):
                        yield ev
                except Exception:  # noqa: BLE001
                    continue
            yield {"event": "on_chain_end", "name": "fallback_supervisor",
                   "data": {}}

    return _Chain()
