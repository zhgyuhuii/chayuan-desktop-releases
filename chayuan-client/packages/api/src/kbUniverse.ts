/**
 * Knowledge Universe API client.
 *
 * 后端契约: chayuan-server/api_server/knowledge_universe_routes.py
 *
 * 设计要点:
 * - 前端统一只看 ku_id (string),不区分文档 KB(name)/外部 source(int)的两套主键
 * - kind 是判断 detail 渲染哪个面板的 single source of truth
 * - detail 是 discriminated union;消费方按 d.kind 收窄
 */

import { getPlatform } from '@chayuan/platform-shared';
import { getAccessToken } from './auth-store';
import { getClientConfig, request } from './client';

export type KuKind = 'document' | 'image' | 'structured' | 'vector';
export type KuSubKind = 'sql' | 'mongo' | 'es' | 'image' | 'vector' | 'vs';

/** document KB 卡片封面用的文件预览条目 */
export interface KuCoverFile {
  name: string;   // 文件名(含扩展名),例 "report.pdf"
  ext: string;    // 扩展名小写,例 "pdf" / "docx"
}

/** 列表项;list 端点返回数组 */
export interface KuItem {
  ku_id: string;
  kind: KuKind;
  /** structured / image / vector 才有;document 没有 */
  sub_kind?: KuSubKind;
  name: string;
  display_name: string;
  description: string;
  /** 文档=文件数;image=图片数;structured/vector 不一定有 */
  count: number | null;
  owner_id: number | null;
  /** 当前用户是否拥有该 KB(决定 UI 给不给删/改按钮) */
  mine: boolean;
  /** 当前用户是否有写权限(owner / editor / admin),用于上传和重建向量 */
  can_write?: boolean;
  access_role?: 'owner' | 'editor' | 'reader' | '' | string;
  visibility: 'public' | 'private' | string;
  vs_type?: string;
  embed_model?: string;
  create_time?: string | null;
  // ─── KbCard 封面用字段(2026-05-17 起后端 list 端点开始返这些)─────────
  /** structured KB:数据库 dialect,前端 DbLogo 选品牌图标用 */
  dialect?: string;
  /** document KB:前 4 个文件名 + 扩展名,前端做文件 chip 九宫格 */
  cover_files?: KuCoverFile[];
  /** image KB:前 4 张图的预览 URL,前端做缩略图九宫格 */
  cover_thumbnails?: string[];
}

// ── Detail union ──────────────────────────────────────────────────────────

interface KuDetailBase<K extends KuKind> {
  ku_id: string;
  kind: K;
  meta: KuItem;
}

export interface DocumentFile {
  file_name: string;
  file_size?: number | null;
  in_db?: boolean;
  doc_count?: number | null;
}
export interface KuDocumentDetail extends KuDetailBase<'document'> {
  files: DocumentFile[];
}

/**
 * upload / status / pipeline 各阶段的状态机:
 *   queued → ocr_and_embedding → ready  (或 failed / partial)
 * 老数据迁移时 state 默认为 "ready"。
 *
 * `partial`:CLIP / OCR 两条向量化路径都失败,但图本身已落盘并可见 ——
 * pipeline 已走完,UI 不再转圈;用户修完依赖(transformers/RapidOCR)后
 * 可点"重新索引"重跑。
 */
export type ImageItemState =
  | 'queued'
  | 'ocr_and_embedding'
  | 'ready'
  | 'failed'
  | 'partial';

export interface ImageItem {
  id: string;
  url?: string;
  thumb_url?: string;
  caption?: string;
  width?: number;
  height?: number;
  meta?: Record<string, unknown>;
  /** Pipeline 状态(2026-05-16 加,老数据/老后端可能没有,默认按 "ready" 处理)*/
  state?: ImageItemState;
  /** 0-100 整数;state=ready 时通常 100 */
  progress?: number;
  /** state=failed 时非空 */
  error?: string | null;
  /** 用户默认文本向量模型已经为本条图的 OCR 文本生成向量 */
  has_text_vector?: boolean;
  /** OCR 抽取的图上文字(可能截断到 200/500 字)*/
  ocr_text?: string | null;
}
export interface KuImageDetail extends KuDetailBase<'image'> {
  sub_kind: 'image';
  items: ImageItem[];
}

