"""ChatGraph 节点实现。

所有节点都遵循**同一约定**：
- 输入 state（ChatState），返回 state 增量（部分 dict）
- 不抛异常（写到 state["error"]）；避免单节点失败拖垮整张图
- 尽量把"副作用"集中到 finalize 节点（写 DB / 扣配额 / 记审计）
- LLM 调用都走 astream_events，节点只负责构造可迭代 stream；真正的 token 流在 runner 层统一编码成 SSE

这使得"加/改一个 mode"只需改 classify 路由与相关节点，其它节点（guardrail /
governance）无需任何改动，真正做到热插拔。
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from chayuan.server.chat.graph.state import ChatMode, ChatRequest, ChatState
from chayuan.settings import Settings

logger = logging.getLogger("chayuan.chat.graph.nodes")


# ===========================================================================
# classify
# ===========================================================================

def node_classify(state: ChatState) -> Dict[str, Any]:
    """把 ChatRequest 中的 mode / 上下文翻译成 resolved_mode。

    - 显式 mode 直接用
    - 未显式时按"有 source_ids / kb_names → multi_source；有 image → vision；
      有 tools → agent；有 kb_name → kb；有 file_chat_id → file；否则 llm"
    """
    req: ChatRequest = state["request"]
    mode = req.mode
    if mode is None:
        if req.image_url:
            mode = ChatMode.VISION
        elif req.source_ids or req.kb_names or req.select_all_sources:
            mode = ChatMode.MULTI_SOURCE
        elif req.tools:
            mode = ChatMode.AGENT
        elif req.kb_name:
            mode = ChatMode.KB
        elif req.file_chat_id:
            mode = ChatMode.FILE
        elif req.search_engine:
            mode = ChatMode.SEARCH_ENGINE
        else:
            mode = ChatMode.LLM
    return {"resolved_mode": mode.value if isinstance(mode, ChatMode) else str(mode)}


# ===========================================================================
# retrieve
# ===========================================================================

async def node_retrieve_async(state: ChatState) -> Dict[str, Any]:
    """按 resolved_mode 走对应的检索路径，产出归一 chunks。

    实现委托给 ``chat.handlers`` 注册表里的对应 handler；新增模式只需在
    ``handlers/`` 包内加一个文件 + ``register_handler``，不需要改本节点。

    - llm / agent / vision：handler.retrieve 默认实现返回空，相当于不检索
    - kb / file / search_engine / multi_source：各 handler 自己实现，保留同步
      IO 走 ``run_in_threadpool`` 的硬约束，避免阻塞事件循环
    """
    t0 = time.time()
    mode = state.get("resolved_mode") or ChatMode.LLM.value
    try:
        from chayuan.server.chat.handlers import HandlerNotFound, get_handler
        try:
            handler = get_handler(mode)
        except HandlerNotFound:
            logger.warning("未找到 mode=%s 的 handler，按空检索处理", mode)
            return {
                "retrieved_chunks": [],
                "retrieved_sources_meta": [],
                "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
            }
        return await handler.retrieve(state)
    except Exception as e:  # noqa: BLE001
        logger.warning("retrieve 节点异常（继续 fail-soft）：%r", e)
        return {
            "retrieved_chunks": [],
            "retrieved_sources_meta": [],
            "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
            "error": f"retrieve: {type(e).__name__}: {e}",
        }


def node_retrieve(state: ChatState) -> Dict[str, Any]:
    """同步 wrapper：仅供 ``run_graph_linear`` / 同步 ``LangGraph.invoke`` 路径使用。

    - 当前线程**没有**运行中的事件循环 → 用 asyncio.run 驱动 async impl；
    - 当前线程**有**运行中的事件循环（理论上不该发生，因为 runner 已直接 await
      async 版本）→ 不做 fallback，直接抛错并 fail-soft 返回空 chunks，避免在
      跑着的循环上再叠一个 loop 触发 ``RuntimeError``。

    所有 async 入口（runner.run_chat_stream / run_chat_sync）应该直接 await
    ``node_retrieve_async``。
    """
    import asyncio
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if running:
        logger.warning(
            "node_retrieve 同步 wrapper 被在 async 上下文里调用，"
            "请改用 await node_retrieve_async；本次返回空检索结果。"
        )
        return {
            "retrieved_chunks": [],
            "retrieved_sources_meta": [],
            "retrieval_elapsed_ms": 0,
            "error": "retrieve: invoked sync wrapper from async context",
        }
    return asyncio.run(node_retrieve_async(state))


def _doc_to_chunk(d: Any, source: str = "") -> Dict[str, Any]:
    """把 Document / dict / DocumentWithVSId 归一为 wire-format chunk。"""
    content = getattr(d, "page_content", None)
    meta = getattr(d, "metadata", None)
    if content is None and isinstance(d, dict):
        content = d.get("page_content") or d.get("content") or ""
        meta = d.get("metadata") or {}
    meta = meta or {}
    return {
        "content": content or "",
        "score": float(getattr(d, "score", meta.get("score", 1.0)) or 1.0),
        "source_id": 0,
        "source_kind": "vector" if source.startswith(("kb:", "file:")) else "other",
        "citation": {
            "title": str(meta.get("source") or meta.get("file_name") or source),
            "source_kind": "vector" if source.startswith(("kb:", "file:")) else "other",
            "meta": {k: str(v)[:200] for k, v in meta.items() if k != "vector"},
        },
    }


# ===========================================================================
# prepare_messages   - 拼装给 LLM 的消息（含 RAG context）
# ===========================================================================

def node_prepare_messages(state: ChatState) -> Dict[str, Any]:
    """把 history + RAG context + user query 组装成 LLM 输入。

    输出：state["messages_for_llm"] 是 list[{"role","content"}]。
    """
    req: ChatRequest = state["request"]
    chunks = state.get("retrieved_chunks") or []
    msgs: List[Dict[str, Any]] = []

    # 系统消息：按 mode + 是否有检索结果切换模板
    system = _build_system_message(req, chunks, state.get("mounted_context") or {})
    if system:
        msgs.append({"role": "system", "content": system})

    # 历史
    for h in (req.history or [])[-12:]:
        role = h.get("role") or "user"
        if role == "system":
            continue
        msgs.append({"role": role, "content": str(h.get("content", ""))})

    # 当前问题:Vision 特殊
    if req.image_url:
        # 本地 loopback URL(http://127.0.0.1:.../v1/files/...) 上游 vision
        # 模型(qwen-vl / GPT-4o / Claude)在云端连不上,必须内联成 data: URL
        from chayuan.server.chat.vision_inline import inline_local_image_url
        inlined = inline_local_image_url(req.image_url)
        msgs.append({
            "role": "user",
            "content": [
                {"type": "text", "text": req.query or ""},
                {"type": "image_url", "image_url": {"url": inlined}},
            ],
        })
    else:
        msgs.append({"role": "user", "content": req.query})

    return {"messages_for_llm": msgs}


def node_load_mounted_context(state: ChatState) -> Dict[str, Any]:
    """Load approved training-data mounts that apply to this chat request."""
    req: ChatRequest = state["request"]
    try:
        from chayuan.server.chat.mounts import build_mounted_context

        mounted = build_mounted_context(req, state)
    except Exception as e:  # noqa: BLE001
        logger.debug("mounted context skipped: %r", e)
        mounted = {
            "enabled": False,
            "mounts": [],
            "preferences": {},
            "fewshot_examples": [],
            "retrieval_boosts": {},
            "safety_rules": [],
            "sources_meta": [],
            "hit_summary": {"mount_count": 0, "error": str(e)},
        }
    return {
        "mounted_context": mounted,
        "mounted_preferences": mounted.get("preferences") or {},
        "mounted_examples": mounted.get("fewshot_examples") or [],
        "mounted_safety_rules": mounted.get("safety_rules") or [],
        "mounted_sources_meta": mounted.get("sources_meta") or [],
        "mounted_hit_summary": mounted.get("hit_summary") or {},
    }


def node_apply_mounted_ranking(state: ChatState) -> Dict[str, Any]:
    """Apply training-data retrieval signals after regular retrieval."""
    chunks = list(state.get("retrieved_chunks") or [])
    mounted = state.get("mounted_context") or {}
    if not chunks or not mounted.get("retrieval_boosts"):
        return {}
    try:
        from chayuan.server.chat.mounts import apply_mounted_ranking

        return {"retrieved_chunks": apply_mounted_ranking(chunks, mounted)}
    except Exception as e:  # noqa: BLE001
        logger.debug("mounted ranking skipped: %r", e)
        return {}


def _build_system_message(
    req: ChatRequest,
    chunks: List[Dict[str, Any]],
    mounted_context: Optional[Dict[str, Any]] = None,
) -> str:
    import os
    parts: List[str] = []
    mounted_context = mounted_context or {}
    preferences = mounted_context.get("preferences") or {}
    pref_lines: List[str] = []
    for style in preferences.get("styles") or []:
        pref_lines.append(f"- 回答风格：{style}")
    for rule in preferences.get("rules") or []:
        pref_lines.append(f"- {rule}")
    if preferences.get("citation_required"):
        pref_lines.append("- 必须尽量给出可追溯引用；资料不足时明确说明不足。")
    if pref_lines:
        parts.append("【已发布回答偏好（来自训练数据中心）】\n" + "\n".join(pref_lines[:12]))

    safety_rules = list(mounted_context.get("safety_rules") or [])
    if safety_rules:
        parts.append("【已发布禁止模式（来自训练数据中心）】\n" + "\n".join(f"- {x}" for x in safety_rules[:10]))

    examples = list(mounted_context.get("fewshot_examples") or [])
    if examples:
        lines = ["【已审核高质量样例（只学习回答方式，不当作事实来源）】"]
        for idx, ex in enumerate(examples[:5], start=1):
            q = str(ex.get("query") or "")[:500]
            a = str(ex.get("answer") or "")[:1000]
            if q and a:
                lines.append(f"示例 {idx}\n用户问题：{q}\n推荐回答：{a}")
        if len(lines) > 1:
            parts.append("\n\n".join(lines))

    if req.prompt_name:
        try:
            from chayuan.server.utils import get_prompt_template
            tpl = get_prompt_template("rag", req.prompt_name) if chunks else ""
            if tpl:
                parts.append(tpl)
        except Exception:  # noqa: BLE001
            pass
    if chunks:
        ctx_lines = ["【参考资料（由检索系统自动注入）】"]
        for i, ch in enumerate(chunks[:8]):
            raw_title = (ch.get("citation") or {}).get("title") or f"chunk#{i+1}"
            # 基名:与 KBHandler 的 sources_meta.cite_index 对齐,LLM 写
            # [出处 N] 时,前端 chip 上显示的 file_name 与 N 一一对应。
            title = os.path.basename(raw_title) or raw_title
            ctx_lines.append(f"[出处 {i+1} | {title}]\n{(ch.get('content') or '')[:1600]}")
        parts.append("\n\n".join(ctx_lines))
        parts.append(
            "请基于上述参考资料回答用户问题；不得编造资料中不存在的事实。\n"
            "**关键格式要求**:每个观点/段落后必须用 `[出处 N]` 形式标注引用("
            "N 是对应的出处编号,如 `[出处 1]`、`[出处 2]`),不需要在末尾再单独罗列。"
            "若同一段引用了多份资料,可写 `[出处 1][出处 3]`。"
        )
    return "\n\n".join(parts).strip()


# ===========================================================================
# generate（流式，真正的 LLM 调用在 runner 层 astream_events；节点只做 sanity）
# ===========================================================================

def node_generate(state: ChatState) -> Dict[str, Any]:
    """非流式回退：调一次 LLM，把完整答复写进 state["answer"]。

    - 流式主路径在 runner.py 里用 `.astream_events(v2)` 完成
    - 本节点作为"非流式请求 / 流式失败兜底"的回退路径，保证图一定能产出一条 final
    - mode=agent：N-4 升级为 LangGraph create_react_agent；失败回退 plain LLM
    """
    req: ChatRequest = state["request"]
    messages = state.get("messages_for_llm") or []
    if not messages:
        return {"answer": "(无输入消息)", "answer_finish_reason": "error"}
    mode = state.get("resolved_mode") or "llm"
    try:
        from chayuan.server.shared.agent_factory import (
            build_plain_agent, build_react_agent,
        )
        model = (req.model or "").strip()

        # N-4：Agent 模式走 create_react_agent
        if mode == "agent" and req.tools:
            try:
                tool_objs = _resolve_tools(req.tools)
                agent = build_react_agent(
                    tools=tool_objs, model=model,
                    temperature=float(req.temperature),
                )
                # LangGraph ReAct 的 invoke 接 messages state；返回最终 AIMessage
                result = agent.invoke({"messages": messages})
                # 提取最后一条 AI 回复
                msgs = result.get("messages") if isinstance(result, dict) else None
                if msgs:
                    last = msgs[-1]
                    text = getattr(last, "content", None) or str(last)
                else:
                    text = ""
                return {
                    "answer": text,
                    "answer_finish_reason": "stop",
                    "llm_model": model,
                }
            except Exception as e:  # noqa: BLE001
                logger.warning("React agent 失败，回退 plain LLM：%r", e)

        # T11：Supervisor 模式 — 三 Agent 协作
        if mode == "supervisor":
            try:
                from chayuan.server.chat.graph.supervisor import build_supervisor_team
                tool_objs = _resolve_tools(req.tools) if req.tools else []
                team = build_supervisor_team(tools=tool_objs, model=model)
                result = team.invoke({"messages": messages})
                msgs = result.get("messages") if isinstance(result, dict) else None
                text = ""
                if msgs:
                    last = msgs[-1]
                    text = getattr(last, "content", None) or str(last)
                # 若 reviewer 最终带了 "FINAL:" 前缀，去掉
                if text.startswith("FINAL:"):
                    text = text[len("FINAL:"):].lstrip()
                return {
                    "answer": text,
                    "answer_finish_reason": "stop",
                    "llm_model": model,
                }
            except Exception as e:  # noqa: BLE001
                logger.warning("Supervisor team 失败，回退 plain LLM：%r", e)

        # 普通路径：plain LLM
        # build_plain_agent → _llm_with_callbacks → get_ChatOpenAI;
        # 失败现在直接 raise ModelNotConfigured / ModelLoadFailed,
        # 外层 except 接到后把根因消息回写 state["answer"]。
        llm = build_plain_agent(
            model=model, temperature=float(req.temperature),
            max_tokens=int(req.max_tokens or 0) or None,
            streaming=False,
        )
        resp = llm.invoke(messages)
        text = getattr(resp, "content", None) or str(resp)
        usage = _usage_from(resp)
        return {
            "answer": text,
            "answer_finish_reason": "stop",
            "answer_tokens": usage,
            "llm_model": model,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("generate 节点 LLM 失败：%r", e)
        return {
            "answer": f"（生成失败：{type(e).__name__}: {e}）",
            "answer_finish_reason": "error",
            "error": f"generate: {e}",
        }


def _resolve_tools(tool_names: List[str]) -> List[Any]:
    """把工具名翻译成 LangChain tool 对象。失败跳过。"""
    try:
        from chayuan.server.utils import get_tool
        all_tools = get_tool()
        out = []
        for name in tool_names or []:
            t = all_tools.get(name) if isinstance(all_tools, dict) else None
            if t is not None:
                out.append(t)
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug("resolve_tools 失败：%r", e)
        return []


def _usage_from(resp) -> int:
    try:
        md = getattr(resp, "usage_metadata", None) or {}
        return int(md.get("total_tokens") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ===========================================================================
# finalize（治理接入点：lineage / quota / masking）
# ===========================================================================

def node_finalize(state: ChatState) -> Dict[str, Any]:
    """写血缘、扣配额、应用脱敏、回传最终 answer。"""
    req: ChatRequest = state["request"]
    answer = state.get("answer") or ""

    # 治理：脱敏（先跑，保证写血缘时的 answer 已脱敏，避免血缘表里也有 PII）
    masked, pii_entities = answer, []
    if req.governance_enabled and answer:
        try:
            from chayuan.server.governance import masking, pii
            pii_entities = pii.scan_text(answer)
            if pii_entities:
                masked = masking.apply_masking(
                    text=answer, entities=pii_entities, user_role=req.user_role,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("PII/masking 失败（忽略）：%r", e)

    # 治理：写血缘
    lineage_id: Optional[int] = None
    if req.governance_enabled:
        try:
            from chayuan.server.governance import lineage
            lineage_id = lineage.record_chat(
                user_id=req.user_id, username=req.username,
                conversation_id=req.conversation_id, request_id=req.request_id,
                mode=state.get("resolved_mode") or "",
                query=req.query, answer_preview=masked[:500],
                llm_model=state.get("llm_model") or req.model,
                sources=state.get("retrieved_sources_meta") or [],
                retrieved_chunks=state.get("retrieved_chunks") or [],
                pii_count=len(pii_entities),
                tokens_total=int(state.get("answer_tokens") or 0),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("lineage 写失败（忽略）：%r", e)

    # 治理：配额扣减（token bucket 在入口处已拒绝，这里只做"事后累计"）
    if req.governance_enabled:
        try:
            from chayuan.server.governance import quota
            quota.record_usage(
                user_id=req.user_id,
                tokens=int(state.get("answer_tokens") or 0),
                mode=state.get("resolved_mode") or "",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("quota.record_usage 失败（忽略）：%r", e)

    # 训练数据挂载命中审计：只记录 mount 粒度摘要，不写完整 prompt。
    try:
        from chayuan.server.db.repository import data_mount_repository

        mounted = state.get("mounted_context") or {}
        for mount in mounted.get("mounts") or []:
            mount_id = str((mount or {}).get("id") or "")
            if not mount_id:
                continue
            summary = dict(state.get("mounted_hit_summary") or {})
            sample_ids = []
            for ex in mounted.get("fewshot_examples") or []:
                sid = str((ex or {}).get("sample_id") or "")
                if sid:
                    sample_ids.append(sid)
            data_mount_repository.record_hit(
                request_id=req.request_id,
                conversation_id=req.conversation_id,
                user_id=req.user_id,
                mount_id=mount_id,
                artifact_type="chat_context",
                sample_ids=sample_ids,
                hit_count=int(summary.get("fewshot_count") or 0) + int(summary.get("boost_count") or 0),
                token_count=int(state.get("answer_tokens") or 0),
                effect_summary=summary,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("data mount hit log failed: %r", e)

    return {
        "output_pii_entities": pii_entities,
        "output_masked_answer": masked if masked != answer else None,
        "lineage_id": lineage_id,
    }
