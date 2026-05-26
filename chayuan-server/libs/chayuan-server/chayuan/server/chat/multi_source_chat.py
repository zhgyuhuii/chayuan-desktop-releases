"""多源 RAG 对话：并行检索 → 合成答复（SSE 流）。

事件流设计对齐 orchestrator 的 multi_search_stream；在其最后 final 事件之后，
追加 LLM 的 token 流事件，最终以 done 结束：

    stage / source_started / source_query / source_chunks / source_failed
    → aggregating → final (含 sources + aggregated chunks)
    → token ... token
    → done
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterable, AsyncIterator, Dict, List, Optional

from fastapi import Body, Request
from langchain_classic.callbacks.streaming_aiter import AsyncIteratorCallbackHandler
from langchain_core.prompts import ChatPromptTemplate
from sse_starlette.sse import EventSourceResponse

from chayuan.server.api_server.api_schemas import OpenAIChatOutput
from chayuan.server.auth.deps import require_auth_enabled  # noqa: F401  (reserved for FastAPI dep use)
from chayuan.server.auth.source_access import filter_accessible
from chayuan.server.chat.utils import History
from chayuan.server.db.repository.knowledge_source_repository import (
    list_accessible_source_ids,
    list_sources,
)
from chayuan.server.knowledge_source.orchestrator import multi_search_stream
from chayuan.server.utils import get_ChatOpenAI, get_default_llm, get_prompt_template, wrap_done
from chayuan.settings import Settings

logger = logging.getLogger("chayuan.chat.multi_source")


def _resolve_sources(user, source_ids, select_all: bool) -> List[Dict[str, Any]]:
    all_rows = list_sources()
    if select_all:
        if user is None:
            return all_rows
        role = user.get("role") if isinstance(user, dict) else ""
        if role == "admin":
            return all_rows
        uid = user.get("id") if isinstance(user, dict) else None
        accessible = set(list_accessible_source_ids(uid))
        return [r for r in all_rows if int(r["id"]) in accessible
                or r.get("visibility") == "public"]
    if not source_ids:
        return []
    allowed = set(filter_accessible(user, [int(x) for x in source_ids]))
    return [r for r in all_rows if int(r["id"]) in allowed]


async def multi_source_chat(
    query: str = Body(..., description="用户问题"),
    source_ids: List[int] = Body(default_factory=list),
    select_all: bool = Body(False),
    top_k: int = Body(5),
    per_source_timeout: float = Body(30.0),
    history: List[Dict[str, str]] = Body(default_factory=list),
    stream: bool = Body(True),
    model: str = Body("", description="答复用 LLM；留空用默认"),
    temperature: float = Body(0.3),
    max_tokens: Optional[int] = Body(None),
    prompt_name: str = Body("default"),
    request: Request = None,
    user=Body(None, include_in_schema=False),
):
    """使用时通常通过 FastAPI 路由注入 user；此函数也可直接调用。"""

    async def _iterator() -> AsyncIterator[Dict[str, str]]:
        # 1) 解析源
        sources = _resolve_sources(user, source_ids, bool(select_all))

        # 2) 并行检索，把 orchestrator 的事件直接透传给前端
        aggregated_wire: List[Dict[str, Any]] = []
        sources_meta: List[Dict[str, Any]] = []
        async for evt in multi_search_stream(
            query=query, sources=sources, top_k=int(top_k),
            per_source_timeout=float(per_source_timeout),
            llm_model=model or None,
            history=[
                {"role": h.get("role") or "user", "content": h.get("content") or ""}
                for h in (history or [])
            ],
        ):
            yield evt
            if evt["event"] == "final":
                try:
                    payload = json.loads(evt["data"])
                    aggregated_wire = payload.get("aggregated") or []
                    sources_meta = payload.get("sources") or []
                except Exception:  # noqa: BLE001
                    pass

        # 3) 构造给 LLM 的上下文：把每条 chunk 拼成「<出处> content」
        context_parts: List[str] = []
        for i, ch in enumerate(aggregated_wire):
            cite = ch.get("citation") or {}
            title = cite.get("title") or f"source#{ch.get('source_id')}"
            context_parts.append(
                f"[出处 {i+1} | {title}]\n{(ch.get('content') or '')[:2400]}"
            )
        context = "\n\n".join(context_parts) or "(无可用参考资料)"

        # 4) LLM 合成答复（流式）
        model_name = (model or "").strip() or get_default_llm()
        mt = max_tokens if max_tokens not in (None, 0) else Settings.model_settings.MAX_TOKENS
        callback = AsyncIteratorCallbackHandler()
        llm = get_ChatOpenAI(
            model_name=model_name, temperature=float(temperature),
            max_tokens=mt, callbacks=[callback],
        )
        prompt_template = get_prompt_template("rag", prompt_name)
        input_msg = History(role="user", content=prompt_template).to_msg_template(False)
        chat_prompt = ChatPromptTemplate.from_messages(
            [History.from_data(h).to_msg_template() for h in (history or [])] + [input_msg]
        )
        chain = chat_prompt | llm
        task = asyncio.create_task(wrap_done(
            chain.ainvoke({"context": context, "question": query}),
            callback.done,
        ))

        # 4.1 answer id
        answer_id = f"chat{uuid.uuid4().hex[:12]}"

        # 4.2 首包：回传 sources（便于前端立即渲染"出处"区域）
        yield _evt("sources", {
            "id": answer_id,
            "sources": sources_meta,
            "aggregated": aggregated_wire,
        })

        # 4.3 token 流
        if stream:
            async for token in callback.aiter():
                if not token:
                    continue
                yield _evt("token", {"id": answer_id, "delta": token})
        else:
            buf = ""
            async for token in callback.aiter():
                buf += token or ""
            yield _evt("answer", {"id": answer_id, "content": buf})

        await task
        yield _evt("done", {"id": answer_id})

    if stream:
        return EventSourceResponse(_iterator())
    # 非流式：把迭代器跑完，只把最终 content 作为 BaseResponse 返回
    async def _collect():
        content = ""
        src_wire = []
        async for evt in _iterator():
            if evt["event"] == "answer":
                try:
                    content = json.loads(evt["data"])["content"]
                except Exception:
                    pass
            elif evt["event"] == "sources":
                try:
                    src_wire = json.loads(evt["data"])
                except Exception:
                    pass
        return {"code": 0, "data": {"content": content, "sources": src_wire}}

    return await _collect()


def _evt(event: str, data: Any) -> Dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}
