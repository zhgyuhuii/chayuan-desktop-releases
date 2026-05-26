"""ChatGraph 共享状态与请求模型。

**设计原则**：
1. State 是纯 dict（TypedDict），LangGraph 原生；节点返回 dict 增量 merge
2. 请求侧 DTO（ChatRequest）独立，避免 HTTP 参数直接污染图状态
3. 所有可观测 / 可治理信息都作为 State 字段，后续节点自然可读
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class ChatMode(str, Enum):
    LLM = "llm"                    # 纯对话
    AGENT = "agent"                # 工具调用 Agent
    KB = "kb"                      # 单向量知识库
    FILE = "file"                  # 临时文件对话
    SEARCH_ENGINE = "search_engine"
    MULTI_SOURCE = "multi_source"  # P0-4：多知识源（SQL / Mongo / ES / 多向量 KB）并行
    VISION = "vision"              # 多模态
    SUPERVISOR = "supervisor"      # T11：多 Agent 协作（researcher → writer → reviewer）


@dataclass
class ChatRequest:
    """前端请求归一后的模型；所有 HTTP 差异在 router 层抹平。"""

    query: str = ""
    mode: Optional[ChatMode] = None         # None 时由 classify 节点决定
    history: List[Dict[str, str]] = field(default_factory=list)
    stream: bool = True
    model: str = ""
    temperature: float = 0.3
    max_tokens: Optional[int] = None
    prompt_name: str = "default"
    # 模式专属参数
    kb_name: Optional[str] = None           # mode=kb(legacy 单 KB)
    kb_names: List[str] = field(default_factory=list)  # mode=multi_source 里向量源名
    # P0 多源:KU 宇宙的 ku_id 列表(`doc:<kb_name>` / `src:<source_id>`),
    # KBHandler 优先用这个走 _process_one_ku,否则回落到 kb_name(s)
    ku_ids: List[str] = field(default_factory=list)
    source_ids: List[int] = field(default_factory=list)  # mode=multi_source
    select_all_sources: bool = False
    file_chat_id: Optional[str] = None      # mode=file
    search_engine: Optional[str] = None     # mode=search_engine
    image_url: Optional[str] = None         # mode=vision；保留以兼容旧客户端
    image_urls: List[str] = field(default_factory=list)  # mode=vision 多图；非空时优先于 image_url
    top_k: int = 5
    score_threshold: float = 2.0
    # P2:Query 改写策略;'auto'(默认) / 'passthrough' / 'rewrite' / 'multi_query' /
    # 'hyde' / 'decompose' / 'off';传给 _process_one_ku 的 rewrite_strategy
    rewrite_strategy: Optional[str] = None
    # P2:hybrid / rerank 显式覆盖;None=按全局 settings
    use_hybrid: Optional[bool] = None
    use_rerank: Optional[bool] = None
    # Agent 专属
    tools: List[str] = field(default_factory=list)
    use_mcp: bool = False
    tool_input: Dict[str, Any] = field(default_factory=dict)
    # 用户 / trace 上下文
    user_id: Optional[int] = None
    username: str = ""
    user_role: str = ""                     # admin / analyst / user 等，用于 P1-9 masking
    conversation_id: str = ""
    request_id: str = ""
    # 治理开关（运行时可 override 策略）
    governance_enabled: bool = True
    # ChatComposer 顶部的 ✨ 深度思考开关 — 启用时按模型族注入 enable_thinking / thinking
    # 等上游推理参数(详见 chayuan.server.chat.deep_think.compute_deep_think_kwargs)
    deep_think: bool = False


class ChatState(TypedDict, total=False):
    """图状态；节点按需读写字段，合并语义由 LangGraph 负责。"""

    # 入参
    request: ChatRequest

    # classify 产出
    resolved_mode: str

    # retrieve 产出
    retrieved_chunks: List[Dict[str, Any]]          # 归一后的 chunk list（含 citation）
    retrieved_sources_meta: List[Dict[str, Any]]
    retrieval_elapsed_ms: int

    # 训练数据中心挂载产物
    mounted_context: Dict[str, Any]
    mounted_preferences: Dict[str, Any]
    mounted_examples: List[Dict[str, Any]]
    mounted_safety_rules: List[str]
    mounted_sources_meta: List[Dict[str, Any]]
    mounted_hit_summary: Dict[str, Any]

    # guardrail-in 产出
    prompt_injection_detected: bool
    prompt_injection_reason: str
    input_blocked: bool

    # generate 产出 / 过程
    messages_for_llm: List[Dict[str, Any]]
    llm_model: str
    answer: str
    answer_tokens: int                               # 估算 token 数，用于配额
    answer_finish_reason: str

    # guardrail-out / PII 产出
    output_pii_entities: List[Dict[str, Any]]       # Presidio / 规则结果
    output_masked_answer: Optional[str]
    output_blocked: bool

    # finalize 产出
    lineage_id: Optional[int]
    quota_rejected: bool
    quota_reason: str

    # 流式事件缓冲（为非流式入口重放）
    events_buffer: List[Dict[str, Any]]

    # 错误
    error: Optional[str]