export interface StructuredField {
  name: string;
  type: string;
  nullable?: boolean;
  /** SQL 主键标识(后端 columns 携带) */
  primary_key?: boolean;
  comment?: string;
}
export interface StructuredTable {
  name: string;
  /** SQL 多 schema 时携带(如 'public'),其它后端可省 */
  schema?: string;
  comment?: string;
  /** 后端规范字段;老服务可能仍用 fields,前端两边都接受 */
  columns?: StructuredField[];
  /** @deprecated 老接口字段名;新代码用 columns */
  fields?: StructuredField[];
  /** 后端给的预估行数;详情页 UI 拿来当 hint */
  row_count_estimate?: number | null;
}
export interface KuStructuredDetail extends KuDetailBase<'structured'> {
  sub_kind: 'sql' | 'mongo' | 'es';
  tables: StructuredTable[];
  /** 后端 introspect 失败 / vector store down 时携带,UI 给降级提示 */
  degraded?: string;
}

/** 分页拉表行(POST /knowledge_universe/structured/rows)*/
export interface StructuredRowsResponse {
  columns: string[];
  rows: Array<Array<unknown>>;
  total: number | null;
  page: number;
  page_size: number;
  table: string;
  schema?: string;
}

export interface VectorCollection {
  name: string;
  dim?: number;
  metric?: string;
  size?: number;
  comment?: string;
  row_count_estimate?: number | null;
}
export interface KuVectorDetail extends KuDetailBase<'vector'> {
  sub_kind: 'vector' | 'vs';
  collections: VectorCollection[];
  degraded?: string;
}

export interface VectorRowsResponse {
  columns: string[];
  rows: Array<Array<unknown>>;
  total: number | null;
  page: number;
  page_size: number;
  collection: string;
  query: string;
}

export type KuDetail = KuDocumentDetail | KuImageDetail | KuStructuredDetail | KuVectorDetail;

// ── Ask result ────────────────────────────────────────────────────────────

export interface AskBlockBase {
  ku_id: string;
  kind: KuKind | null;
  ok: boolean;
  error?: string;
}

/**
 * 单条文档命中。后端 file_rag/result_envelope.to_wire 的字段一一对应。
 *
 * - `snippet` 是带 ±200 字窗口 + jieba 关键词高亮范围的首选展示文本
 *   (UI 渲染用 `highlight_spans` 套 <mark>);
 * - `content` 是完整 chunk,默认折叠,用户点"展开"才出。
 * - `score` 已经是归一后的相似度(0..1);`rerank_score` 单独暴露是否经过 cross-encoder。
 * - `retrieval_path` ∈ {'vector','bm25','hybrid','rerank','hyde','multi_query',...},
 *   用于结果卡片上挂"来源标签"。
 * - `page` / `chunk_index` 后端已就所有 KB 后端做过 key 归一(file_name/page/page_no/...
 *   都映射到这里)。可能为 null。
 */
export interface DocumentHit {
  id: string;
  kb_name: string;
  file_name: string;
  page: number | null;
  chunk_index: number | null;
  score: number;
  rerank_score?: number | null;
  snippet: string;
  highlight_spans: Array<[number, number]>;
  content: string;
  embed_model_used: string;
  retrieval_path: string;
  source: string;
  /** 源文件下载链接(相对 API base;前端点击时会补 token) */
  download_url?: string;
  /** 源文件内联预览链接(相对 API base;前端点击时会补 token) */
  preview_url?: string;
}
/**
 * Query rewriter trace — 后端 retrieval_pipeline.rewriter.run() 的输出。
 * UI 在结果列表上方做小胶囊展示("auto → hyde, 312ms, 2 条 query"),建立信任。
 */
export interface RewriteTrace {
  strategy_used: 'passthrough' | 'rewrite' | 'multi_query' | 'hyde' | 'decompose' | 'off';
  elapsed_ms: number;
  n: number;
}
export interface AskBlockDocument extends AskBlockBase {
  kind: 'document';
  /** 当前 KB 实际用的 embed model — 给前端 UI 上小徽章 */
  embed_model?: string;
  /** 改写后的所有 query(第一项始终是原 query) */
  rewritten_queries?: string[];
  /** 改写过程的 trace — UI 调试面板可用 */
  rewrite_trace?: RewriteTrace;
  /**
   * 0 命中时后端补的诊断字符串(嵌入失败 / collection 空 / 阈值过严等)。
   * 多条 query 的原因 ` | ` 拼起来,UI 在 EmptyDocResult 顶部显眼展示。
   */
  diagnostic?: string;
  results: DocumentHit[];
}

