"""ChatGraph 的 StateGraph 组装。

图结构（按箭头顺序）：

  START → quota_check → classify → [guardrail_in]
                                      │
                                      ▼
                                   retrieve
                                      │
                                      ▼
                             prepare_messages
                                      │
                                      ▼
                                   generate        (非流式时走这里；流式路径由 runner 单独处理 astream_events)
                                      │
                                      ▼
                              [guardrail_out]
                                      │
                                      ▼
                                   finalize → END

LangGraph 未安装时返回 None；runner 自动走线性 fallback（同等功能）。

注：guardrail_in / guardrail_out 目前是 no-op 占位（P1-7 实现）；已放在图里，
将来打开只需替换节点实现即可。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from chayuan.server.chat.graph.nodes import (
    node_apply_mounted_ranking,
    node_classify,
    node_finalize,
    node_generate,
    node_load_mounted_context,
    node_prepare_messages,
    node_retrieve,
    node_retrieve_async,
)
from chayuan.server.chat.graph.state import ChatState

logger = logging.getLogger("chayuan.chat.graph.graph")


# 复用全局 logger 的同名变量，便于 guardrail 节点内部使用
_ = logger


# ---- Guardrail 节点（P1-7 真实实现，后端由 Settings 决定）-----------------

def node_guardrail_in(state: ChatState) -> Dict[str, Any]:
    """对 req.query 做输入过滤。severity=high 直接拦，否则只打标签。"""
    req = state["request"]
    if not req.governance_enabled:
        return {"input_blocked": False}
    try:
        from chayuan.server.guardrails import get_guardrail
        verdict = get_guardrail().check_input(req.query or "", context={
            "user_id": req.user_id, "user_role": req.user_role,
            "mode": state.get("resolved_mode") or "",
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("guardrail_in 异常（fail-open）：%r", e)
        return {"input_blocked": False}

    if verdict.severity == "high" and not verdict.allowed:
        return {
            "input_blocked": True,
            "prompt_injection_detected": "prompt_injection" in (verdict.categories or []),
            "prompt_injection_reason": verdict.reason or "guardrail blocked input",
        }
    # 非 high 的 block / 仅打标 → 不阻断，但把信号留给 finalize（可进血缘）
    return {
        "input_blocked": False,
        "prompt_injection_detected": "prompt_injection" in (verdict.categories or []),
        "prompt_injection_reason": verdict.reason or "",
    }


def node_guardrail_out(state: ChatState) -> Dict[str, Any]:
    """对 generate 产出的 answer 做输出过滤。

    - severity=high → 替换为"该回复已被安全策略拦截"，并设 output_blocked=True
    - 否则放行（即便 allowed=False 但 severity<high，也只是记录到血缘）
    """
    req = state["request"]
    answer = state.get("answer") or ""
    if not req.governance_enabled or not answer:
        return {"output_blocked": False}
    try:
        from chayuan.server.guardrails import get_guardrail
        verdict = get_guardrail().check_output(answer, context={
            "user_id": req.user_id, "user_role": req.user_role,
            "mode": state.get("resolved_mode") or "",
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("guardrail_out 异常（fail-open）：%r", e)
        return {"output_blocked": False}

    if verdict.severity == "high" and not verdict.allowed:
        safe_msg = (
            f"[Guardrail 拦截] {verdict.reason or '该回复被安全策略拦截'}。"
            f"请调整提问或联系管理员。"
        )
        return {
            "output_blocked": True,
            "answer": safe_msg,
            "answer_finish_reason": "blocked",
        }
    return {"output_blocked": False}


# ---- 预留 quota 入口节点（P1-9 quota 在此下判，超限直接跳 finalize）----------

def node_quota_check(state: ChatState) -> Dict[str, Any]:
    """进图的第一道闸：超配额直接拒绝。"""
    req = state["request"]
    if not req.governance_enabled or req.user_id is None:
        return {"quota_rejected": False}
    try:
        from chayuan.server.governance import quota
        ok, reason = quota.check_and_reserve(user_id=int(req.user_id))
        return {"quota_rejected": not ok, "quota_reason": reason or ""}
    except Exception as e:  # noqa: BLE001
        logger.debug("quota.check_and_reserve 失败（fail-open）：%r", e)
        return {"quota_rejected": False}


# ---- 条件边 ---------------------------------------------------------------

def _after_quota(state: ChatState) -> str:
    return "finalize" if state.get("quota_rejected") else "classify"


def _after_guardrail_in(state: ChatState) -> str:
    return "finalize" if state.get("input_blocked") else "retrieve"


def _after_generate(state: ChatState) -> str:
    return "guardrail_out"


# ---- 组装 -----------------------------------------------------------------

def build_chat_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception:  # noqa: BLE001
        return None

    g = StateGraph(ChatState)
    g.add_node("quota_check", node_quota_check)
    g.add_node("classify", node_classify)
    g.add_node("guardrail_in", node_guardrail_in)
    # 用 async retrieve；LangGraph 1.x StateGraph 接受 async 节点，
    # 配合 ainvoke / astream 使用，可以并发处理多源检索的 IO
    g.add_node("retrieve", node_retrieve_async)
    g.add_node("load_mounted_context", node_load_mounted_context)
    g.add_node("apply_mounted_ranking", node_apply_mounted_ranking)
    g.add_node("prepare_messages", node_prepare_messages)
    g.add_node("generate", node_generate)
    g.add_node("guardrail_out", node_guardrail_out)
    g.add_node("finalize", node_finalize)

    g.add_edge(START, "quota_check")
    g.add_conditional_edges(
        "quota_check", _after_quota,
        {"classify": "classify", "finalize": "finalize"},
    )
    g.add_edge("classify", "guardrail_in")
    g.add_conditional_edges(
        "guardrail_in", _after_guardrail_in,
        {"retrieve": "retrieve", "finalize": "finalize"},
    )
    g.add_edge("retrieve", "load_mounted_context")
    g.add_edge("load_mounted_context", "apply_mounted_ranking")
    g.add_edge("apply_mounted_ranking", "prepare_messages")
    g.add_edge("prepare_messages", "generate")
    g.add_edge("generate", "guardrail_out")
    g.add_edge("guardrail_out", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


# 单例 compile（首包加载延迟换长期零开销）
_GRAPH = None
_BUILT = False


def get_graph():
    global _GRAPH, _BUILT
    if _BUILT:
        return _GRAPH
    _BUILT = True
    try:
        _GRAPH = build_chat_graph()
    except Exception as e:  # noqa: BLE001
        logger.warning("ChatGraph build 失败：%r", e)
        _GRAPH = None
    return _GRAPH


# ---- 线性 fallback：LangGraph 未装时保证功能不退化 ----------------------------

def run_graph_linear(initial_state: ChatState) -> ChatState:
    """同步线性回退；只能在没有 running event loop 的线程里调。

    多源 retrieve 的 async IO 由 ``node_retrieve`` 的同步 wrapper 处理（内部
    asyncio.run），生产环境的 async 路径请改用 ``run_graph_linear_async``。
    """
    s: ChatState = dict(initial_state)  # type: ignore[assignment]
    s.update(node_quota_check(s))
    if s.get("quota_rejected"):
        s.update(node_finalize(s))
        return s
    s.update(node_classify(s))
    s.update(node_guardrail_in(s))
    if s.get("input_blocked"):
        s.update(node_finalize(s))
        return s
    s.update(node_retrieve(s))
    s.update(node_load_mounted_context(s))
    s.update(node_apply_mounted_ranking(s))
    s.update(node_prepare_messages(s))
    s.update(node_generate(s))
    s.update(node_guardrail_out(s))
    s.update(node_finalize(s))
    return s


async def run_graph_linear_async(initial_state: ChatState) -> ChatState:
    """异步线性回退：直接 await 异步 retrieve，避免 new_event_loop 冲突。

    所有非 IO 节点本身是 sync，但都是 CPU-轻量；保留同步调用即可。
    """
    s: ChatState = dict(initial_state)  # type: ignore[assignment]
    s.update(node_quota_check(s))
    if s.get("quota_rejected"):
        s.update(node_finalize(s))
        return s
    s.update(node_classify(s))
    s.update(node_guardrail_in(s))
    if s.get("input_blocked"):
        s.update(node_finalize(s))
        return s
    s.update(await node_retrieve_async(s))
    s.update(node_load_mounted_context(s))
    s.update(node_apply_mounted_ranking(s))
    s.update(node_prepare_messages(s))
    s.update(node_generate(s))
    s.update(node_guardrail_out(s))
    s.update(node_finalize(s))
    return s
