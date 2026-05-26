/**
 * 协议中立的统一事件流。
 *
 * 业务侧只消费 StructuredSSEEvent，不直接看 OpenAI delta / AG-UI raw。
 * sse-parser 会把所有形态归一到这套。
 */

export type StructuredSSEEvent =
  | { type: 'token'; delta: string; reasoning?: string; messageId?: string }
  | {
      type: 'tool_call';
      id?: string;
      name?: string;
      arguments?: string;
      status?: 'start' | 'delta' | 'end';
      result?: unknown;
    }
  | {
      type: 'citation';
      sources: Array<{
        title?: string;
        url?: string;
        snippet?: string;
        score?: number;
        chunks?: Array<{
          content?: string;
          snippet?: string;
          score?: number;
          page?: number | null;
          chunk_index?: number | null;
          retrieval_path?: string;
          title?: string;
        }>;
        /** 来自知识库时,KB 名(用于打开预览面板) */
        kb_name?: string;
        /** 来自知识库时,文件名(同上) */
        file_name?: string;
        /** 标记来源类型:'kb' / 'web' / 'file' / undefined(老协议) */
        source_kind?: 'kb' | 'web' | 'file' | string;
        /** 1-based 引用编号,与答案文本中的「[出处 N]」一一对应 */
        cite_index?: number;
        /** 命中片段数(同一文件多 chunk 命中聚合) */
        chunk_count?: number;
      }>;
    }
  | { type: 'interrupt'; reason?: string; payload?: unknown }
  | { type: 'mounted'; summary: MountedContextSummary; sources?: MountedSource[] }
  | { type: 'usage'; promptTokens?: number; completionTokens?: number; totalTokens?: number; costUsd?: number }
  | { type: 'error'; message: string; code?: number }
  | { type: 'done'; finishReason?: string };

export interface MountedContextSummary {
  mount_count?: number;
  mount_ids?: string[];
  fewshot_count?: number;
  boost_count?: number;
  safety_rule_count?: number;
  [key: string]: unknown;
}

export interface MountedSource {
  mount_id?: string;
  name?: string;
  artifact_type?: string;
  sample_ids?: string[];
  [key: string]: unknown;
}

export interface ChatRequestV2 {
  query: string;
  mode?: '' | 'llm' | 'agent' | 'kb' | 'file' | 'search_engine' | 'multi_source' | 'vision';
  history?: Array<{ role: 'user' | 'assistant' | 'system' | 'tool'; content: string }>;
  stream?: boolean;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  prompt_name?: string;
  /** Legacy document-KB compatibility. Prefer `ku_ids`; only doc:* selections should be mirrored here. */
  kb_name?: string;
  /** Legacy document-KB compatibility. Non-document sources must not be sent only through this field. */
  kb_names?: string[];
  /**
   * P0:KU 宇宙的 ku_id 列表(`doc:<kb>` / `src:<id>`)。
   * 非空时后端 KBHandler 走多源聚合(doc+structured+image 同时检索),
   * 否则回落 kb_name(s) 单/多 doc KB。
   */
  ku_ids?: string[];
  /** @deprecated use `ku_ids` with `src:<id>` so kind and routing remain explicit. */
  source_ids?: number[];
  select_all_sources?: boolean;
  file_chat_id?: string;
  search_engine?: string;
  /** 单图（保留兼容；新客户端推荐用 image_urls） */
  image_url?: string;
  /** 多图 vision；非空时后端会优先使用此字段 */
  image_urls?: string[];
  top_k?: number;
  score_threshold?: number;
  /** P2:Query 改写策略;'auto'(默认) / 'passthrough' / 'rewrite' / 'multi_query' / 'hyde' / 'decompose' / 'off' */
  rewrite_strategy?: 'auto' | 'passthrough' | 'rewrite' | 'multi_query' | 'hyde' | 'decompose' | 'off';
  /** P2:hybrid / rerank 显式覆盖;不传或 null = 用全局 settings */
  use_hybrid?: boolean;
  use_rerank?: boolean;
  tools?: string[];
  use_mcp?: boolean;
  conversation_id?: string;
  governance_enabled?: boolean;
  /**
   * ChatComposer 顶部"深度思考"✨ 开关。
   * 后端按模型族注入对应推理参数:
   *   - 阿里 Qwen3 / Qwen-Plus / Qwen-Max:extra_body.enable_thinking = true
   *   - Anthropic Claude(Sonnet 3.7+ / Opus 4+):extra_body.thinking = {type:enabled, budget_tokens}
   *   - OpenAI o-series / DeepSeek-Reasoner:本身就是推理模型,本字段不影响
   *   - 其它模型:noop,后端日志 INFO
   */
  deep_think?: boolean;
}

export interface SendOptions {
  signal?: AbortSignal;
  /** 调用方提供的 trace_id；与 Langfuse 联动 */
  traceId: string;
  /** 启用 worker 解析（流量大时显著降低主线程压力） */
  useWorker?: boolean;
}

export type ResumeDecision = {
  approved: boolean;
  patch?: Record<string, unknown>;
};