/** 检索增强按请求开关 — 给前端工具栏切换用 */
export interface RetrievalOpts {
  /** 默认 'auto';'off' 完全跳过改写;其它值精确指定策略 */
  rewrite?: 'auto' | 'passthrough' | 'rewrite' | 'multi_query' | 'hyde' | 'decompose' | 'off';
  /** 默认走全局 settings;true/false 显式开/关 */
  useHybrid?: boolean;
  useRerank?: boolean;
  /** @deprecated 查询嵌入模型必须跟随 KB 配置；保留字段仅兼容旧调用方，不再发送给后端 */
  embedModel?: string;
  /** 结构化库范围:ku_id -> 表 / 集合 / 索引名列表;空表示整个结构化库 */
  structuredScopes?: Record<string, string[]>;
  /** 外部向量库范围:ku_id -> collection 名列表;空表示整个向量库 */
  vectorScopes?: Record<string, string[]>;
}
export interface AskBlockStructured extends AskBlockBase {
  kind: 'structured';
  /** 结构化查询被限定的表 / 集合 / 索引;缺省表示整个结构化库 */
  scope?: { tables?: string[] };
  /** LLM 生成的亲和中文总结 — 是用户主要看的;UI 应当醒目展示 */
  summary?: string;
  /** query_database 返回的自由文本(含执行结果 + 提取的 SQL) — 兜底/调试用 */
  text?: string;
  /** 服务器端从 text 里挖出来的 SQL */
  sql?: string;
  /** 服务器端 re-execute 后的结构化 rows;最后一条可能是 {__truncated__: ...} */
  rows?: Array<Record<string, unknown>>;
  /** P1:由 rows[0].keys 推导的列顺序;无 rows 时缺省 */
  columns?: string[];
}
export interface AskBlockVector extends AskBlockBase {
  kind: 'vector';
  hits?: Array<{ content?: string; score?: number; metadata?: Record<string, unknown> }>;
}

/** P1 图像 caption 命中:文本→图(text-to-image,需跨模态 embedder) */
export interface ImageHit {
  id?: string;
  path?: string;
  preview_url?: string;
  download_url?: string;
  title?: string;
  caption?: string;
  score?: number;
}
export interface AskBlockImage extends AskBlockBase {
  kind: 'image';
  results?: ImageHit[];
}
export type AskBlock =
  | AskBlockDocument
  | AskBlockStructured
  | AskBlockVector
  | AskBlockImage
  | (AskBlockBase & { kind: null });

export interface AskResponse {
  query: string;
  results: AskBlock[];
}

// ── Unified kb-query API ──────────────────────────────────────────────────

export interface KbQueryHit {
  hit_id?: string;
  source_type?: KuKind | string;
  score?: number;
  text?: string;
  snippet?: string;
  retrieval_path?: string;
  sql?: string;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  citation?: Record<string, unknown>;
}

export interface KbQueryBlock {
  kb_id: string;
  name?: string;
  display_name?: string;
  kind: KuKind | string;
  sub_kind?: string;
  ok: boolean;
  results: KbQueryHit[];
  diagnostic?: Record<string, unknown>;
  error?: { code?: string; message?: string };
}

export interface KbQueryItem {
  content_id: string;
  ok: boolean;
  query: string;
  blocks: KbQueryBlock[];
  results: KbQueryHit[];
  diagnostic?: Record<string, unknown>;
}

export interface KbQueryResponse {
  request_id?: string;
  job_id?: string;
  kb_id?: string;
  ku_ids: string[];
  kind: KuKind | 'multi_source' | string;
  items: KbQueryItem[];
  usage?: Record<string, unknown>;
}

export interface KbQueryOptions {
  topK?: number;
  scoreThreshold?: number;
  rewrite?: RetrievalOpts['rewrite'];
  useHybrid?: boolean;
  useRerank?: boolean;
  structuredScopes?: Record<string, string[]>;
  vectorScopes?: Record<string, string[]>;
  signal?: AbortSignal;
}

// ── API ───────────────────────────────────────────────────────────────────

// ── Image source 子端点(/knowledge_source/{id}/image/*) ──────────────────

/** upload 响应的单条 item(后端 _item_to_wire 形态)。
 *  与 KuImageDetail.items 的 ImageItem 不同 — 这边用 image_id/filename/path/preview_url。
 */
export interface ImageUploadItem {
  filename: string;
  image_id: string;
  path: string;
  state: ImageItemState;
  progress: number;
  error: string | null;
  has_text_vector: boolean;
  ocr_text: string | null;
  preview_url: string;
  /** 老前端契约保留:'indexed' | 'queued' | 'missing'。 */
  vector_status: string;
}

