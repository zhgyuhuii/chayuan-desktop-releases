import asyncio
import json
import uuid
import os
import traceback
from chayuan.server.db.repository.message_repository import filter_message
from typing import Any, AsyncIterable, Dict, List, Optional, Union, Tuple
from langchain_core.load import dumpd, dumps, load, loads

from fastapi import Body
from langchain_classic.chains import LLMChain
from langchain_core.prompts import ChatPromptTemplate

from chayuan.server.agents_registry.agents_registry import agents_registry
from sse_starlette.sse import EventSourceResponse

from chayuan.server.db.repository.mcp_connection_repository import get_enabled_mcp_connections
from chayuan.settings import Settings
from chayuan.server.api_server.api_schemas import OpenAIChatOutput
from langchain_chayuan.callbacks.agent_callback_handler import (
    AgentExecutorAsyncIteratorCallbackHandler,
    AgentStatus,
)
from langchain_chayuan.agents.platform_tools import PlatformToolsAction, PlatformToolsFinish, \
    PlatformToolsActionToolStart, PlatformToolsActionToolEnd, PlatformToolsLLMStatus
from chayuan.server.chat.utils import History
from chayuan.server.db.repository import add_message_to_db, update_message

from langchain_chayuan import ChatPlatformAI, PlatformToolsRunnable
from chayuan.server.utils import (
    MsgType,
    get_ChatOpenAI,
    get_prompt_template,
    get_tool,
    wrap_done,
    get_default_llm,
    build_logger,
    get_ChatPlatformAIParams
)

logger = build_logger()


# ─── 模型类型识别 ────────────────────────────────────────────────────────────
# 按模型名前缀判定其真实用途(chat / t2i / vl / asr / tts / embedding / rerank)。
# 阿里百炼把所有模型混在同一个 API key 下,前端模型广场如果没把 qwen-image-*
# 这种 t2i 模型放进 text2image_models 字段而是误塞进 llm_models,用户选完发起
# chat 就会撞 OpenAI 兼容路径的 4xx。这里在 chat 入口和错误翻译两处都用上
# 这个识别,做"防御+解释"双层保险。
#
# 命名规则参考阿里官方:https://www.alibabacloud.com/help/en/model-studio/qwen-image-api
# 以及 model_studio_zh 文档(qwen-vl-* / qwen-omni-* / qwen-asr-* / qvq-* /
# qwen-image-* / qwen-image-edit-* / qwen-tts-* / wanx-* / text-embedding-v*)。
_NON_CHAT_PATTERNS: list[tuple[str, str]] = [
    # (substring,           category 用户看的标签)
    ("qwen-image-edit",     "图像编辑(image-edit)"),
    ("qwen-image",          "文生图(text-to-image)"),
    ("wanx",                "文生图/视频(Wanxiang)"),
    ("qvq-",                "视觉推理(多模态,需 content 列表)"),
    ("qwen-vl",             "视觉语言(多模态,需 content 列表)"),
    ("qwen-omni",           "全模态(多模态,需 content 列表)"),
    ("qwen-audio",          "音频多模态(需 content 列表)"),
    ("qwen-asr",            "语音识别(ASR,需走 /audio/transcriptions)"),
    ("qwen3-asr",           "语音识别(ASR,需走 /audio/transcriptions)"),
    ("qwen-tts",            "语音合成(TTS,需走 /audio/speech)"),
    ("text-embedding-v",    "文本向量(embedding,不是对话模型)"),
    ("gte-rerank",          "重排(rerank,不是对话模型)"),
    ("gte-",                "可能是 GTE 向量/重排模型,不是对话模型"),
]


def classify_non_chat_model(model_name: str) -> tuple[str, str] | None:
    """模型名匹配到已知非聊天模型时返回 (pattern, 类别中文)。匹配不到返 None。

    保守策略:只对前缀显著就是 t2i/vl/asr/tts/embedding/rerank 的模型返非 None;
    其它(包括 qwen-turbo / qwen-max / qwen-plus / qwen-long / qwq / qwen2.x /
    qwen3.x 文本系列)一律视为对话兼容,留给 LLM 自己回话。
    """
    if not model_name:
        return None
    low = model_name.lower().strip()
    for pat, label in _NON_CHAT_PATTERNS:
        if low.startswith(pat) or f"/{pat}" in low:  # 兼容 'provider/qwen-image-max' 这种带前缀的写法
            return pat, label
    return None


