from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
# 41 题 P7:删除未使用的 ``from langchain_core.prompts import PromptTemplate`` —
# 这一行死代码 import 拖整条 ``langchain_core → transformers → torch`` 链
# (~2 秒),是子进程 spawn 启动慢的最大来源之一。
from sse_starlette import EventSourceResponse

from chayuan.server.api_server.api_schemas import OpenAIChatInput
from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.chat.chat import chat, flatten_chat_model_config_for_inference
from chayuan.server.chat.kb_chat import kb_chat
from chayuan.server.chat.feedback import chat_feedback
from chayuan.server.chat.file_chat import file_chat
from chayuan.server.db.repository import add_message_to_db
from chayuan.server.db.repository.conversation_repository import (
    delete_conversation,
    get_conversation_by_id,
    list_conversations_for_user,
    rename_conversation,
)
from chayuan.server.utils import (
    get_default_llm,
    get_tool,
    get_tool_config,
)
from chayuan.settings import Settings
from chayuan.utils import build_logger
from .openai_routes import get_model_client, openai_request, OpenAIChatOutput


def _is_vision_request(messages) -> bool:
    """
    检测最后一条用户消息是否是 vision 多模态（content 是数组、且含 image_url/input_image）。

    Vision 请求的 messages[-1]["content"] 形如：
        [{"type": "text", "text": "描述这张图"},
         {"type": "image_url", "image_url": {"url": "http://..."}}]
    直接走 agent 会把整个 list 字符串化成 "[{...}]" 丢给 LLM，模型读到的是乱码；
    应该旁路到 /v1/chat/completions 透传到平台原生 vision 能力。
    """
    if not messages:
        return False
    last = messages[-1] if isinstance(messages, list) else None
    if not isinstance(last, dict):
        return False
    content = last.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "input_image", "image"):
                return True
    return False


logger = build_logger()

chat_router = APIRouter(prefix="/chat", tags=["察元 对话"])

# chat_router.post(
#     "/chat",
#     summary="与llm模型对话(通过LLMChain)",
# )(chat)

# /feedback/v2：客户端 + Langfuse 友好版本。
# 接受 trace_id 直接挂分到 Langfuse；非阻塞。score 用任意数（典型 ±1 / 0-1 / 0-100），
# Langfuse 侧不做范围校验，前端约定即可。
@chat_router.post("/feedback/v2", summary="对话评分（Langfuse-aware）")
def chat_feedback_v2_endpoint(
    trace_id: str = Body(..., max_length=64, description="Langfuse trace_id；前端发送时生成"),
    score: float = Body(..., description="评分；约定：±1 用户主观；0-1 量化；0-100 后台"),
    name: str = Body("user_feedback", max_length=64, description="score name；如 user_feedback / aborted"),
    comment: str = Body("", description="可选文字反馈"),
    message_id: str = Body("", max_length=32, description="可选；客户端消息 id（非数据库 id）"),
    conversation_id: str = Body("", max_length=64, description="可选；客户端会话 id"),
    user=Depends(require_auth_enabled()),
):
    """把 score 写入 Langfuse。"""
    try:
        from chayuan.server.observability.langfuse_client import get_langfuse  # 见下方新增
        lf = get_langfuse()
        if lf is not None:
            lf.score(
                trace_id=trace_id,
                name=name or "user_feedback",
                value=score,
                comment=comment or None,
                observation_id=message_id or None,
            )
    except Exception as exc:  # noqa: BLE001
        # 不阻塞客户端：后续 outbox 会补传
        import logging
        logging.getLogger("chayuan.api.chat").warning("feedback v2 failed: %r", exc)
        return {"code": 1, "msg": str(exc)}
    return {"code": 0, "msg": "ok"}


# /feedback：旧端点保留兼容（数据库 message 评分链路）
@chat_router.post("/feedback", summary="返回llm模型对话评分（旧；分数 0-100）")
def chat_feedback_endpoint(
    message_id: str = Body(..., max_length=32, description="消息 ID"),
    score: int = Body(..., ge=0, le=100, description="评分 0 - 100"),
    reason: str = Body("", description="评分原因"),
    user=Depends(require_auth_enabled()),
):
    if user is not None and (user.get("role") if isinstance(user, dict) else "") != "admin":
        from chayuan.server.db.repository.message_repository import get_message_by_id
        try:
            m = get_message_by_id(message_id=message_id)
        except Exception:
            m = None
        if m is None:
            raise HTTPException(status_code=404, detail="message not found")
        owner = getattr(m, "user_id", None)
        uid = user.get("id") if isinstance(user, dict) else None
        if owner is not None and owner != uid:
            raise HTTPException(status_code=403, detail="message not owned by current user")
    return chat_feedback(message_id=message_id, score=score, reason=reason)