export interface ImageUploadResult {
  added: ImageUploadItem[];
  errors: Array<{ filename: string; error: string }>;
}

/** GET /knowledge_source/{id}/image/{image_id}/status 返回。供前端轮询。 */
export interface ImageItemStatus {
  id: string;
  state: ImageItemState;
  progress: number;
  error: string | null;
  has_text_vector: boolean;
  ocr_text: string | null;
}

/** POST /knowledge_source/{id}/image/search_by_text 返回的单条 hit。
 *  source_path:文本向量路 / CLIP 跨模态路 / 融合。
 *  与文件顶部的 ImageHit(用于 ask blocks)不同。 */
export interface ImageRrfHit {
  id: string;
  filename: string;
  thumbnail_url: string;
  score: number;
  source_path: 'text_vec' | 'clip_text' | 'clip_image' | 'fused';
  fused: boolean;
  ocr_snippet: string | null;
  has_text_vector: boolean;
  meta: Record<string, unknown>;
}

export interface ImageRrfDiagnostics {
  /** 中文人话:例 "按关键词命中 3 张" / "按关键词未命中" / "..." */
  keyword_path: string;
  text_path: string;
  image_path: string;
  /** 整体诊断摘要(空库 / 三路都失败时);存在时前端用 amber 警告条展示 */
  summary?: string;
}

export interface ImageRrfSearchResponse {
  hits: ImageRrfHit[];
  diagnostics: ImageRrfDiagnostics;
}

/**
 * 以图搜图返回的 RetrievalChunk(后端 to_wire() 形态)。
 * citation.preview_url / download_url 可直接拼到 baseURL 后访问;
 * meta 中常见字段:image_id / caption / width / height / tags。
 */
export interface ImageSearchHit {
  content: string;
  score: number;
  source_id: number;
  source_kind: string;
  citation: {
    title?: string;
    source_id?: number;
    source_kind?: string;
    generated_query?: string | null;
    download_url?: string | null;
    preview_url?: string | null;
    meta?: Record<string, unknown>;
  };
}

export const imageSource = {
  /** 上传图像;source_id 是 src:<id> 解码后的整数 */
  async upload(sourceId: number | string, files: File[], tags = ''): Promise<ImageUploadResult> {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    if (tags) form.append('tags', tags);
    const r = await request<{ data: ImageUploadResult }>(`/knowledge_source/${sourceId}/image/upload`, {
      method: 'POST',
      body: form,
      raw: true,
    });
    return r.data?.data ?? { added: [], errors: [] };
  },
  async remove(sourceId: number | string, imageId: string): Promise<boolean> {
    const r = await request<{ code: number }>(`/knowledge_source/${sourceId}/image/${encodeURIComponent(imageId)}`, {
      method: 'DELETE',
      raw: true,
    });
    return r.data?.code === 0;
  },
  /**
   * 以图搜图。后端:POST /knowledge_source/{id}/image/search_by_image
   * 同源返回 chunks(已 to_wire),score 越大越相似;命中的图片可用 meta.image_id 反查
   * 当前 source 内的具体 ImageItem。signal 用于取消(主线程发起新查询时取消上一次)。
   */
  async searchByImage(
    sourceId: number | string,
    file: File | Blob,
    topK = 5,
    opts: { signal?: AbortSignal; tags?: string } = {},
  ): Promise<ImageSearchHit[]> {
    const form = new FormData();
    form.append('file', file as Blob, (file as File).name || 'query.bin');
    form.append('top_k', String(topK));
    if (opts.tags) form.append('tags', opts.tags);
    // ⚠ 客户端默认 timeout 30s,首次 CLIP 模型冷启动(从磁盘 mmap 400MB-1GB 权
    // 重 + 首次推理)在低端 CPU 上可能 10-30s,30s 必撞;实际推理 1-3s,但首次
    // 走一趟得给充足余量。bump 到 120s — 用户报"请求已中止"的根因就是默认太
    // 短。AbortSignal 仍然可用(用户主动取消 / 切窗口)。
    const r = await request<{ data: ImageSearchHit[] }>(
      `/knowledge_source/${sourceId}/image/search_by_image`,
      { method: 'POST', body: form, signal: opts.signal, raw: true, timeoutMs: 120_000 },
    );
    return r.data?.data ?? [];
  },
  /** GET 单条 item 状态(供前端轮询占位卡片);404 时抛 — 调用方应包 try。 */
  async itemStatus(sourceId: number | string, imageId: string): Promise<ImageItemStatus> {
    const r = await request<{ data: ImageItemStatus }>(
      `/knowledge_source/${sourceId}/image/${encodeURIComponent(imageId)}/status`,
      { raw: true },
    );
    return r.data?.data as ImageItemStatus;
  },
  /**
   * 按文字搜图。后端两路并行(用户默认文本向量模型 + CLIP 跨模态)+ RRF 融合。
   * 任一路降级时 diagnostics 会标 "unavailable: <reason>",前端应据此显示提示 banner。
   */
  async searchByText(
    sourceId: number | string,
    query: string,
    topK = 20,
    opts: { kRrf?: number; signal?: AbortSignal } = {},
  ): Promise<ImageRrfSearchResponse> {
    const r = await request<{ data: ImageRrfSearchResponse }>(
      `/knowledge_source/${sourceId}/image/search_by_text`,
      {
        method: 'POST',
        body: { query, top_k: topK, k_rrf: opts.kRrf ?? 60 },
        signal: opts.signal,
        raw: true,
      },
    );
    return r.data?.data ?? { hits: [], diagnostics: { text_path: 'unavailable: empty response', image_path: 'unavailable: empty response' } };
  },
  /** 拼出预览 / 下载 URL,带 token 走 query(image 端点是 token 白名单一员的话;不是的话仍可用 fetch) */
  fileUrl(sourceId: number | string, imageId: string, opts: { download?: boolean; token?: string } = {}): string {
    const qs = new URLSearchParams();
    if (opts.download) qs.set('download', 'true');
    if (opts.token) qs.set('token', opts.token);
    const tail = qs.toString() ? `?${qs.toString()}` : '';
    return `/knowledge_source/${sourceId}/image/${encodeURIComponent(imageId)}${tail}`;
  },
};