def _translate_chat_error(raw: str, model_name: str = "") -> str:
    """把上游模型/SDK 抛出的原始报错翻译成给最终用户看的中文提示。

    兜底三类常见误用,避免前端直接把 `BadRequestError(...)` 当错误红框甩出:
      1. 多模态模型 当纯文本对话 — DashScope 报 "Input should be a valid list:
         input.messages.0.content" 或 "invalid_parameter_error"。
      2. t2i / image-edit / asr / tts / embedding / rerank 走 chat — 报
         "content parameter's length invalid" 或 endpoint 拒绝。
      3. 上游 key/quota 缺失,引导去配 API key。

    其它情况原样回传,保留排查信息。
    """
    low = raw.lower()
    m = model_name or "所选模型"

    # —— 先按模型名识别(最精准,不依赖上游具体错误文案) ——
    classified = classify_non_chat_model(model_name)
    if classified is not None:
        _pat, label = classified
        return (
            f"模型「{m}」是 {label},不能走纯文本对话接口。\n"
            f"  • 改用对话模型 — 阿里百炼建议 qwen-turbo / qwen-plus / qwen-max,"
            f"或本地 chayuan-chat / qwen2.5\n"
            f"  • 若想用此模型的专属能力(画图 / 语音 / 视觉等),请走对应工具入口,"
            f"不要在普通对话里直接选\n"
            f"  • 也可在「设置 → 模型广场」把它从聊天模型清单里移除\n"
            f"原始错误:{raw[:200]}"
        )

    # —— 模型名没匹配上,再按错误文案兜底 ——
    looks_multimodal = (
        ("should be a valid list" in low and "messages" in low and "content" in low)
        or ("invalid_parameter_error" in low and "messages" in low)
    )
    if looks_multimodal:
        return (
            f"模型「{m}」要求多模态 content(list),不能直接用纯文本对话。\n"
            f"  • 改用对话模型(如 qwen-turbo / qwen-max / 智谱 GLM / 内置 chayuan-chat)\n"
            f"  • 或在「设置 → 模型广场」把它移出聊天模型清单"
        )
    # 阿里百炼 t2i / 错位 endpoint 常见错:"content parameter's length invalid"
    looks_wrong_endpoint = (
        "content parameter" in low and "length invalid" in low
    ) or (
        "parameter" in low and "length invalid" in low
    )
    if looks_wrong_endpoint:
        return (
            f"模型「{m}」不支持 OpenAI 兼容的对话接口,大概率是文生图/语音/嵌入类\n"
            f"专用模型被误当聊天模型用。请到「设置 → 模型广场」把它从聊天清单移除,\n"
            f"换回 qwen-turbo / qwen-plus / qwen-max 等对话模型。\n"
            f"原始错误:{raw[:200]}"
        )
    if "no api key" in low or "invalid api key" in low or ("401" in low and "unauthorized" in low):
        return (
            "上游模型 API key 缺失或无效。请到「设置 → 模型广场」对应厂商填一个有效 key,"
            "或切到「本地模型服务 / chayuan-chat」。"
        )
    return raw


