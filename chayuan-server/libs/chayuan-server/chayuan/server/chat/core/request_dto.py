"""归一聊天请求 DTO + 各端 body → DTO 的转换器。

理念：``ChatGraph.ChatRequest`` 是图内部协议（强 dataclass），不适合直接绑到
HTTP 入参签名（参数太多、变更影响 router）。这里再加一层 :class:`UnifiedChatRequest`
作为"协议适配的最小目标"，让各路由不必关心 ChatGraph 内部字段名变化。

转换函数：
- :func:`from_openai_body`        ←  ``OpenAIChatInput``（chat_routes.chat_completions）
- :func:`from_kb_body`            ←  ``kb_chat`` 入参（local_kb / temp_kb / search_engine）
- :func:`from_file_body`          ←  ``file_chat`` 入参
- :func:`from_multi_source_body`  ←  ``multi_source_chat`` 入参

新增端点继续在这里追加映射函数即可，不污染 dispatcher / handler 层。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UnifiedChatRequest:
    """聚合所有入口的对话参数；字段对齐 ``chat.graph.ChatRequest``，
    多出的元数据（messages、metadata、conversation_id、tool_choice、extras）
    在适配函数里展开 / 透传。
    """

    # ---- 基础 ----
    query: str = ""
    mode: Optional[str] = None  # "llm" / "agent" / "kb" / "file" / "search_engine" / "multi_source" / "vision"
    history: List[Dict[str, str]] = field(default_factory=list)
    stream: bool = True
    model: str = ""
    temperature: float = 0.3
    max_tokens: Optional[int] = None
    prompt_name: str = "default"

    # ---- 模式专属 ----
    kb_name: Optional[str] = None
    kb_names: List[str] = field(default_factory=list)
    # P0:KU 宇宙的 ku_id 列表(`doc:<kb>` / `src:<id>`),非空时 KBHandler 走多源聚合
    ku_ids: List[str] = field(default_factory=list)
    source_ids: List[int] = field(default_factory=list)
    select_all_sources: bool = False
    file_chat_id: Optional[str] = None
    search_engine: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    """T10：语音对话音频 URL / 本地路径。dispatcher 检测后走 ASR 取 query 文本。"""
    video_url: Optional[str] = None
    """T10：视频理解 URL / 本地路径。dispatcher 走抽帧 + ASR + Vision 的 understand_video。"""
    modality: Optional[str] = None
    """显式模态：text / vision / audio / video；留空由 ModalityService.detect 判定。"""
    top_k: int = 5
    score_threshold: float = 2.0
    # P2:检索模式三件套(对齐 ChatRequest)
    rewrite_strategy: Optional[str] = None
    use_hybrid: Optional[bool] = None
    use_rerank: Optional[bool] = None

    # ---- Agent 专属 ----
    tools: List[str] = field(default_factory=list)
    tool_choice: Optional[Dict[str, Any]] = None
    use_mcp: bool = False
    tool_input: Dict[str, Any] = field(default_factory=dict)

    # ---- 治理 / 上下文 ----
    conversation_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    governance_enabled: bool = True

    # ---- 深度思考开关(对应 ChatComposer 顶部那个 ✨ 按钮) ----
    deep_think: bool = False
    """开启后,根据上游模型族注入对应的"思考"参数:
       - 阿里 Qwen3 / Qwen-Plus / Qwen-Max:extra_body={"enable_thinking": True}
       - Anthropic Claude(Sonnet 3.7+ / Opus 4+):extra_body={"thinking":{"type":"enabled","budget_tokens":N}}
       - OpenAI o-series / DeepSeek-Reasoner:本身就是推理模型,本字段不影响
       - 其它模型:noop(后端日志 INFO,UI 按钮仍可按但实际无效)"""

    # ---- 透传 / 调试 ----
    raw_messages: List[Dict[str, Any]] = field(default_factory=list)
    """OpenAI 风格的完整 messages 列表；vision 多模态 content 在此保留。"""

    extras: Dict[str, Any] = field(default_factory=dict)
    """OpenAI ``extra_body`` 透传（chat_model_config、_selected_llm_for_chain 等）。"""

    # ---- helpers ----

    @property
    def is_vision(self) -> bool:
        """检测最后一条用户消息是否含 image_url 多模态 content。"""
        if self.image_url:
            return True
        if not self.raw_messages:
            return False
        last = self.raw_messages[-1] if isinstance(self.raw_messages, list) else None
        if not isinstance(last, dict):
            return False
        content = last.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "image_url", "input_image", "image",
                ):
                    return True
        return False


# ---------------------------------------------------------------------------
# OpenAI body → DTO
# ---------------------------------------------------------------------------

def from_openai_body(body: Any) -> UnifiedChatRequest:
    """``OpenAIChatInput`` → DTO；保留 messages 用于 vision 与 history 还原。

    body 是 Pydantic 模型；这里用 ``model_dump`` 取值，避免直接 attr 访问对
    Optional 字段触发校验。
    """
    raw_messages: List[Dict[str, Any]] = list(getattr(body, "messages", []) or [])
    extras: Dict[str, Any] = dict(getattr(body, "model_extra", None) or {})

    # 取最后一条 user 消息的文本作为 query；vision 时 content 是 list，由 is_vision 处理
    query = ""
    if raw_messages:
        last = raw_messages[-1] or {}
        content = last.get("content")
        if isinstance(content, str):
            query = content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    query = str(part.get("text") or "")
                    if query:
                        break

    # 历史 = messages[:-1] 中 user/assistant/system 的部分
    history: List[Dict[str, str]] = []
    for m in raw_messages[:-1]:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip()
        content = m.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content:
            history.append({"role": role, "content": content})

    # OpenAI 兼容 body 里挑深度思考开关 — 我们既支持顶层 ``deep_think``,也支持
    # ``extra_body.deep_think``(部分 SDK 把非标字段塞到 extra_body 里)
    body_deep_think = bool(
        getattr(body, "deep_think", None)
        or extras.get("deep_think")
        or False,
    )

    # mode：tools 非空 → agent；vision 自动；其它 → llm（kb/file 由专属端点设置）
    tools = list(getattr(body, "tools", None) or [])
    tool_names: List[str] = []
    for t in tools:
        if isinstance(t, dict) and (t.get("function") or {}).get("name"):
            tool_names.append(str(t["function"]["name"]))
        elif isinstance(t, str):
            tool_names.append(t)

    return UnifiedChatRequest(
        query=query,
        mode=None,  # 让 ChatGraph classify 决定
        history=history,
        stream=bool(getattr(body, "stream", False)),
        model=str(getattr(body, "model", "") or ""),
        temperature=float(getattr(body, "temperature", 0.3) or 0.3),
        max_tokens=getattr(body, "max_tokens", None) or None,
        prompt_name=str(extras.get("prompt_name") or "default"),
        tools=tool_names,
        tool_choice=getattr(body, "tool_choice", None) if isinstance(
            getattr(body, "tool_choice", None), dict
        ) else None,
        use_mcp=bool(extras.get("use_mcp", False)),
        conversation_id=str(extras.get("conversation_id") or ""),
        metadata=dict(extras.get("metadata") or {}),
        raw_messages=raw_messages,
        extras=extras,
        deep_think=body_deep_think,
    )


# ---------------------------------------------------------------------------
# kb_chat 入参 → DTO
# ---------------------------------------------------------------------------

def from_kb_body(
    *,
    query: str,
    mode: str,
    kb_name: str,
    top_k: int,
    score_threshold: float,
    history: List[Dict[str, str]],
    stream: bool,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    prompt_name: str,
) -> UnifiedChatRequest:
    """``mode`` 取 local_kb / temp_kb / search_engine。"""
    chat_mode = {
        "local_kb": "kb",
        "temp_kb": "file",
        "search_engine": "search_engine",
    }.get(mode, "llm")
    return UnifiedChatRequest(
        query=query,
        mode=chat_mode,
        history=history or [],
        stream=bool(stream),
        model=str(model or ""),
        temperature=float(temperature),
        max_tokens=max_tokens or None,
        prompt_name=prompt_name or "default",
        kb_name=(kb_name if chat_mode == "kb" else None),
        file_chat_id=(kb_name if chat_mode == "file" else None),
        search_engine=(kb_name if chat_mode == "search_engine" else None),
        top_k=int(top_k or 5),
        score_threshold=float(score_threshold),
    )


# ---------------------------------------------------------------------------
# file_chat 入参 → DTO
# ---------------------------------------------------------------------------

def from_file_body(
    *,
    query: str,
    knowledge_id: str,
    top_k: int,
    score_threshold: float,
    history: List[Dict[str, str]],
    stream: bool,
    model_name: Optional[str],
    temperature: float,
    max_tokens: Optional[int],
    prompt_name: str,
) -> UnifiedChatRequest:
    return UnifiedChatRequest(
        query=query,
        mode="file",
        history=history or [],
        stream=bool(stream),
        model=str(model_name or ""),
        temperature=float(temperature),
        max_tokens=max_tokens or None,
        prompt_name=prompt_name or "default",
        file_chat_id=str(knowledge_id),
        top_k=int(top_k or 5),
        score_threshold=float(score_threshold),
    )


# ---------------------------------------------------------------------------
# multi_source 入参 → DTO
# ---------------------------------------------------------------------------

def from_multi_source_body(
    *,
    query: str,
    source_ids: List[int],
    select_all: bool,
    top_k: int,
    history: List[Dict[str, str]],
    stream: bool,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    prompt_name: str,
) -> UnifiedChatRequest:
    return UnifiedChatRequest(
        query=query,
        mode="multi_source",
        history=history or [],
        stream=bool(stream),
        model=str(model or ""),
        temperature=float(temperature),
        max_tokens=max_tokens or None,
        prompt_name=prompt_name or "default",
        source_ids=[int(x) for x in (source_ids or [])],
        select_all_sources=bool(select_all),
        top_k=int(top_k or 5),
    )