/** 流式 ask 的事件回调 */
export interface AskStreamHandlers {
  onMeta?(meta: { query: string; ku_ids: string[]; total: number }): void;
  /** 每完成一个 ku 推一次;顺序由后端完成时间决定,不保证与 ku_ids 一致 */
  onBlock(block: AskBlock): void;
  /** 综合总结开始(所有 block 已收集完,LLM 正在写答案) */
  onSummaryStart?(): void;
  /** 综合总结结束 — text 是 LLM 写的最终答案(含 [N] 引用),可能为空 */
  onSummary?(payload: { text: string; ok: boolean; model?: string; evidence_count?: number; error?: string }): void;
  onDone?(): void;
  /** 网络/解析层级的错误(单个 KB 的业务错误已包在 block.error) */
  onError?(err: Error): void;
}

/**
 * 极简 SSE 帧解析(只够消费 EventSourceResponse 输出):
 *   - 按 \n\n 切帧
 *   - 每帧 event: / data: 行各一条(后端只发单行 data)
 * 不引入 transport 包,避免 api → transport 的反向依赖。
 */
async function* parseEventStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: string }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // 按空行切帧
      let sepIdx: number;
      // 同时容忍 \n\n 与 \r\n\r\n
      while (
        // eslint-disable-next-line no-cond-assign
        (sepIdx = (() => {
          const a = buf.indexOf('\n\n');
          const b = buf.indexOf('\r\n\r\n');
          if (a === -1) return b;
          if (b === -1) return a;
          return Math.min(a, b);
        })()) !== -1
      ) {
        const isCRLF = buf.startsWith('\r\n\r\n', sepIdx);
        const frame = buf.slice(0, sepIdx);
        buf = buf.slice(sepIdx + (isCRLF ? 4 : 2));
        let event = 'message';
        const dataLines: string[] = [];
        for (const rawLine of frame.split(/\r?\n/)) {
          if (!rawLine || rawLine.startsWith(':')) continue;
          const colon = rawLine.indexOf(':');
          const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
          let val = colon === -1 ? '' : rawLine.slice(colon + 1);
          if (val.startsWith(' ')) val = val.slice(1);
          if (field === 'event') event = val;
          else if (field === 'data') dataLines.push(val);
        }
        if (dataLines.length > 0) yield { event, data: dataLines.join('\n') };
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }
}