def flatten_chat_model_config_for_inference(
    chat_model_config: dict, selected_model: str
) -> dict:
    """
    前端（WebUI）传入的 chat_model_config 与 LLM_MODEL_CONFIG 同形：每种 model_type 下
    往往是 { 模型名: { model, temperature, ... } } 的映射；而 create_models_from_config
    需要 { model_type: { model: str, ... } } 单层 dict。若不解开，params.get(\"model\") 恒为空，
    会退回 get_default_llm()（例如始终 deepseek-chat）。

    根据 OpenAI 请求体中的 model（用户当前选择的模型）展开为单层配置。
    """
    from copy import deepcopy

    defaults = Settings.model_settings.LLM_MODEL_CONFIG or {}
    if not isinstance(chat_model_config, dict):
        chat_model_config = {}
    selected_model = (selected_model or "").strip() or get_default_llm()

    def is_per_model_map(d: dict) -> bool:
        if not d or "model" in d:
            return False
        return all(isinstance(v, dict) for v in d.values())

    out: dict = {}
    for model_type, default_cfg in defaults.items():
        raw = chat_model_config.get(model_type)
        if raw is None:
            cfg = deepcopy(default_cfg) if isinstance(default_cfg, dict) else {}
            if model_type in ("llm_model", "action_model") and isinstance(cfg, dict):
                cfg["model"] = selected_model
            out[model_type] = cfg
            continue

        if isinstance(raw, dict) and is_per_model_map(raw):
            if selected_model in raw:
                cfg = deepcopy(raw[selected_model])
            elif raw:
                # 不要用 next(iter(raw)) 的模板当「当前模型」：键顺序不定，且易与仅配置了 deepseek 条目的前端组合出错
                cfg = deepcopy(default_cfg) if isinstance(default_cfg, dict) else {}
                tmpl = raw.get(next(iter(raw)))
                if isinstance(tmpl, dict):
                    for _k, _v in tmpl.items():
                        if _k != "model":
                            cfg[_k] = _v
            else:
                cfg = deepcopy(default_cfg) if isinstance(default_cfg, dict) else {}
            if model_type in ("llm_model", "action_model") and isinstance(cfg, dict):
                cfg["model"] = selected_model
            out[model_type] = cfg
            continue

        cfg = deepcopy(raw) if isinstance(raw, dict) else {}
        if model_type in ("llm_model", "action_model") and isinstance(cfg, dict):
            cfg["model"] = selected_model
        out[model_type] = cfg

    for model_type, raw in chat_model_config.items():
        if model_type in out or not isinstance(raw, dict):
            continue
        if is_per_model_map(raw):
            default_cfg = (Settings.model_settings.LLM_MODEL_CONFIG or {}).get(model_type, {})
            if selected_model in raw:
                cfg = deepcopy(raw[selected_model])
            elif raw:
                cfg = deepcopy(default_cfg) if isinstance(default_cfg, dict) else {}
                tmpl = raw.get(next(iter(raw)))
                if isinstance(tmpl, dict):
                    for _k, _v in tmpl.items():
                        if _k != "model":
                            cfg[_k] = _v
            else:
                cfg = deepcopy(default_cfg) if isinstance(default_cfg, dict) else {}
            if model_type in ("llm_model", "action_model") and isinstance(cfg, dict):
                cfg["model"] = selected_model
            out[model_type] = cfg
        else:
            cfg = deepcopy(raw)
            if model_type in ("llm_model", "action_model") and isinstance(cfg, dict):
                cfg["model"] = selected_model
            out[model_type] = cfg
    return out


def create_models_from_config(configs, callbacks, stream, max_tokens):
    configs = configs or Settings.model_settings.LLM_MODEL_CONFIG
    if not isinstance(configs, dict):
        configs = Settings.model_settings.LLM_MODEL_CONFIG
    models = {}
    prompts = {}
    for model_type, params in configs.items():
        if not isinstance(params, dict):
            params = {}
        model_name = params.get("model", "").strip() or get_default_llm()
        callbacks = callbacks if params.get("callbacks", False) else None
        # 判断是否传入 max_tokens 的值, 如果传入就按传入的赋值(api 调用且赋值), 如果没有传入则按照初始化配置赋值(ui 调用或 api 调用未赋值)
        max_tokens_value = max_tokens if max_tokens is not None else params.get("max_tokens", 1000)
        if model_type == "action_model":

            llm_params = get_ChatPlatformAIParams(
                model_name=model_name,
                temperature=params.get("temperature", 0.5),
                max_tokens=max_tokens_value,
            )
            model_instance = ChatPlatformAI(**llm_params)
        else:
            model_instance = get_ChatOpenAI(
                model_name=model_name,
                temperature=params.get("temperature", 0.5),
                max_tokens=max_tokens_value,
                callbacks=callbacks,
                streaming=stream,
                local_wrap=True,
            )
        models[model_type] = model_instance
        prompt_name = params.get("prompt_name", "default")
        prompt_template = get_prompt_template(type=model_type, name=prompt_name)
        if prompt_template is None and prompt_name != "default":
            logger.warning(
                f"prompt template missing for type={model_type}, name={prompt_name}; fallback to default"
            )
            prompt_template = get_prompt_template(type=model_type, name="default")
        prompts[model_type] = prompt_template
    return models, prompts


