"""统一调度入口：DTO + AuthContext → ChatGraph → SSE / dict。

所有路由（``/chat/chat/completions``、``/chat/kb_chat``、``/chat/file_chat``、
``multi_source_chat``、``/openapi/v1/chat/complete``）都应该最终调用
:func:`dispatch_chat`。

为什么要在 ChatGraph 之上再包一层？
- ChatGraph 关心"图执行流程"，DTO/AuthContext 翻译、SSE 协议化、语义缓存、
  vision 旁路、conv 归属校验等是"协议适配层"职责，不应混入图节点；
- 让 ChatGraph 仍然可以独立被脚本 / 测试 / agent 直接驱动。

调度流程：
1. 从 :class:`UnifiedChatRequest` + :class:`AuthContext` 构造 ChatGraph 的
   ``ChatRequest``（注入 user_id / username / role）
2. Vision 旁路（直接走 OpenAI 透传，避开 agent 文本化 list-content 的乱码问题）
3. 语义缓存命中（仅非流式 + 短上下文）
4. 进入 ChatGraph 执行；流式 → :class:`EventSourceResponse`，非流式 → dict
5. 非流式成功结果回写语义缓存
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from sse_starlette.sse import EventSourceResponse

from chayuan.server.chat.core.auth_context import AuthContext, AuthMode
from chayuan.server.chat.core.request_dto import UnifiedChatRequest

logger = logging.getLogger("chayuan.chat.core.dispatcher")


# ---------------------------------------------------------------------------
# Feature flag（T5 完成后：统一调度已为唯一链路，保留函数仅用于调用端兼容）
# ---------------------------------------------------------------------------

def is_dispatcher_enabled() -> bool:
    """统一调度已默认开启；旧的 ``USE_CHAT_DISPATCHER`` Feature Flag 已废弃。

    保留函数签名是为了让外层路由的调用点无需同步删除；未来彻底下线前此函数始终
    返回 True。如果运维需要临时跳过 dispatcher（极少见），可通过环境变量
    ``CHAYUAN_DISABLE_CHAT_DISPATCHER=1`` 关闭。
    """
    import os
    return os.environ.get("CHAYUAN_DISABLE_CHAT_DISPATCHER", "").strip() not in ("1", "true", "TRUE")


# ---------------------------------------------------------------------------
# DTO → ChatGraph.ChatRequest
# ---------------------------------------------------------------------------

def _to_graph_request(req: UnifiedChatRequest, auth: AuthContext):
    """把统一 DTO + 鉴权上下文翻译成 ``chat.graph.ChatRequest``。"""
    from chayuan.server.chat.graph import ChatMode, ChatRequest

    mode_obj: Optional[ChatMode] = None
    if req.mode:
        try:
            mode_obj = ChatMode(req.mode)
        except Exception:  # noqa: BLE001
            mode_obj = None

    # AuthContext 三态都可以变成 ChatGraph 想要的 user_id（int 或 None）
    # App 模式没有人类 user_id；用 0 占位避免破坏血缘 schema，后续 governance
    # 层会读 auth_mode/app_id 区分
    pid = auth.principal_id
    user_id_int: Optional[int]
    if isinstance(pid, int):
        user_id_int = pid
    elif isinstance(pid, str) and pid.isdigit():
        user_id_int = int(pid)
    elif auth.mode == AuthMode.APP:
        user_id_int = 0  # App 调用：保留非 None 但表达"非自然人"
    else:
        user_id_int = None

    return ChatRequest(
        query=req.query, mode=mode_obj, history=req.history or [],
        stream=req.stream, model=req.model,
        temperature=float(req.temperature),
        max_tokens=(int(req.max_tokens) if req.max_tokens else None),
        prompt_name=req.prompt_name or "default",
        kb_name=req.kb_name, kb_names=list(req.kb_names or []),
        ku_ids=list(req.ku_ids or []),
        source_ids=[int(x) for x in (req.source_ids or [])],
        select_all_sources=bool(req.select_all_sources),
        file_chat_id=req.file_chat_id,
        search_engine=req.search_engine,
        image_url=req.image_url,
        top_k=int(req.top_k or 5), score_threshold=float(req.score_threshold),
        rewrite_strategy=req.rewrite_strategy,
        use_hybrid=req.use_hybrid, use_rerank=req.use_rerank,
        tools=list(req.tools or []), use_mcp=bool(req.use_mcp),
        tool_input=dict(req.tool_input or {}),
        user_id=user_id_int,
        username=auth.display_name or "",
        user_role=auth.role or "guest",
        conversation_id=req.conversation_id or "",
        request_id=auth.request_id or "",
        governance_enabled=bool(req.governance_enabled),
        deep_think=bool(req.deep_think),
    )


# ---------------------------------------------------------------------------
# 语义缓存（仅非流式 + 单轮短上下文，与原 chat_completions 行为一致）
# ---------------------------------------------------------------------------

def _semcache_key_extras(req: UnifiedChatRequest) -> Dict[str, Any]:
    """规范化的缓存维度：模型 / 温度 / 工具集合（顺序无关）。"""
    return {
        "route": "chat_dispatch",
        "temperature": float(req.temperature),
        "tools": sorted(list(req.tools or [])),
        "mode": req.mode or "auto",
    }


def _semcache_get(req: UnifiedChatRequest, auth: AuthContext) -> Optional[Dict[str, Any]]:
    if req.stream:
        return None
    if not req.query:
        return None
    if len(req.raw_messages or []) > 2:
        return None
    try:
        from chayuan.server.resilience import semcache_get
    except Exception:  # noqa: BLE001
        return None
    try:
        uid = auth.principal_id if isinstance(auth.principal_id, int) else None
        return semcache_get(
            query=req.query, user_id=uid, kb=req.kb_name,
            model=(req.model or "").strip() or None,
            extra=_semcache_key_extras(req),
        )
    except Exception:  # noqa: BLE001
        logger.debug("semcache_get failed", exc_info=True)
        return None


def _semcache_set(
    req: UnifiedChatRequest, auth: AuthContext, response: Dict[str, Any],
) -> None:
    if req.stream or not req.query:
        return
    if not isinstance(response, dict) or not response.get("choices") and not response.get("data"):
        # 没有 choices/data 字段的，不像是成功响应，跳过
        return
    try:
        from chayuan.server.resilience import semcache_set
    except Exception:  # noqa: BLE001
        return
    try:
        uid = auth.principal_id if isinstance(auth.principal_id, int) else None
        semcache_set(
            query=req.query, response=response, user_id=uid, kb=req.kb_name,
            model=(req.model or "").strip() or None,
            extra=_semcache_key_extras(req),
        )
    except Exception:  # noqa: BLE001
        logger.debug("semcache_set failed", exc_info=True)


# ---------------------------------------------------------------------------
# Vision 旁路：直接走 OpenAI 透传
# ---------------------------------------------------------------------------

async def _vision_passthrough(
    req: UnifiedChatRequest, auth: AuthContext,
) -> Union[Dict[str, Any], EventSourceResponse]:
    """Vision 多模态：messages 含 image_url；agent 会把 list content 字符串化成
    乱码，必须直接走 OpenAI 透传。复用 ``openai_routes.openai_request`` 实现。

    本地 loopback 图片 URL(http://127.0.0.1:.../v1/files/... 或 /v1/artifacts/...)
    在送出本机前内联成 ``data:`` URL —— 上游 vision 模型在云端连不到本机端口。
    """
    from chayuan.server.api_server.api_schemas import OpenAIChatInput
    from chayuan.server.api_server.openai_routes import (
        get_model_client, openai_request,
    )
    from chayuan.server.chat.vision_inline import inline_local_image_url

    # 深复制 + 改写每条 message 里的 image_url 部分
    fixed_messages = []
    for m in (req.raw_messages or []):
        if not isinstance(m, dict):
            fixed_messages.append(m)
            continue
        content = m.get("content")
        if not isinstance(content, list):
            fixed_messages.append(m)
            continue
        new_parts = []
        for part in content:
            if not isinstance(part, dict):
                new_parts.append(part)
                continue
            ptype = part.get("type")
            if ptype == "image_url":
                # OpenAI 多模态:{"type":"image_url","image_url":{"url":"..."}}
                iu = part.get("image_url") or {}
                if isinstance(iu, dict) and isinstance(iu.get("url"), str):
                    iu = {**iu, "url": inline_local_image_url(iu["url"])}
                new_parts.append({**part, "image_url": iu})
            elif ptype in ("input_image", "image"):
                # Anthropic / DashScope native:{"type":"image","source":{"data":"..."}}
                # 极少数前端会直接构造这种,留兜底:如果是 url 字段,转 data
                if isinstance(part.get("url"), str):
                    new_parts.append({**part, "url": inline_local_image_url(part["url"])})
                elif isinstance(part.get("source"), dict) and isinstance(part["source"].get("url"), str):
                    src = part["source"]
                    new_parts.append({
                        **part,
                        "source": {**src, "url": inline_local_image_url(src["url"])},
                    })
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)
        fixed_messages.append({**m, "content": new_parts})

    body = OpenAIChatInput(
        model=req.model or "",
        messages=fixed_messages,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=req.stream,
    )
    async with get_model_client(body.model) as client:
        return await openai_request(client.chat.completions.create, body)


# ---------------------------------------------------------------------------
# 调度主入口
# ---------------------------------------------------------------------------

async def _audio_to_text(req: UnifiedChatRequest) -> str:
    """T10：把 audio_url 跑 ASR 变成 query 文本。失败返回原 query（可能为空）。"""
    try:
        from chayuan.server.modality import get_modality_service
        svc = get_modality_service()
        text = svc.asr(req.audio_url)
        return (text or "").strip() or req.query
    except Exception as e:  # noqa: BLE001
        logger.warning("ASR 失败，回退原 query：%r", e)
        return req.query


async def _video_understand_then_chat(
    req: UnifiedChatRequest, auth: AuthContext,
) -> Union[EventSourceResponse, Dict[str, Any]]:
    """T10：video → understand → 注入摘要成 system context，再进 ChatGraph。"""
    try:
        from chayuan.server.modality import get_modality_service
        svc = get_modality_service()
        result = svc.understand_video(
            req.video_url, query=req.query,
            vision_model=(req.model or "").strip() or None,
        )
        summary = result.get("summary") or ""
        asr = result.get("asr") or ""
        # 把视频摘要 + ASR 作为 system 上下文喂 ChatGraph
        aug_history = list(req.history or [])
        if summary or asr:
            aug_history = [
                {"role": "system",
                 "content": ("【视频理解上下文】\n"
                             f"语音转写（前 2000 字）：{asr[:2000]}\n\n"
                             f"关键帧+摘要：{summary}")},
            ] + aug_history
        aug = UnifiedChatRequest(**{**req.__dict__, "history": aug_history,
                                      "video_url": None, "modality": "text"})
        return await dispatch_chat(aug, auth)
    except Exception as e:  # noqa: BLE001
        logger.warning("video understand 失败：%r", e)
        return {"code": 1, "msg": f"video understand failed: {e!r}"}


async def _dispatch_chat_inner(
    req: UnifiedChatRequest, auth: AuthContext,
) -> Union[EventSourceResponse, Dict[str, Any]]:
    # 0) 模态路由（T10）：视频先理解 → 文本；音频先 ASR → 文本
    try:
        from chayuan.server.modality import get_modality_service
        mod = req.modality or get_modality_service().detect(
            req.raw_messages, extras={
                "audio_url": req.audio_url, "video_url": req.video_url,
            },
        )
    except Exception:  # noqa: BLE001
        mod = "text"
    if mod == "video" or req.video_url:
        return await _video_understand_then_chat(req, auth)
    if mod == "audio" or req.audio_url:
        req = UnifiedChatRequest(**{**req.__dict__,
                                      "query": await _audio_to_text(req),
                                      "audio_url": None, "modality": "text"})

    # 1) Vision：直接旁路；不进 ChatGraph，不命中语义缓存
    if req.is_vision:
        logger.info(
            "[dispatch] vision passthrough mode=%s model=%s parts=%d",
            req.mode, req.model, len(req.raw_messages[-1].get("content") or [])
            if req.raw_messages else 0,
        )
        return await _vision_passthrough(req, auth)

    # 2) 语义缓存命中
    hit = _semcache_get(req, auth)
    if hit is not None:
        logger.info(
            "[dispatch] semcache hit principal=%s mode=%s",
            auth.principal_id, req.mode,
        )
        return hit

    # 3) 进入 ChatGraph
    from chayuan.server.chat.graph import run_chat_stream, run_chat_sync
    graph_req = _to_graph_request(req, auth)

    if req.stream:
        return EventSourceResponse(run_chat_stream(graph_req))

    out = await run_chat_sync(graph_req)
    _semcache_set(req, auth, out)
    return out


async def dispatch_chat(
    req: UnifiedChatRequest, auth: AuthContext,
) -> Union[EventSourceResponse, Dict[str, Any]]:
    """单一对话调度入口。返回值：

    - ``stream=True``  → ``EventSourceResponse``，前端按统一 SSE 协议消费
    - ``stream=False`` → ``Dict``，与 ChatGraph.run_chat_sync 一致

    P2-10：用 OTel span 包整条调度，方便在 trace 里看到 modality / cache / graph 父子关系。
    """
    attrs = {
        "auth.mode": auth.mode.value,
        "auth.principal_id": str(auth.principal_id or ""),
        "auth.tenant_id": auth.tenant_id or "",
        "chat.mode": req.mode or "auto",
        "chat.kb_name": req.kb_name or "",
        "chat.modality": req.modality or "text",
        "chat.stream": bool(req.stream),
        "chat.model": req.model or "",
    }
    try:
        from chayuan.server.observability.tracing import start_as_current_span
    except Exception:  # noqa: BLE001
        return await _dispatch_chat_inner(req, auth)
    with start_as_current_span("chat.dispatch", attrs):
        return await _dispatch_chat_inner(req, auth)