export const knowledgeUniverse = {
  /** 列出全部 KB(可按 kind 过滤);后端按用户权限自动过滤 */
  async list(kind?: KuKind): Promise<KuItem[]> {
    const qs = new URLSearchParams();
    if (kind) qs.set('kind', kind);
    qs.set('include_vector', 'true');
    const r = await request<{ data: KuItem[] }>(
      `/knowledge_universe/list?${qs.toString()}`,
      { raw: true },
    );
    return r.data?.data ?? [];
  },

  /** 拉单个 KB 详情(文件 / 图像 / 表 / 集合) */
  async detail(kuId: string): Promise<KuDetail> {
    const r = await request<{ data: KuDetail }>(
      `/knowledge_universe/${encodeURIComponent(kuId)}/detail`,
      { raw: true },
    );
    if (!r.data?.data) throw new Error('detail 返回为空');
    return r.data.data;
  },

  /**
   * 分页拉结构化 KB 某张表的真实行数据。仅 SQL connector 支持;
   * Mongo / ES 走 400 让 UI 给"该源类型暂不支持"提示。
   * 表名走后端 introspect 白名单校验 + sqlalchemy quoted_name 防注入。
   */
  async fetchStructuredRows(body: {
    ku_id: string;
    table: string;
    schema?: string;
    page?: number;
    page_size?: number;
  }): Promise<StructuredRowsResponse> {
    const r = await request<{ data: StructuredRowsResponse }>(
      '/knowledge_universe/structured/rows',
      { method: 'POST', body, raw: true },
    );
    if (!r.data?.data) throw new Error('rows 返回为空');
    return r.data.data;
  },

  /** 外部向量库集合预览：通过该 collection 做一次语义检索，展示可读字段和样例数据。 */
  async fetchVectorRows(body: {
    ku_id: string;
    collection: string;
    query?: string;
    page?: number;
    page_size?: number;
  }): Promise<VectorRowsResponse> {
    const r = await request<{ data: VectorRowsResponse }>(
      '/knowledge_universe/vector/rows',
      { method: 'POST', body, raw: true },
    );
    if (!r.data?.data) throw new Error('vector rows 返回为空');
    return r.data.data;
  },

  /** 跨多 KB 提问(同步;一次性拿完整 results)。优先用 askStream 获得逐项进度 */
  async ask(
    query: string,
    kuIds: string[],
    topK = 5,
    opts?: { model?: string } & RetrievalOpts,
  ): Promise<AskResponse> {
    const r = await request<{ data: AskResponse }>('/knowledge_universe/ask', {
      method: 'POST',
      body: {
        query,
        ku_ids: kuIds,
        top_k: topK,
        model: opts?.model || undefined,
        use_hybrid: opts?.useHybrid,
        use_rerank: opts?.useRerank,
        rewrite_strategy: opts?.rewrite || undefined,
        structured_scopes: opts?.structuredScopes,
        vector_scopes: opts?.vectorScopes,
      },
      raw: true,
    });
    if (!r.data?.data) throw new Error('ask 返回为空');
    return r.data.data;
  },

  /**
   * 跨多 KB 提问 SSE 流式;后端 /ask/stream 并发跑,完成一个推一个。
   * 返回的 Promise 在 onDone 触发后 resolve。signal 用于客户端取消。
   *
   * `opts.model` 可选 — 透传给后端 NL→SQL,避免后端用空字符串模型;
   * 不传时后端走默认级联挑(action_model → DEFAULT_LLM_MODEL → 第一个可用)。
   */
  async askStream(
    query: string,
    kuIds: string[],
    topK: number,
    handlers: AskStreamHandlers,
    opts: { signal?: AbortSignal; model?: string } & RetrievalOpts = {},
  ): Promise<void> {
    const cfg = getClientConfig();
    const url = `${(cfg.baseURL || '').replace(/\/+$/, '')}/knowledge_universe/ask/stream`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };
    if ((cfg.authMode ?? 'bearer') === 'bearer') {
      const tok = await getAccessToken();
      if (tok) headers.Authorization = `Bearer ${tok}`;
    }
    const res = await getPlatform().net.fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        query,
        ku_ids: kuIds,
        top_k: topK,
        model: opts.model || undefined,
        use_hybrid: opts.useHybrid,
        use_rerank: opts.useRerank,
        rewrite_strategy: opts.rewrite || undefined,
        structured_scopes: opts.structuredScopes,
        vector_scopes: opts.vectorScopes,
      }),
      signal: opts.signal,
      credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
    } as RequestInit);
    if (!res.ok || !res.body) {
      const txt = await res.text().catch(() => '');
      throw new Error(`ask/stream HTTP ${res.status}: ${txt || res.statusText}`);
    }
    try {
      for await (const ev of parseEventStream(res.body, opts.signal)) {
        if (opts.signal?.aborted) return;
        if (ev.event === 'meta') {
          try { handlers.onMeta?.(JSON.parse(ev.data)); } catch { /* ignore parse */ }
        } else if (ev.event === 'block') {
          try { handlers.onBlock(JSON.parse(ev.data) as AskBlock); } catch (e) {
            handlers.onError?.(e as Error);
          }
        } else if (ev.event === 'summary_start') {
          handlers.onSummaryStart?.();
        } else if (ev.event === 'summary') {
          try { handlers.onSummary?.(JSON.parse(ev.data)); } catch (e) {
            handlers.onError?.(e as Error);
          }
        } else if (ev.event === 'done') {
          handlers.onDone?.();
          return;
        }
      }
    } catch (e) {
      if ((e as Error)?.name !== 'AbortError') handlers.onError?.(e as Error);
    }
  },
};