async def create_models_chains(
    history_len, prompts, models, tools, callbacks, conversation_id, metadata,
    use_mcp: bool = False,
    explicit_history: Optional[List[Dict[str, Any]]] = None,
):
    """
    explicit_history：客户端没传 conversation_id（新会话/匿名），但在 messages 里带了
    多轮上下文时由上游（chat_routes.chat_completions）透传过来；非空则跳过 DB 历史
    重建，直接作为 chat_history 使用，避免 agent 在无历史情况下被系统提示带偏。
    """
    # 从数据库获取conversation_id对应的 intermediate_steps 、 mcp_connections
    # 仅在走有 conversation_id 的分支时才去查 DB；没 conversation_id 时 DB 查询
    # 只会返回空列表并可能打多余 SQL，无必要。
    if conversation_id:
        messages = filter_message(
            conversation_id=conversation_id, limit=history_len
        )
    else:
        messages = []
    # 返回的记录按时间倒序，转为正序
    messages = list(reversed(messages))
    history: List[Union[List, Tuple]] = []
    if explicit_history:
        for m in explicit_history:
            history.append({"role": m["role"], "content": m["content"]})
    else:
        for message in messages:
            history.append({"role": "user", "content": message["query"]})
            history.append({"role": "assistant", "content":  message["response"]})

    if len(messages) > 0 and isinstance(messages[-1], dict):
        metadata = messages[-1].get("metadata") or {}
        intermediate_steps = loads(
            metadata.get("intermediate_steps"),
            valid_namespaces=[
                "langchain_chayuan",
                "langchain_chatchat",
                "agent_toolkits",
                "all_tools",
                "tool",
            ],
        ) if metadata.get("intermediate_steps") else []
    else:
        intermediate_steps = []
    llm = models["action_model"]
    llm.callbacks = callbacks
    connections = get_enabled_mcp_connections() or []
    
    # 转换为MCP连接格式，支持StdioConnection和SSEConnection类型
    mcp_connections = {}
    for conn in connections:
        if conn["transport"] == "stdio":
            conn_config = conn.get("config") or {}
            # StdioConnection类型
            mcp_connections[conn["server_name"]] = {
                "transport": "stdio",
                "command": conn_config.get("command", conn["args"][0] if conn["args"] else ""),
                "args": conn["args"][1:] if len(conn["args"]) > 1 else [],
                "env": conn["env"],
                "encoding": "utf-8",
                "encoding_error_handler": "strict"
            }
        elif conn["transport"] == "sse":
            conn_config = conn.get("config") or {}
            # SSEConnection类型
            mcp_connections[conn["server_name"]] = {
                "transport": "sse",
                "url": conn_config.get("url", ""),
                "headers": conn_config.get("headers", {}),
                "timeout": conn.get("timeout", 30.0),
                "sse_read_timeout": conn.get("sse_read_timeout", 60.0)
            }
    
    agent_executor = await PlatformToolsRunnable.create_agent_executor_async(
        agent_type="platform-knowledge-mode",
        agents_registry=agents_registry,
        llm=llm,
        tools=tools,
        history=history,
        intermediate_steps=intermediate_steps,
        mcp_connections=mcp_connections if use_mcp else {},
    )

    full_chain = {"chat_input": lambda x: x["input"]} | agent_executor

    return full_chain, agent_executor