# /kb_chat /file_chat：签名很长且走流式，用 dependencies 在 FastAPI 层前置校验鉴权，
# 不改写原 handler 的参数解析；KB 级 ACL 在更精细的 `/knowledge_base/{mode}/{param}/chat/completions` 端点上做。
chat_router.post(
    "/kb_chat", summary="知识库对话",
    dependencies=[Depends(require_auth_enabled())],
)(kb_chat)


# ---------------------------------------------------------------------------
# P1-6：统一 ChatGraph 入口（新）。
# 路径保持与老接口平行，方便前端灰度切换；feature flag：
#   Settings.basic_settings.USE_CHAT_GRAPH=true 时会把 /chat/chat/completions
#   也自动路由到 ChatGraph（见下方 universal_chat）。
# ---------------------------------------------------------------------------

@chat_router.post("/v2/chat", summary="[新] ChatGraph 统一对话入口（LangGraph + 治理）")
async def chatgraph_endpoint(
    query: str = Body(..., description="用户输入"),
    mode: str = Body("", description="llm/agent/kb/file/search_engine/multi_source/vision；留空自动判定"),
    history: List[Dict] = Body(default_factory=list),
    stream: bool = Body(True),
    model: str = Body(""),
    temperature: float = Body(0.3),
    max_tokens: int = Body(None),
    prompt_name: str = Body("default"),
    kb_name: str = Body(""),
    kb_names: List[str] = Body(default_factory=list),
    ku_ids: List[str] = Body(default_factory=list,
                             description="P0 多源:KU 宇宙的 ku_id 列表(doc:<kb>/src:<id>);"
                                         "非空时 KBHandler 走 _process_one_ku 多源聚合"),
    source_ids: List[int] = Body(default_factory=list),
    select_all_sources: bool = Body(False),
    file_chat_id: str = Body(""),
    search_engine: str = Body(""),
    image_url: str = Body(""),
    image_urls: List[str] = Body(default_factory=list, description="多图 vision；非空时优先于 image_url"),
    top_k: int = Body(5),
    score_threshold: float = Body(2.0),
    rewrite_strategy: Optional[str] = Body(
        None,
        description="P2:Query 改写策略 auto/passthrough/rewrite/multi_query/hyde/decompose/off",
    ),
    use_hybrid: Optional[bool] = Body(None, description="按请求覆盖 hybrid;None=全局默认"),
    use_rerank: Optional[bool] = Body(None, description="按请求覆盖 rerank;None=全局默认"),
    tools: List[str] = Body(default_factory=list),
    use_mcp: bool = Body(False),
    conversation_id: str = Body(""),
    governance_enabled: bool = Body(True),
    deep_think: bool = Body(False, description="深度思考开关 — 按模型族注入 enable_thinking / thinking 等参数"),
    request: Request = None,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.chat.graph import ChatMode, ChatRequest, run_chat_stream, run_chat_sync
    from sse_starlette.sse import EventSourceResponse

    try:
        _mode = ChatMode(mode) if mode else None
    except Exception:
        _mode = None

    uid = (user or {}).get("id") if isinstance(user, dict) else None
    uname = str((user or {}).get("username") or "") if isinstance(user, dict) else ""
    urole = str((user or {}).get("role") or "") if isinstance(user, dict) else ""

    # image_urls 优先；fallback 到单图 image_url；都无则 None
    _images: List[str] = list(image_urls or [])
    if not _images and image_url:
        _images = [image_url]
    _primary_image = _images[0] if _images else None

    req = ChatRequest(
        query=query, mode=_mode, history=history or [], stream=stream, model=model,
        temperature=float(temperature),
        max_tokens=(int(max_tokens) if max_tokens else None),
        prompt_name=prompt_name,
        kb_name=kb_name or None, kb_names=list(kb_names or []),
        ku_ids=list(ku_ids or []),
        source_ids=[int(x) for x in (source_ids or [])],
        select_all_sources=bool(select_all_sources),
        file_chat_id=file_chat_id or None,
        search_engine=search_engine or None,
        image_url=_primary_image,
        image_urls=_images,
        top_k=int(top_k or 5), score_threshold=float(score_threshold),
        rewrite_strategy=rewrite_strategy,
        use_hybrid=use_hybrid, use_rerank=use_rerank,
        tools=list(tools or []), use_mcp=bool(use_mcp),
        user_id=int(uid) if uid is not None else None,
        username=uname, user_role=urole,
        conversation_id=conversation_id or "",
        request_id=str(getattr(request.state, "request_id", "")) if request else "",
        governance_enabled=bool(governance_enabled),
        deep_think=bool(deep_think),
    )

    if stream:
        return EventSourceResponse(run_chat_stream(req))
    return await run_chat_sync(req)


@chat_router.post(
    "/v2/chat/resume",
    summary="恢复一个被 HIL 中断的 agent 执行（T6）",
)
async def chatgraph_resume(
    conversation_id: str = Body(..., description="对应 thread_id；与发起调用时一致"),
    approved: bool = Body(True, description="是否批准继续执行工具调用"),
    patch: Dict[str, Any] = Body(default_factory=dict,
                                  description="可选：覆盖 state 中的字段（如修改工具参数）"),
    user=Depends(require_auth_enabled()),
):
    """HIL：在 ``interrupt_before=['tools']`` 等场景下，agent 会停在等待审批的状态，
    前端拿到 ``conversation_id`` 后调此接口选择继续或拒绝。需要 checkpointer 已启用
    （``CHAYUAN_AGENT_CHECKPOINTER=memory|postgres``）。
    """
    try:
        from chayuan.server.shared.agent_factory import _default_checkpointer
        cp = _default_checkpointer()
        if cp is None:
            return {"code": 1, "msg": "checkpointer 未启用；请先设置 CHAYUAN_AGENT_CHECKPOINTER"}
        # 这里不直接运行 agent；前端在 resume 后重新发起 /v2/chat
        # （带同 conversation_id）即可让 LangGraph 从 checkpoint 续跑。
        # 若 `approved=False`，把一条"工具调用被拒"的系统消息写回 state。
        if not approved:
            try:
                cp.put(
                    {"configurable": {"thread_id": str(conversation_id)}},
                    checkpoint={"channel_values": {"messages": [
                        {"role": "system",
                         "content": "人工已拒绝工具调用；请基于已有信息给出答复。"}
                    ]}},
                    metadata={"source": "hil_reject"},
                )
            except Exception as e:  # noqa: BLE001
                return {"code": 1, "msg": f"reject 失败：{e!r}"}
        if patch:
            try:
                cp.put(
                    {"configurable": {"thread_id": str(conversation_id)}},
                    checkpoint={"channel_values": patch},
                    metadata={"source": "hil_patch"},
                )
            except Exception as e:  # noqa: BLE001
                return {"code": 1, "msg": f"patch 失败：{e!r}"}
        return {"code": 0, "msg": "ready to resume", "data": {"conversation_id": conversation_id}}
    except Exception as e:  # noqa: BLE001
        return {"code": 1, "msg": f"{type(e).__name__}: {e}"}


chat_router.post(
    "/file_chat", summary="文件对话",
    dependencies=[Depends(require_auth_enabled())],
)(file_chat)


@chat_router.get("/conversations", summary="列出当前用户的会话")
def list_my_conversations(
    user=Depends(require_auth_enabled()),
    limit: int = 100, offset: int = 0,
) -> List[Dict]:
    """未登录时按当前策略返回空列表；admin 会看到全部用户的会话。"""
    if user is None:
        return []
    uid = user.get("id") if isinstance(user, dict) else None
    if (user.get("role") if isinstance(user, dict) else "") == "admin":
        uid = None  # admin 全部
    return list_conversations_for_user(user_id=uid, limit=limit, offset=offset)


def _assert_conv_owner(user, conv_id: str):
    """归属校验 helper：admin 放行；其他必须 owner 匹配。"""
    conv = get_conversation_by_id(conversation_id=conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if user is None:
        return conv
    role = user.get("role") if isinstance(user, dict) else None
    uid = user.get("id") if isinstance(user, dict) else None
    if role != "admin" and conv.user_id not in (None, uid):
        raise HTTPException(
            status_code=403, detail="conversation not owned by current user",
        )
    return conv


@chat_router.delete("/conversations/{conversation_id}", summary="删除会话（包括消息）")
def delete_my_conversation(
    conversation_id: str,
    user=Depends(require_auth_enabled()),
):
    _assert_conv_owner(user, conversation_id)
    deleted = delete_conversation(conversation_id=conversation_id)
    return {"code": 0, "msg": "ok", "deleted": int(deleted or 0)}


@chat_router.patch("/conversations/{conversation_id}", summary="重命名会话")
def rename_my_conversation(
    conversation_id: str,
    name: str = Body(..., embed=True, max_length=64, description="新标题"),
    user=Depends(require_auth_enabled()),
):
    _assert_conv_owner(user, conversation_id)
    ok = rename_conversation(conversation_id=conversation_id, name=name)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"code": 0, "msg": "ok"}


@chat_router.post("/chat/completions", summary="兼容 openai 的统一 chat 接口")
async def chat_completions(
    request: Request,
    body: OpenAIChatInput,
    user=Depends(require_auth_enabled()),
) -> Dict:
    """
    请求参数与 openai.chat.completions.create 一致，可以通过 extra_body 传入额外参数
    tools 和 tool_choice 可以直接传工具名称，会根据项目里包含的 tools 进行转换
    通过不同的参数组合调用不同的 chat 功能：
    - tool_choice
        - extra_body 中包含 tool_input: 直接调用 tool_choice(tool_input)
        - extra_body 中不包含 tool_input: 通过 agent 调用 tool_choice
    - tools: agent 对话
    - 其它：LLM 对话
    以后还要考虑其它的组合（如文件对话）
    返回与 openai 兼容的 Dict
    """
    # ---- 统一调度入口（T5：dispatcher 为默认且唯一链路）----
    # 把 OpenAI body + 请求三态身份翻译成 UnifiedChatRequest，进 ChatGraph。
    # vision 旁路 / 语义缓存 / message 落库 / history 还原全部沉到 dispatcher 层，
    # 路由只负责协议适配；dispatcher 异常时才回退老路径（留作 safety net）。
    try:
        from chayuan.server.chat.core import (
            AuthContext, dispatch_chat, from_openai_body, is_dispatcher_enabled,
        )
        if is_dispatcher_enabled():
            # tool_choice / tools 名字 → openai 工具描述（与下方旧逻辑同源）
            if isinstance(body.tool_choice, str):
                if t := get_tool(body.tool_choice):
                    body.tool_choice = {
                        "function": {"name": t.name}, "type": "function",
                    }
            if isinstance(body.tools, list):
                for i in range(len(body.tools)):
                    if isinstance(body.tools[i], str):
                        if t := get_tool(body.tools[i]):
                            body.tools[i] = {
                                "type": "function",
                                "function": {
                                    "name": t.name,
                                    "description": t.description,
                                    "parameters": t.args,
                                },
                            }
            if body.max_tokens in [None, 0]:
                body.max_tokens = Settings.model_settings.MAX_TOKENS
            dto = from_openai_body(body)
            auth = AuthContext.from_request(request)
            return await dispatch_chat(dto, auth)
    except Exception as _e:  # noqa: BLE001
        logger.warning("dispatcher path failed, fallback to legacy: %r", _e)

    # 当调用本接口且 body 中没有传入 "max_tokens" 参数时, 默认使用配置中定义的值
    if body.max_tokens in [None, 0]:
        body.max_tokens = Settings.model_settings.MAX_TOKENS

    extra = {**body.model_extra} or {}
    hint = str(extra.get("_selected_llm_for_chain") or "").strip()
    # 部分客户端未传 model 时，用 extra_body 中的显式选择与 flatten/chat 对齐
    if not (body.model or "").strip():
        body.model = hint or get_default_llm()
    for key in list(extra):
        delattr(body, key)

    # check tools & tool_choice in request body
    if isinstance(body.tool_choice, str):
        if t := get_tool(body.tool_choice):
            body.tool_choice = {"function": {"name": t.name}, "type": "function"}
    if isinstance(body.tools, list):
        for i in range(len(body.tools)):
            if isinstance(body.tools[i], str):
                if t := get_tool(body.tools[i]):
                    body.tools[i] = {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.args,
                        },
                    }

    conversation_id = extra.get("conversation_id")
    uid = (user or {}).get("id") if isinstance(user, dict) else None

    # ---- Vision 旁路：messages[-1].content 是数组且含 image_url 时，不走 agent ----
    # 走 /v1/chat/completions 等价路径，直接把多模态请求透传给平台；否则 agent 会把
    # list content 字符串化成乱码，模型读到的就不是一张图而是 "[{'type'...}]" 文本。
    if _is_vision_request(body.messages):
        # vision 不走 tool_calls / agent 协议
        body.tools = None
        body.tool_choice = None
        logger.info(
            "[vision] bypassing agent for model=%s (content parts=%d)",
            body.model,
            len(body.messages[-1].get("content") or []),
        )
        try:
            async with get_model_client(body.model) as client:
                return await openai_request(client.chat.completions.create, body)
        except Exception as e:  # noqa: BLE001
            logger.exception("[vision] bypass failed: %s", e)
            raise HTTPException(status_code=500, detail=f"vision passthrough failed: {e}")

    # ---- 语义缓存：仅非流式 & 单轮查询命中 ----
    from chayuan.server.resilience import semcache_get, semcache_set
    last_query = ""
    try:
        last_query = (body.messages[-1].get("content") or "") if body.messages else ""
    except Exception:
        last_query = ""
    cache_scope = {
        "route": "chat_completions",
        "temperature": body.temperature,
        "tools": [t.get("function", {}).get("name") for t in (body.tools or [])] if body.tools else [],
    }
    if not body.stream and len(body.messages or []) <= 2 and last_query:
        hit = semcache_get(
            query=last_query, user_id=uid, kb=None,
            model=(body.model or "").strip() or None, extra=cache_scope,
        )
        if hit is not None:
            logger.info("semcache hit (chat): uid=%s", uid)
            return hit

    # 多用户隔离：拿到已存在会话时，校验归属；admin 放行
    if conversation_id and uid is not None:
        try:
            conv = get_conversation_by_id(conversation_id=conversation_id)
        except Exception:
            conv = None
        if conv is not None and conv.user_id not in (None, uid):
            if (user or {}).get("role") != "admin":
                raise HTTPException(
                    status_code=403, detail="conversation not owned by current user",
                )

    raw_chat_cfg = extra.get("chat_model_config") or {}
    selected_model = (body.model or "").strip() or hint or get_default_llm()

    try:
        message_id = (
            add_message_to_db(
                chat_type="agent_chat",
                query=body.messages[-1]["content"],
                conversation_id=conversation_id,
                metadata={"model": selected_model} if selected_model else {},
                user_id=uid,
            )
            if conversation_id
            else None
        )
    except Exception as e:
        logger.warning(f"failed to add message to db: {e}")
        message_id = None
    chat_model_config = flatten_chat_model_config_for_inference(
        raw_chat_cfg, selected_model
    )
    tool_config = {}
    if body.tools:
        tool_names = [x["function"]["name"] for x in body.tools]
        tool_config = {name: get_tool_config(name) for name in tool_names}

    # 若客户端没传 conversation_id（新会话首条 / 匿名会话），而 messages 里带了多轮
    # 上下文（前端 chat_box 历史 + 当前用户句），直接把历史下传给 chat()，避免
    # agent 拿不到任何 chat_history、只看到 system prompt + 单句用户输入时被带偏。
    # conversation_id 有值时仍走 DB filter_message 的原有路径（两条链路互斥）。
    explicit_history = []
    if not conversation_id and body.messages and len(body.messages) > 1:
        for m in body.messages[:-1]:
            role = (m.get("role") or "").strip()
            content = m.get("content")
            if role in {"user", "assistant", "system"} and content:
                explicit_history.append({"role": role, "content": content})

    result = await chat(
        query=body.messages[-1]["content"],
        metadata=extra.get("metadata", {}),
        conversation_id=extra.get("conversation_id", ""),
        message_id=message_id,
        history_len=-1,
        history=explicit_history,
        stream=body.stream,
        chat_model_config=chat_model_config,
        tool_config=tool_config,
        use_mcp=extra.get("use_mcp", False),
        max_tokens=body.max_tokens,
    )
    # 非流式成功结果缓存一份；失败 / 含错误的结果不入缓存
    if not body.stream and last_query and isinstance(result, dict):
        try:
            if result.get("choices"):
                semcache_set(
                    query=last_query, response=result, user_id=uid, kb=None,
                    model=(body.model or "").strip() or None, extra=cache_scope,
                )
        except Exception:
            logger.debug("semcache_set(chat) failed", exc_info=True)
    return result