// ---------------------------------------------------------------------------
// 94 题:混合集合(kb_collections)
// ---------------------------------------------------------------------------

export interface KbCollectionMember {
  id: number;
  collection_id: number;
  ku_id: string;
  kind: 'document' | 'image';
  sort_order: number;
}

export interface KbCollection {
  id: number;
  name: string;
  display_name: string;
  description: string;
  owner_id: number;
  visibility: string;
  member_count?: number;       // list 端点带,detail 端点没
  members?: KbCollectionMember[]; // 仅 detail
  create_time?: string | null;
}

export interface KbCollectionSearchHit {
  id: string;
  content: string;
  score: number;
  metadata: Record<string, unknown>;
  source_kind: 'document' | 'image';
  ku_id: string;
}

export interface KbCollectionSearchResponse {
  items: KbCollectionSearchHit[];
  total: number;
  diagnostics: Array<{
    ku_id: string; kind: 'document' | 'image';
    ok: boolean; hit_count: number; error: string;
  }>;
}

export const kbCollections = {
  /** 列出当前用户的混合集合 */
  async list(): Promise<KbCollection[]> {
    const r = await request<{ data: { items: KbCollection[]; total: number } }>(
      '/knowledge_universe/collections',
      { raw: true },
    );
    return r.data?.data?.items ?? [];
  },

  /** 详情(展开 members) */
  async detail(collectionId: number): Promise<KbCollection> {
    const r = await request<{ data: KbCollection }>(
      `/knowledge_universe/collections/${collectionId}`,
      { raw: true },
    );
    if (!r.data?.data) throw new Error('集合详情返回为空');
    return r.data.data;
  },

  /** 新建集合 */
  async create(payload: {
    name: string;
    display_name?: string;
    description?: string;
    visibility?: 'private' | 'public';
  }): Promise<KbCollection> {
    const r = await request<{ data: KbCollection }>(
      '/knowledge_universe/collections',
      { method: 'POST', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('创建失败');
    return r.data.data;
  },

  async update(
    id: number,
    payload: { display_name?: string; description?: string },
  ): Promise<KbCollection> {
    const r = await request<{ data: KbCollection }>(
      `/knowledge_universe/collections/${id}`,
      { method: 'PATCH', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('更新失败');
    return r.data.data;
  },

  /** 删集合(级联删子 KB) */
  async remove(id: number): Promise<{ deleted_id: number; errors: unknown[] }> {
    const r = await request<{
      data: { deleted_id: number; errors: unknown[] };
    }>(`/knowledge_universe/collections/${id}`, {
      method: 'DELETE', raw: true,
    });
    return r.data?.data ?? { deleted_id: id, errors: [] };
  },

  /** 加成员;ku_id 必须与集合 owner 一致 */
  async addMember(
    collectionId: number,
    payload: { ku_id: string; kind: 'document' | 'image'; sort_order?: number },
  ): Promise<KbCollectionMember> {
    const r = await request<{ data: KbCollectionMember }>(
      `/knowledge_universe/collections/${collectionId}/members`,
      { method: 'POST', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('加成员失败');
    return r.data.data;
  },

  async removeMember(
    collectionId: number,
    kuId: string,
  ): Promise<{ removed: boolean }> {
    const r = await request<{ data: { removed: boolean } }>(
      `/knowledge_universe/collections/${collectionId}/members/${encodeURIComponent(kuId)}`,
      { method: 'DELETE', raw: true },
    );
    return r.data?.data ?? { removed: false };
  },

  /** 集合内并发搜索;单子超时跳过(94-3) */
  async search(
    collectionId: number,
    payload: {
      query: string;
      top_k?: number;
      per_member_timeout_s?: number;
      max_results?: number;
    },
  ): Promise<KbCollectionSearchResponse> {
    const r = await request<{ data: KbCollectionSearchResponse }>(
      `/knowledge_universe/collections/${collectionId}/search`,
      { method: 'POST', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('search 返回为空');
    return r.data.data;
  },
};

// ---------------------------------------------------------------------------
// 95 题:文件夹定时同步
// ---------------------------------------------------------------------------

export interface FolderSyncJob {
  id: number;
  name: string;
  folder_path: string;
  target: string;
  owner_id: number;
  interval_seconds: number;
  enabled: boolean;
  recursive: boolean;
  include_globs: string[];
  exclude_globs: string[];
  last_sync_at: string | null;
  last_sync_summary: Record<string, unknown>;
  create_time: string | null;
}

export interface FolderSyncTriggerResult {
  applied: boolean;
  summary: Record<string, unknown>;
  diff_preview?: {
    added: string[];
    modified: string[];
    removed: string[];
    unchanged: number;
  };
}

export const folderSync = {
  async list(): Promise<FolderSyncJob[]> {
    const r = await request<{ data: { items: FolderSyncJob[]; total: number } }>(
      '/folder_sync/jobs',
      { raw: true },
    );
    return r.data?.data?.items ?? [];
  },

  async detail(id: number): Promise<FolderSyncJob> {
    const r = await request<{ data: FolderSyncJob }>(
      `/folder_sync/jobs/${id}`, { raw: true },
    );
    if (!r.data?.data) throw new Error('detail 返回为空');
    return r.data.data;
  },

  async create(payload: {
    name: string;
    folder_path: string;
    target: string; // coll:N / doc:NAME / src:ID
    interval_seconds?: number;
    recursive?: boolean;
    include_globs?: string[];
    exclude_globs?: string[];
    enabled?: boolean;
  }): Promise<FolderSyncJob> {
    const r = await request<{ data: FolderSyncJob }>(
      '/folder_sync/jobs',
      { method: 'POST', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('创建失败');
    return r.data.data;
  },

  async update(id: number, payload: Partial<FolderSyncJob>): Promise<FolderSyncJob> {
    const r = await request<{ data: FolderSyncJob }>(
      `/folder_sync/jobs/${id}`,
      { method: 'PATCH', body: payload, raw: true },
    );
    if (!r.data?.data) throw new Error('更新失败');
    return r.data.data;
  },

  async remove(id: number): Promise<void> {
    await request(`/folder_sync/jobs/${id}`, {
      method: 'DELETE', raw: true,
    });
  },

  /** 立即同步一次 */
  async trigger(id: number): Promise<FolderSyncTriggerResult> {
    const r = await request<{ data: FolderSyncTriggerResult }>(
      `/folder_sync/jobs/${id}/trigger`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('trigger 返回为空');
    return r.data.data;
  },

  /** 干跑 — 不真上传,只看 diff 预览 */
  async dryRun(id: number): Promise<FolderSyncTriggerResult> {
    const r = await request<{ data: FolderSyncTriggerResult }>(
      `/folder_sync/jobs/${id}/dry_run`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('dry_run 返回为空');
    return r.data.data;
  },
};

export const kbQuery = {
  async search(query: string, kuIds: string[], opts: KbQueryOptions = {}): Promise<KbQueryResponse> {
    const r = await request<{ data: KbQueryResponse }>('/api/v1/kb-query/search', {
      method: 'POST',
      body: {
        ku_ids: kuIds,
        contents: [{ content_id: 'query', text: query }],
        structured_scopes: opts.structuredScopes,
        vector_scopes: opts.vectorScopes,
        options: {
          top_k: opts.topK ?? 5,
          score_threshold: opts.scoreThreshold,
          use_hybrid: opts.useHybrid,
          use_rerank: opts.useRerank,
          rewrite_strategy: opts.rewrite,
          structured_scopes: opts.structuredScopes,
          vector_scopes: opts.vectorScopes,
          return_diagnostics: true,
        },
      },
      signal: opts.signal,
      raw: true,
    });
    if (!r.data?.data) throw new Error('kb-query 返回为空');
    return r.data.data;
  },
};