async def chat(
        query: str = Body(..., description="用户输入", examples=["恼羞成怒"]),
        metadata: dict = Body({}, description="附件，可能是图像或者其他功能", examples=[]),
        conversation_id: str = Body("", description="对话框ID"),
        message_id: str = Body(None, description="数据库消息ID"),
        history_len: int = Body(-1, description="从数据库中取历史消息的数量"),
        history: List[Dict[str, Any]] = Body(
            None,
            description=(
                "显式多轮历史（[{role, content}]），非空时跳过 DB 历史重建；"
                "用于 conversation_id 为空的匿名/新会话从请求 messages 还原上下文。"
            ),
        ),
        stream: bool = Body(True, description="流式输出"),
        chat_model_config: dict = Body({}, description="LLM 模型配置", examples=[]),
        tool_config: dict = Body({}, description="工具配置", examples=[]),
        use_mcp: bool = Body(False, description="使用MCP"),
        max_tokens: int = Body(None, description="LLM最大token数配置", example=4096),
):
    """Agent 对话"""

    async def chat_iterator_event() -> AsyncIterable[OpenAIChatOutput]:
        try:
            callbacks = []
            safe_chat_model_config = chat_model_config if isinstance(chat_model_config, dict) else {}
            safe_tool_config = tool_config if isinstance(tool_config, dict) else {}

            # Langfuse：统一通过 langfuse_integration 注入；
            # 未配置 / 未装 / 被 CHAYUAN_LANGFUSE_DISABLE 禁用时 自动 no-op。
            try:
                from chayuan.server.observability.langfuse_integration import (
                    inject_into_callbacks,
                )
                callbacks = inject_into_callbacks(callbacks)
            except Exception:  # noqa: BLE001
                pass

            models, prompts = create_models_from_config(
                callbacks=callbacks, configs=safe_chat_model_config, stream=stream, max_tokens=max_tokens
            )
            # 预检:选了 t2i / VL / ASR / TTS / embedding / rerank 等非聊天模型时
            # 直接返友好提示,不浪费一次上游 4xx。上游错误固然有 _translate_chat_error
            # 兜底,但预检能省一次网络往返 + 不让上游 quota 因误用而扣费。
            try:
                pre_model = models["llm_model"].model_name
            except Exception:  # noqa: BLE001
                pre_model = ""
            classified = classify_non_chat_model(pre_model)
            if classified is not None:
                _pat, label = classified
                msg = (
                    f"模型「{pre_model}」是 {label},不能走纯文本对话接口。\n"
                    f"  • 改用对话模型 — 阿里百炼建议 qwen-turbo / qwen-plus / qwen-max,"
                    f"或本地 chayuan-chat / qwen2.5\n"
                    f"  • 若想用此模型的专属能力(画图 / 语音 / 视觉等),请走对应工具入口,"
                    f"不要在普通对话里直接选\n"
                    f"  • 也可在「设置 → 模型广场」把它从聊天模型清单里移除"
                )
                logger.warning("[chat] 拒绝非聊天模型走 chat 路径: model=%s label=%s", pre_model, label)
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"error": {"type": "ModelNotChatCompatible", "message": msg}},
                        ensure_ascii=False,
                    ),
                }
                return
            all_tools = get_tool().values()
            tools = [tool for tool in all_tools if tool.name in safe_tool_config]
            tools = [t.copy(update={"callbacks": callbacks}) for t in tools]
            full_chain, agent_executor = await create_models_chains(
                prompts=prompts,
                models=models,
                conversation_id=conversation_id,
                tools=tools,
                callbacks=callbacks,
                history_len=history_len,
                metadata=metadata,
                use_mcp=use_mcp,
                explicit_history=history,
            )
            try:
                _model_name = models["llm_model"].model_name
            except Exception:  # noqa: BLE001
                _model_name = ""
            message_id = add_message_to_db(
                    chat_type="llm_chat",
                    query=query,
                    conversation_id=conversation_id,
                    metadata={"model": _model_name} if _model_name else {},
            )
            chat_iterator = full_chain.invoke({
                "input": query
            })
            last_tool = {}
            async for item in chat_iterator:

                data = {}

                data["status"] = item.status
                data["tool_calls"] = []
                data["message_type"] = MsgType.TEXT
                if isinstance(item, PlatformToolsAction):
                    logger.info("PlatformToolsAction:" + str(item.to_json()))
                    data["text"] = item.log
                    tool_call = {
                        "index": 0,
                        "id": item.run_id,
                        "type": "function",
                        "function": {
                            "name": item.tool,
                            "arguments": item.tool_input,
                        },
                        "tool_output": None,
                        "is_error": False,
                    }
                    data["tool_calls"].append(tool_call)

                elif isinstance(item, PlatformToolsFinish):
                    data["text"] = item.log

                    last_tool.update(
                        tool_output=item.return_values["output"],
                    )
                    data["tool_calls"].append(last_tool)

                    try:
                        tool_output = json.loads(item.return_values["output"])
                        if isinstance(tool_output, dict) and (message_type := tool_output.get("message_type")):
                            data["message_type"] = message_type
                    except:
                        ...

                elif isinstance(item, PlatformToolsActionToolStart):
                    logger.info("PlatformToolsActionToolStart:" + str(item.to_json()))

                    last_tool = {
                        "index": 0,
                        "id": item.run_id,
                        "type": "function",
                        "function": {
                            "name": item.tool,
                            "arguments": item.tool_input,
                        },
                        "tool_output": None,
                        "is_error": False,
                    }
                    data["tool_calls"].append(last_tool)

                elif isinstance(item, PlatformToolsActionToolEnd):
                    logger.info("PlatformToolsActionToolEnd:" + str(item.to_json()))
                    last_tool.update(
                        tool_output=item.tool_output,
                        is_error=False,
                    )
                    data["tool_calls"] = [last_tool]

                    last_tool = {}
                    try:
                        tool_output = json.loads(item.tool_output)
                        if isinstance(tool_output, dict) and (message_type := tool_output.get("message_type")):
                            data["message_type"] = message_type
                    except:
                        ...
                elif isinstance(item, PlatformToolsLLMStatus):

                    data["text"] = item.text

                ret = OpenAIChatOutput(
                    id=f"chat{uuid.uuid4()}",
                    object="chat.completion.chunk",
                    content=data.get("text", ""),
                    role="assistant",
                    tool_calls=data["tool_calls"],
                    model=models["llm_model"].model_name,
                    status=data["status"],
                    message_type=data["message_type"],
                    message_id=message_id,
                    class_name=item.class_name()
                )
                yield ret.model_dump_json()

            string_intermediate_steps = dumps(agent_executor.intermediate_steps, pretty=True)

            response_text = ""
            if agent_executor.history and isinstance(agent_executor.history[-1], dict):
                response_text = agent_executor.history[-1].get("content", "")
            update_message(
                message_id,
                response_text,
                metadata={
                    "intermediate_steps": string_intermediate_steps,
                    "model": _model_name,
                }
            )
             
        except asyncio.exceptions.CancelledError:
            logger.warning("streaming progress has been interrupted by user.")
            return
        except Exception as e:
            # logger.exception 会把 traceback 附上；colors=False 已在 build_logger 里绑死，
            # 避免 traceback 里 <listcomp>/<module> 被 loguru colorizer 当色彩标签抛错。
            logger.exception(f"error in chat: {type(e).__name__}: {e}")
            raw_msg = str(e) or repr(e)
            friendly_msg = _translate_chat_error(raw_msg, locals().get("_model_name") or "")
            # 回传给前端一段可识别的 SSE 错误事件；原先只发 {"error": "..."}，前端（openai
            # SDK）把它当成异常 payload 吞了，结果用户看到的是 RemoteProtocolError 而非真因。
            err_payload = {
                "error": {
                    "type": type(e).__name__,
                    "message": friendly_msg,
                }
            }
            yield {"event": "error", "data": json.dumps(err_payload, ensure_ascii=False)}
            return

    if stream:
        return EventSourceResponse(chat_iterator_event())
    else:
        ret = OpenAIChatOutput(
            id=f"chat{uuid.uuid4()}",
            object="chat.completion",
            content="",
            role="assistant",
            finish_reason="stop",
            tool_calls=[],
            status=AgentStatus.agent_finish,
            message_type=MsgType.TEXT,
            message_id=message_id,
        )

        async for chunk in chat_iterator_event():
            data = json.loads(chunk)
            if text := data["choices"][0]["delta"]["content"]:
                ret.content += text
            if data["status"] == AgentStatus.tool_end:
                ret.tool_calls += data["choices"][0]["delta"]["tool_calls"]
            ret.model = data["model"]
            ret.created = data["created"]

        return ret.model_dump()