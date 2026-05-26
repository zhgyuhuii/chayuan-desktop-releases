"""ChatGraph 的对外运行时：统一入口 + SSE 流式。

流式流程：
  1. 跑图的**非 generate 路径**（quota_check / classify / guardrail_in / retrieve / prepare_messages）
     一路到 prepare_messages 完成，得到 messages_for_llm
  2. 此时向前端推 `stage / sources` 两个事件
  3. 手动用 `llm.astream_events(version='v2')` 执行 LLM 流；streaming.py 将事件转 SSE
  4. 拿到完整答复后，再跑**后置节点**（guardrail_out / finalize）；推 `done`

为什么不让 LangGraph 自己跑完？——LangGraph 的 compile().astream_events 在 1.x
里对"让中途某个节点对外流式产出 token"还不成熟；拆成两段可获得可控 SSE 协议。

非流式 run_chat_sync：LangGraph 跑一遍返回 answer dict；包括 PII 脱敏。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.chat.graph.graph import (
    get_graph,
    node_classify,
    node_finalize,
    node_guardrail_in,
    node_guardrail_out,
    node_quota_check,
    run_graph_linear_async,
)
from chayuan.server.chat.graph.nodes import (
    node_apply_mounted_ranking,
    node_load_mounted_context,
    node_prepare_messages,
    node_retrieve_async,
)
from chayuan.server.chat.graph.state import ChatRequest, ChatState
from chayuan.server.chat.graph.streaming import (
    langchain_events_to_sse,
    make_sse,
)

logger = logging.getLogger("chayuan.chat.graph.runner")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _init_state(req: ChatRequest) -> ChatState:
    return {
        "request": req,
        "retrieved_chunks": [],
        "retrieved_sources_meta": [],
        "retrieval_elapsed_ms": 0,
        "events_buffer": [],
    }


def _patch(state: ChatState, delta: Dict[str, Any]) -> ChatState:
    state.update(delta or {})
    return state


# ---------------------------------------------------------------------------
# 流式：拆两段（前置 → LLM astream → 后置）
# ---------------------------------------------------------------------------

async def run_chat_stream(req: ChatRequest) -> AsyncIterator[Dict[str, str]]:
    """核心流式入口；产出统一 SSE dict 列。

    事件清单（按大致顺序）：
        stage("planning") →
            [quota_rejected? finalize done]
        stage("classifying") → stage("retrieving") →
            sources / chunks →
            stage("generating") →
            token*         (LLM astream_events)
            llm_end        (finish_reason + token usage)
        stage("post_processing") →
        sources_final (masked preview + lineage_id) →
        done
    """
    run_id = f"chat{uuid.uuid4().hex[:12]}"
    state = _init_state(req)
    yield make_sse("stage", {"stage": "planning", "run_id": run_id})

    # 1) 配额
    _patch(state, node_quota_check(state))
    if state.get("quota_rejected"):
        yield make_sse("quota_denied", {
            "run_id": run_id, "reason": state.get("quota_reason") or "配额超限",
        })
        _patch(state, node_finalize(state))
        yield make_sse("done", {"run_id": run_id, "reason": "quota_denied"})
        return

    # 2) classify + guardrail_in
    _patch(state, node_classify(state))
    yield make_sse("stage", {"stage": "classified", "mode": state.get("resolved_mode")})

    _patch(state, node_guardrail_in(state))
    if state.get("input_blocked"):
        yield make_sse("input_blocked", {
            "run_id": run_id,
            "reason": state.get("prompt_injection_reason") or "input blocked by guardrail",
        })
        _patch(state, node_finalize(state))
        yield make_sse("done", {"run_id": run_id, "reason": "input_blocked"})
        return

    # 3) 检索（await async 版本，避免在跑着的事件循环上再 new_event_loop）
    yield make_sse("stage", {"stage": "retrieving"})
    _patch(state, await node_retrieve_async(state))
    _patch(state, node_load_mounted_context(state))
    _patch(state, node_apply_mounted_ranking(state))
    chunks = state.get("retrieved_chunks") or []
    meta = state.get("retrieved_sources_meta") or []
    mounted_meta = state.get("mounted_sources_meta") or []
    if chunks or meta:
        yield make_sse("sources", {
            "run_id": run_id,
            "chunks": chunks[:20],
            "sources": meta,
            "mounted_sources": mounted_meta,
            "mounted": state.get("mounted_hit_summary") or {},
            "elapsed_ms": state.get("retrieval_elapsed_ms") or 0,
        })

    # 4) 拼消息
    _patch(state, node_prepare_messages(state))
    messages = state.get("messages_for_llm") or []

    # 5) 流式生成（astream_events v2）
    yield make_sse("stage", {"stage": "generating"})
    accum_tokens_est = 0
    full_text_parts: List[str] = []
    resolved_mode = state.get("resolved_mode") or ""
    try:
        from chayuan.server.observability.langfuse_integration import (
            inject_into_callbacks,
        )
        from chayuan.server.utils import get_ChatOpenAI, get_default_llm
        model = (req.model or "").strip() or get_default_llm()

        # agent 模式 + tools：走 LangGraph create_react_agent 的 astream_events，
        # 原生并行 tool calls + 可选 checkpointer（会话恢复 / HIL）。
        runnable = None
        runnable_input: Any = messages
        astream_config: Dict[str, Any] = {}
        if resolved_mode == "agent" and req.tools:
            try:
                from chayuan.server.chat.graph.nodes import _resolve_tools
                from chayuan.server.shared.agent_factory import build_react_agent
                tool_objs = _resolve_tools(req.tools)
                runnable = build_react_agent(
                    tools=tool_objs, model=model,
                    temperature=float(req.temperature),
                )
                runnable_input = {"messages": messages}
                if req.conversation_id:
                    astream_config = {
                        "configurable": {"thread_id": str(req.conversation_id)},
                    }
            except Exception as e:  # noqa: BLE001
                logger.warning("build_react_agent 失败，回退 plain LLM astream：%r", e)
                runnable = None

        if runnable is None:
            # 深度思考开关 → 按模型族注入 extra_body(qwen3 enable_thinking /
            # claude thinking budget / o-series 自动);compute_deep_think_kwargs
            # 不识别的模型 noop + 日志,UI 按钮按了也"不报错只无效"
            from chayuan.server.chat.deep_think import compute_deep_think_kwargs
            deep_extras = compute_deep_think_kwargs(model, bool(getattr(req, "deep_think", False)))
            llm_kwargs: Dict[str, Any] = {}
            if deep_extras:
                # ChatOpenAI 接受 model_kwargs={"extra_body": ...},直接转发给底层 client
                existing_mk = llm_kwargs.get("model_kwargs") or {}
                merged = {**existing_mk, **deep_extras}
                llm_kwargs["model_kwargs"] = merged

            # get_ChatOpenAI 现在 raise ModelNotConfigured / ModelLoadFailed
            # 不再 swallow → 上层 except 拿到原异常 + cause,直接转 token 事件
            runnable = get_ChatOpenAI(
                model_name=model,
                temperature=float(req.temperature),
                max_tokens=int(req.max_tokens or 0) or None,
                streaming=True,
                callbacks=inject_into_callbacks([]) or None,
                **llm_kwargs,
            )

        if astream_config:
            stream = runnable.astream_events(runnable_input, version="v2", config=astream_config)
        else:
            stream = runnable.astream_events(runnable_input, version="v2")
        async for sse in langchain_events_to_sse(stream, id=run_id):
            # 捕获 token 文本用于后续 masking / 血缘
            if sse["event"] == "token":
                try:
                    piece = json.loads(sse["data"]).get("delta") or ""
                    full_text_parts.append(piece)
                    accum_tokens_est += max(1, len(piece) // 3)
                except Exception:  # noqa: BLE001
                    pass
            yield sse
        state["answer"] = "".join(full_text_parts)
        state["answer_tokens"] = accum_tokens_est
        state["llm_model"] = model
    except Exception as e:  # noqa: BLE001
        logger.warning("astream_events 失败，回退非流式 generate：%r", e)
        from chayuan.server.chat.graph.nodes import node_generate
        _patch(state, node_generate(state))
        # 以 token 事件的形式一次性推完整答复
        if state.get("answer"):
            yield make_sse("token", {"id": run_id, "delta": state["answer"]})

    # 6) 后置（guardrail_out + finalize）：PII / 血缘 / 配额累计
    yield make_sse("stage", {"stage": "post_processing"})
    _patch(state, node_guardrail_out(state))
    _patch(state, node_finalize(state))

    if state.get("output_blocked"):
        yield make_sse("output_blocked", {"run_id": run_id,
                                           "reason": "output blocked by guardrail"})

    # 若进行了 PII 脱敏，单独回推一个 masked 结果，前端可选替换
    masked = state.get("output_masked_answer")
    if masked is not None:
        yield make_sse("masked_answer", {
            "run_id": run_id,
            "masked": masked,
            "pii_entities": state.get("output_pii_entities") or [],
        })

    yield make_sse("final", {
        "run_id": run_id,
        "lineage_id": state.get("lineage_id"),
        "mode": state.get("resolved_mode"),
        "tokens": int(state.get("answer_tokens") or 0),
        "mounted": state.get("mounted_hit_summary") or {},
    })
    yield make_sse("done", {"run_id": run_id})


# ---------------------------------------------------------------------------
# 非流式：LangGraph 一次跑完
# ---------------------------------------------------------------------------

async def run_chat_sync(req: ChatRequest) -> Dict[str, Any]:
    state = _init_state(req)
    graph = get_graph()
    if graph is not None:
        try:
            # 图里现在含 async retrieve 节点，必须走 ainvoke；
            # 早期版本用 run_in_executor + invoke 反而会在执行 retrieve 节点时
            # 试图 new_event_loop，触发 RuntimeError，是上一轮 bug 的根因。
            final = await graph.ainvoke(state, config={"recursion_limit": 16})
        except Exception as e:  # noqa: BLE001
            logger.warning("LangGraph ainvoke 失败，回退线性：%r", e)
            final = await run_graph_linear_async(state)
    else:
        final = await run_graph_linear_async(state)

    answer = final.get("output_masked_answer") or final.get("answer") or ""
    return {
        "code": 0,
        "data": {
            "answer": answer,
            "mode": final.get("resolved_mode"),
            "sources": final.get("retrieved_sources_meta") or [],
            "chunks": final.get("retrieved_chunks") or [],
            "mounted": final.get("mounted_hit_summary") or {},
            "mounted_sources": final.get("mounted_sources_meta") or [],
            "lineage_id": final.get("lineage_id"),
            "pii_entities": final.get("output_pii_entities") or [],
            "tokens": int(final.get("answer_tokens") or 0),
            "quota_rejected": bool(final.get("quota_rejected")),
            "quota_reason": final.get("quota_reason") or "",
        },
    }
