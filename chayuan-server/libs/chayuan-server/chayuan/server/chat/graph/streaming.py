"""LangChain ``.astream_events(version='v2')`` → 前端 SSE 协议 的转换器。

LangChain 1.x 的 astream_events 会产出结构化事件（on_chat_model_stream /
on_chain_start / on_tool_start 等），替代我们老代码里的 AsyncIteratorCallbackHandler。

我们的前端（webui / 外部调用方）依赖的 SSE 协议是：
- ``event: token  data: {"delta": "..."}``
- ``event: stage  data: {"stage": "planning|retrieving|generating|..."}``
- ``event: sources data: {"chunks": [...], "sources": [...]}``
- ``event: tool_call  data: {...}``
- ``event: done   data: {"id": "..."}``

本模块提供两套工具：

1. ``langchain_events_to_sse()``：把 LangChain async 迭代器转成上述 SSE dict 流
2. ``sse_emit()`` / ``make_sse()`` 工具函数，供各节点手工发事件
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger("chayuan.chat.graph.streaming")


def make_sse(event: str, data: Any) -> Dict[str, str]:
    """SSE 事件 dict（event + JSON-encoded data 字符串）；供 sse-starlette 消费。"""
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        payload = json.dumps({"raw": str(data)}, ensure_ascii=False)
    return {"event": event, "data": payload}


async def langchain_events_to_sse(
    stream: AsyncIterator[Dict[str, Any]],
    *,
    id: str,
    emit_runs: bool = False,
) -> AsyncIterator[Dict[str, str]]:
    """消费 ``llm.astream_events(version='v2')``，产出统一 SSE。

    关心的事件（LangChain v2）：
    - on_chat_model_stream / on_llm_stream  → token 增量
    - on_chat_model_end / on_llm_end        → 统计信息（finish_reason / token）
    - on_tool_start / on_tool_end           → 工具调用
    - on_chain_start / on_chain_end         → 可选，debug 用（默认不透传给前端）
    """
    try:
        async for ev in stream:
            et = ev.get("event")
            data = ev.get("data") or {}

            if et in ("on_chat_model_stream", "on_llm_stream"):
                # v2 的 chunk 一般在 data["chunk"] 里，是 AIMessageChunk
                chunk = data.get("chunk")
                token = _extract_token(chunk)
                if token:
                    yield make_sse("token", {"id": id, "delta": token})

            elif et in ("on_chat_model_end", "on_llm_end"):
                out = data.get("output")
                finish = _extract_finish(out)
                # 尽量把 token usage 带出来，让 finalize 节点归入配额/成本
                usage = _extract_usage(out)
                yield make_sse("llm_end", {
                    "id": id, "finish_reason": finish, "usage": usage,
                })

            elif et == "on_tool_start":
                yield make_sse("tool_start", {
                    "id": id,
                    "tool": ev.get("name") or "",
                    "input": data.get("input"),
                })

            elif et == "on_tool_end":
                yield make_sse("tool_end", {
                    "id": id,
                    "tool": ev.get("name") or "",
                    "output": _truncate(data.get("output"), 2000),
                })

            elif et == "on_chain_start" and emit_runs:
                yield make_sse("chain_start", {
                    "id": id, "name": ev.get("name") or "",
                })

            elif et == "on_chain_end" and emit_runs:
                yield make_sse("chain_end", {
                    "id": id, "name": ev.get("name") or "",
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("langchain_events_to_sse 异常：%r", e)
        yield make_sse("error", {"id": id, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# AIMessageChunk / LLMResult 的字段提取（兼容 LangChain 0.x / 1.x）
# ---------------------------------------------------------------------------

def _extract_token(chunk) -> str:
    if chunk is None:
        return ""
    # AIMessageChunk
    text = getattr(chunk, "content", None)
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        # 1.x 的 multimodal content list of dicts
        parts = []
        for item in text:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item["text"]))
        return "".join(parts)
    # LLMResult 里 generations
    try:
        generations = getattr(chunk, "generations", None)
        if generations:
            return str(generations[0][0].text or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _extract_finish(output) -> str:
    if output is None:
        return ""
    try:
        # AIMessage.response_metadata.finish_reason
        rm = getattr(output, "response_metadata", None)
        if isinstance(rm, dict):
            return str(rm.get("finish_reason") or "")
        # LLMResult
        gens = getattr(output, "generations", None)
        if gens:
            info = getattr(gens[0][0], "generation_info", None) or {}
            return str(info.get("finish_reason") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _extract_usage(output) -> Dict[str, int]:
    """从 output 里尽量抓 token usage。"""
    try:
        md = getattr(output, "usage_metadata", None) or {}
        if isinstance(md, dict) and md:
            return {
                "input_tokens": int(md.get("input_tokens") or 0),
                "output_tokens": int(md.get("output_tokens") or 0),
                "total_tokens": int(md.get("total_tokens") or 0),
            }
        # 旧风格：LLMResult.llm_output["token_usage"]
        if hasattr(output, "llm_output"):
            u = ((output.llm_output or {}).get("token_usage")) or {}
            return {
                "input_tokens": int(u.get("prompt_tokens") or 0),
                "output_tokens": int(u.get("completion_tokens") or 0),
                "total_tokens": int(u.get("total_tokens") or 0),
            }
    except Exception:  # noqa: BLE001
        pass
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _truncate(obj: Any, limit: int = 2000) -> Any:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = str(obj)
    if len(s) > limit:
        return s[:limit] + "...[truncated]"
    return obj
