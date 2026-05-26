/**
 * 察元 AI 一体化平台前端 API 封装。
 *
 * 与后端 `chayuan-server` 的两类端点对话：
 *
 * 1) **OpenAI 协议网关**：`/v1/chat/completions`、`/v1/embeddings`、
 *    `/v1/rerank`、`/v1/audio/{transcriptions,speech}`、`/v1/images/generations`、
 *    `/v1/videos/generations`、`/v1/ocr/recognize`、`/v1/models`。
 *    由 `chayuan_gateway`（`chayuan-server/libs/chayuan-gateway`）暴露并被
 *    `chayuan/server/ai_platform/__init__.py::register_ai_platform_routes`
 *    挂到主 ASGI app。
 *
 * 2) **管理面 / 自检 / vendor 视图**：复用既有 `runtime.ts` 的
 *    `/runtime/services` `/runtime/vendor` `/runtime/models`；本模块不重新封装。
 *
 * 设计要点：
 * - 与 chayuan-client 现有 `request()` 客户端契合：所有 GET/POST 都默认壳
 *   `{code, msg, data}`，但 OpenAI 协议端返回的是裸对象（{id, choices, ...}），
 *   因此对 `/v1/*` 路径设置 `raw: true`，让 client 不剥壳；
 * - 流式 SSE 走 `transport.chat`（`@chayuan/transport`）已有的 worker 路径，
 *   本模块只暴露非流式 + 列模型 + 简易调用工具；
 * - 任何后端返回 5xx → 自动记一条到 observability outbox（由 `request()` 兜底）。
 */

import { request, getClientConfig } from './client';
import { isBizError } from './errors';

// ---------------------------------------------------------------------------
// 公共类型（与 OpenAI / 察元后端契合）
// ---------------------------------------------------------------------------

export type Capability =
  | 'chat'
  | 'text-embedding'
  | 'image-embedding'
  | 'rerank'
  | 'text-to-image'
  | 'text-to-video'
  | 'text-to-speech'
  | 'asr'
  | 'ocr';

export interface AiPlatformModel {
  id: string;
  object: 'model';
  /** 推断出的能力（chayuan-identify） */
  capability?: Capability;
  /** 适配器（ollama/vllm/llamacpp/comfyui/...） */
  runtime?: string;
  /** 文件格式（gguf/safetensors/onnx/diffusers） */
  format?: string;
  /** 估算大小 */
  size_bytes?: number;
  /** 主权重 / manifest 文件路径（落盘后才有；后端 to_public 直填）。 */
  path?: string | null;
  /** 父目录，前端 "打开文件夹" 按钮用。 */
  dir?: string | null;
  /** path 是否真的存在；被人手删时会变 false。 */
  exists?: boolean;
  /** 模型权重的 SHA256（如果 manifest 写了）。 */
  sha256?: string | null;
  /** 创建时间戳 */
  created?: number;
  /** 内部识别置信度 1-5 */
  confidence?: number;
  /** 来源标签（packaged / downloaded / imported / dropped） */
  source_tag?: string;
}

/** ``GET /v1/admin/models/{id}/locate`` 的响应。 */
export interface ModelLocateResponse {
  model_id: string;
  runtime: string;
  found: boolean;
  path: string | null;
  dir: string | null;
  size_bytes: number;
  cache_kind: 'ollama-blobs' | 'hf-cache' | 'comfyui' | 'local' | null;
  blobs: Array<{
    digest: string;
    size_bytes: number;
    media_type?: string;
    path: string;
    exists: boolean;
  }>;
  notes?: string | null;
  extra?: Record<string, unknown>;
  sha256?: string | null;
  category?: string | null;
}

export interface ListModelsResponse {
  object: 'list';
  data: AiPlatformModel[];
}

// ---------------------------------------------------------------------------
// /v1/models — 列出所有可用模型（自动检测 + ollama 注册的）
// ---------------------------------------------------------------------------

export const aiPlatformModels = {
  async list(opts: { capability?: Capability } = {}): Promise<AiPlatformModel[]> {
    const resp = await request<ListModelsResponse>('/v1/models', {
      query: opts.capability ? { capability: opts.capability } : undefined,
        raw: true,
    });
    return resp.data?.data ?? [];
  },

  /** 删除一个本地模型（递归删目录）。仅 admin。 */
  async remove(modelId: string): Promise<{ deleted: boolean }> {
    return (
      await request<{ deleted: boolean }>(
        `/v1/models/${encodeURIComponent(modelId)}`,
        { method: 'DELETE' },
      )
    ).data;
  },

  /** 查询某个模型在本机磁盘上的真实文件位置（含 ollama / hf-cache / comfyui / local）。 */
  async locate(
    modelId: string,
    opts?: { runtime?: string },
  ): Promise<ModelLocateResponse> {
    // model_id 可能含 ``/`` 或 ``:``；前者是 ollama tag 风格，后者是 HF 仓库
    // 风格。后端用 ``{model_id:path}`` 接收，前端走 encodeURIComponent 安全。
    const url = `/v1/admin/models/${encodeURIComponent(modelId)}/locate${
      opts?.runtime ? `?runtime=${encodeURIComponent(opts.runtime)}` : ''
    }`;
    return (await request<ModelLocateResponse>(url, { raw: true })).data;
  },

  /** 把某个 model 设为对应 capability/category 的默认（chat / embedding / clip / ...）。 */
  async setDefault(category: string, modelId: string): Promise<{ ok: boolean }> {
    return (
      await request<{ ok: boolean }>('/v1/admin/models/default', {
        method: 'POST',
        body: { category, model: modelId },
        raw: true,
      })
    ).data;
  },

  async enable(modelId: string): Promise<{ ok: boolean }> {
    return (
      await request<{ ok: boolean }>(
        `/v1/admin/models/${encodeURIComponent(modelId)}/enable`,
        { method: 'POST', raw: true },
      )
    ).data;
  },

  async disable(modelId: string): Promise<{ ok: boolean }> {
    return (
      await request<{ ok: boolean }>(
        `/v1/admin/models/${encodeURIComponent(modelId)}/disable`,
        { method: 'POST', raw: true },
      )
    ).data;
  },

  /**
   * 订阅模型增量事件（替换前端轮询）。
   *
   * 服务端在 `/v1/models/events` 暴露 SSE：
   *   - `model.added`   {model: AiPlatformModel}
   *   - `model.updated` {model: AiPlatformModel}
   *   - `model.removed` {model_id: string}
   *
   * 注意：缺包/未启用 ai-platform 时该端点返回 404 → onError 回调被触发，
   * 调用方应静默降级到轮询。
   */
  subscribeEvents(handlers: {
    onAdded?: (m: AiPlatformModel) => void;
    onUpdated?: (m: AiPlatformModel) => void;
    onRemoved?: (id: string) => void;
    onError?: (err: unknown) => void;
  }): () => void {
    const cfg = getClientConfig();
    const url = `${cfg.baseURL.replace(/\/+$/, '')}/v1/models/events`;
    let es: EventSource | null = null;
    try {
      es = new EventSource(url, { withCredentials: cfg.authMode === 'cookie' });
    } catch (e) {
      handlers.onError?.(e);
      return () => undefined;
    }
    const safeJSON = (ev: Event): any => {
      try {
        return JSON.parse((ev as MessageEvent).data);
      } catch (e) {
        handlers.onError?.(e);
        return null;
      }
    };
    es.addEventListener('model.added', (ev) => {
      const data = safeJSON(ev);
      if (data?.model) handlers.onAdded?.(data.model);
    });
    es.addEventListener('model.updated', (ev) => {
      const data = safeJSON(ev);
      if (data?.model) handlers.onUpdated?.(data.model);
    });
    es.addEventListener('model.removed', (ev) => {
      const data = safeJSON(ev);
      if (data?.model_id) handlers.onRemoved?.(data.model_id);
    });
    es.onerror = (ev) => handlers.onError?.(ev);
    return () => {
      try {
        es?.close();
      } catch {
        // noop
      }
    };
  },
};

// ---------------------------------------------------------------------------
// /v1/chat/completions — 非流式 chat（流式建议走 @chayuan/transport）
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  name?: string;
  tool_call_id?: string;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string[];
  /** 该方法是非流式调用；流式请走 transport.chat() */
  stream?: false;
  user?: string;
  /** 透传任意自定义字段 */
  [k: string]: unknown;
}

export interface ChatCompletionResponse {
  id: string;
  object: 'chat.completion';
  created: number;
  model: string;
  choices: {
    index: number;
    message: ChatMessage;
    finish_reason: string;
  }[];
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export const aiPlatformChat = {
  async complete(req: ChatCompletionRequest): Promise<ChatCompletionResponse> {
    return (
      await request<ChatCompletionResponse>('/v1/chat/completions', {
        method: 'POST',
        body: { ...req, stream: false },
        raw: true,
        timeoutMs: 120_000,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// /v1/embeddings — 文本 / 图像嵌入
// ---------------------------------------------------------------------------

export interface EmbeddingRequest {
  model: string;
  input: string | string[] | { image: string }[];   // 图像支持 url / base64
  encoding_format?: 'float' | 'base64';
  dimensions?: number;
  user?: string;
}

export interface EmbeddingResponse {
  object: 'list';
  data: { object: 'embedding'; index: number; embedding: number[] | string }[];
  model: string;
  usage?: { prompt_tokens: number; total_tokens: number };
}

export const aiPlatformEmbeddings = {
  async create(req: EmbeddingRequest): Promise<EmbeddingResponse> {
    return (
      await request<EmbeddingResponse>('/v1/embeddings', {
        method: 'POST',
        body: req,
        raw: true,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// /v1/rerank — BGE / Cohere 风格的重排
// ---------------------------------------------------------------------------

export interface RerankRequest {
  model: string;
  query: string;
  documents: string[];
  top_n?: number;
  return_documents?: boolean;
}

export interface RerankResponse {
  id?: string;
  results: { index: number; relevance_score: number; document?: { text: string } }[];
  model: string;
}

export const aiPlatformRerank = {
  async rerank(req: RerankRequest): Promise<RerankResponse> {
    return (
      await request<RerankResponse>('/v1/rerank', {
        method: 'POST',
        body: req,
        raw: true,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// /v1/images/generations — 文生图
// ---------------------------------------------------------------------------

export interface ImageGenerateRequest {
  model: string;
  prompt: string;
  n?: number;
  size?: '512x512' | '768x768' | '1024x1024' | string;
  response_format?: 'url' | 'b64_json';
  user?: string;
}

export interface ImageGenerateResponse {
  created: number;
  data: { url?: string; b64_json?: string }[];
}

export const aiPlatformImages = {
  async generate(req: ImageGenerateRequest): Promise<ImageGenerateResponse> {
    return (
      await request<ImageGenerateResponse>('/v1/images/generations', {
        method: 'POST',
        body: req,
        raw: true,
        timeoutMs: 600_000,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// /v1/videos/generations — 文生视频
// ---------------------------------------------------------------------------

export interface VideoGenerateRequest {
  model: string;
  prompt: string;
  duration_sec?: number;
  resolution?: string;          // "720p" | "480p" | ...
  fps?: number;
  user?: string;
}

export interface VideoGenerateResponse {
  created: number;
  data: { url?: string; b64?: string }[];
}

export const aiPlatformVideos = {
  async generate(req: VideoGenerateRequest): Promise<VideoGenerateResponse> {
    return (
      await request<VideoGenerateResponse>('/v1/videos/generations', {
        method: 'POST',
        body: req,
        raw: true,
        timeoutMs: 1_800_000,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// /v1/audio/{transcriptions,speech} — ASR / TTS
// ---------------------------------------------------------------------------

export const aiPlatformAudio = {
  /** 语音识别（multipart/form-data：file + model[+ language]） */
  async transcribe(opts: {
    file: Blob | File;
    model: string;
    language?: string;
    prompt?: string;
  }): Promise<{ text: string }> {
    const fd = new FormData();
    fd.append('file', opts.file);
    fd.append('model', opts.model);
    if (opts.language) fd.append('language', opts.language);
    if (opts.prompt) fd.append('prompt', opts.prompt);
    return (
      await request<{ text: string }>('/v1/audio/transcriptions', {
        method: 'POST',
        body: fd,
        raw: true,
        timeoutMs: 600_000,
      })
    ).data;
  },

  /** 语音合成（返回原始音频字节） */
  async speak(opts: {
    model: string;
    input: string;
    voice?: string;
    response_format?: 'mp3' | 'wav' | 'ogg' | 'opus';
    speed?: number;
  }): Promise<Blob> {
    const resp = await fetch(
      `${getClientConfig().baseURL.replace(/\/+$/, '')}/v1/audio/speech`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...opts, response_format: opts.response_format ?? 'mp3' }),
      },
    );
    if (!resp.ok) {
      throw new Error(`/v1/audio/speech ${resp.status}: ${await resp.text()}`);
    }
    return resp.blob();
  },
};

// ---------------------------------------------------------------------------
// /v1/ocr/recognize — 图像文字识别
// ---------------------------------------------------------------------------

export interface OcrRequest {
  /** 二选一：file 或 image_url */
  file?: Blob | File;
  image_url?: string;
  model?: string;          // 默认 rapidocr
  /** 是否返回每一行的 bounding box */
  return_boxes?: boolean;
}

export interface OcrResponse {
  text: string;
  lines?: { text: string; box: number[][]; score: number }[];
  model: string;
}

export const aiPlatformOcr = {
  async recognize(opts: OcrRequest): Promise<OcrResponse> {
    if (opts.file) {
      const fd = new FormData();
      fd.append('file', opts.file);
      fd.append('model', opts.model ?? 'rapidocr');
      if (opts.return_boxes) fd.append('return_boxes', 'true');
      return (
        await request<OcrResponse>('/v1/ocr/recognize', {
          method: 'POST',
          body: fd,
          raw: true,
          timeoutMs: 120_000,
        })
      ).data;
    }
    return (
      await request<OcrResponse>('/v1/ocr/recognize', {
        method: 'POST',
        body: { image_url: opts.image_url, model: opts.model ?? 'rapidocr',
                return_boxes: opts.return_boxes ?? false },
        raw: true,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// 服务自检 / 部署诊断（被"系统设置 → AI 平台"页面调用）
// ---------------------------------------------------------------------------

export interface DoctorReport {
  ok: boolean;
  checks: {
    name: string;
    status: 'ok' | 'warn' | 'error' | 'skip';
    message: string;
    /** 修复建议命令 / 链接 */
    hint?: string;
  }[];
}

export const aiPlatformDoctor = {
  async run(): Promise<DoctorReport> {
    return (await request<DoctorReport>('/v1/admin/doctor', { timeoutMs: 30_000 })).data;
  },

  async fixAv(): Promise<DoctorReport> {
    return (await request<DoctorReport>('/v1/admin/doctor/fix-av', { method: 'POST' })).data;
  },

  async fixPorts(): Promise<DoctorReport> {
    return (await request<DoctorReport>('/v1/admin/doctor/fix-ports', { method: 'POST' })).data;
  },

  /** 通用修复入口（doctor 报告中的 check_name → server 给文字指引或自动修） */
  async fix(checkName: string): Promise<{ ok: boolean; hint?: string; result?: unknown }> {
    return (await request(`/v1/admin/doctor/fix/${encodeURIComponent(checkName)}`, {
      method: 'POST',
      raw: true,
    })).data as { ok: boolean; hint?: string; result?: unknown };
  },
};

// ---------------------------------------------------------------------------
// 推荐模型 + 下载进度 SSE + 服务拓扑
// ---------------------------------------------------------------------------

export interface RecommendedModelDto {
  id: string;
  capability: Capability;
  runtime: string;
  hf_repo: string;
  size_mb: number;
  intent: string;
  offline_friendly: boolean;
  optional_for_lite: boolean;
  id_aliases: string[];
  installed?: boolean;
  default?: boolean;
}

export interface RecommendedListResponse {
  capabilities: Capability[];
  recommended: Partial<Record<Capability, RecommendedModelDto[]>>;
  default_for_capability: Partial<Record<Capability, string | null>>;
}

export interface PullTask {
  task_id: string;
  state: 'queued' | 'running' | 'done' | 'failed' | 'error';
  repo: string;
}

export interface PullProgress {
  task_id?: string;
  repo?: string;
  filename?: string;
  bytes_done?: number;
  bytes_total?: number;
  percent?: number;
  state?: 'queued' | 'running' | 'done' | 'failed' | 'error' | 'rescanned';
  message?: string;
}

export interface ServiceTopology {
  host_os: 'linux' | 'mac' | 'win';
  services: ServiceTopologyItem[];
}

export interface ServiceTopologyItem {
  name: string;
  kind: string;
  managed: boolean;
  status: 'healthy' | 'configured' | 'missing';
  host?: string | null;
  port?: number | null;
  url?: string | null;
  binary_present: boolean;
  binary_path: string | null;
  probe?: { ok?: boolean; kind?: string; url?: string; detail?: string };
  install: {
    preferred: string[];
    docker: string[];
    all: Record<string, string[]>;
  };
}

export const aiPlatformRecommended = {
  async list(capability?: Capability): Promise<RecommendedListResponse> {
    const url = capability
      ? `/v1/admin/recommended_models?capability=${encodeURIComponent(capability)}`
      : '/v1/admin/recommended_models';
    return (await request<RecommendedListResponse>(url, { raw: true })).data;
  },
  /** 一键安装 —— 启动后台拉取，返回 task_id；前端再用 streamProgress 订阅。 */
  async install(repo: string, opts?: { capability?: Capability; mirror?: string }): Promise<PullTask> {
    return (
      await request<PullTask>('/v1/admin/models/pull', {
        method: 'POST',
        body: { repo, category: opts?.capability, mirror: opts?.mirror },
        raw: true,
      })
    ).data;
  },
  async status(taskId: string): Promise<PullProgress> {
    return (
      await request<PullProgress>(`/v1/admin/models/pull/${encodeURIComponent(taskId)}`, {
        raw: true,
      })
    ).data;
  },
  /** 通过 SSE 订阅进度；返回 cleanup 函数。每个进度帧调用一次 ``onProgress``。 */
  streamProgress(
    taskId: string,
    onProgress: (p: PullProgress) => void,
    onDone?: (p: PullProgress | null) => void,
  ): () => void {
    const base = getClientConfig().baseURL || '';
    const url = `${base.replace(/\/+$/, '')}/v1/admin/models/pull/${encodeURIComponent(taskId)}/stream`;
    const es = new EventSource(url, { withCredentials: false });
    let last: PullProgress | null = null;

    const onMessage = (ev: MessageEvent) => {
      const data = (ev as MessageEvent<string>).data;
      if (!data || data === '[DONE]') {
        es.close();
        onDone?.(last);
        return;
      }
      try {
        const parsed = JSON.parse(data) as PullProgress;
        last = parsed;
        onProgress(parsed);
        if (parsed.state === 'done' || parsed.state === 'failed' || parsed.state === 'error' || parsed.state === 'rescanned') {
          // 服务端会再发一行 [DONE]，这里不用主动 close（让 onmessage 处理）
        }
      } catch {
        // 心跳 / 注释行（: ping）忽略
      }
    };
    es.onmessage = onMessage;
    es.onerror = () => {
      es.close();
      onDone?.(last);
    };
    return () => es.close();
  },
};

export const aiPlatformTopology = {
  async get(): Promise<ServiceTopology> {
    return (await request<ServiceTopology>('/v1/admin/services/topology', { raw: true })).data;
  },
};

// ---------------------------------------------------------------------------
// 运行时（adapter）卡片 + 一键安装 + 9 类默认模型
// ---------------------------------------------------------------------------

export type RuntimeHealth = 'healthy' | 'configured' | 'missing';
export type RuntimeInstallKind = 'one-click' | 'pip' | 'docker' | 'manual';

export interface RuntimeCardDto {
  name: string;
  label: string;
  /** 内部 category 词汇（``chat`` / ``embedding`` / ``clip`` / ``t2i`` / ``t2v`` /
   *  ``tts`` / ``asr`` / ``ocr`` / ``rerank``）。 */
  categories: string[];
  /** 中文友好版（"对话 / 文本嵌入 / 图像嵌入 / ..."），可直接渲染。 */
  category_labels: string[];
  url: string | null;
  host?: string | null;
  port?: number | null;
  health: RuntimeHealth;
  probe: { ok?: boolean; kind?: string; url?: string; detail?: string };
  models_served: number;
  install_kind: RuntimeInstallKind;
  install_recipes: Record<string, string[]>;
  subprocess: boolean;
  needs_gpu: boolean;
  default_pip_package?: string | null;
}

export interface RuntimesResponse {
  runtimes: RuntimeCardDto[];
  host_os: 'linux' | 'mac' | 'win';
}

export interface RuntimeInstallTask {
  task_id: string;
  name: string;
  state: 'queued' | 'running' | 'done' | 'failed';
  kind: RuntimeInstallKind;
  log?: string[];
  return_code?: number;
  error?: string;
}

export const aiPlatformRuntimes = {
  async list(): Promise<RuntimesResponse> {
    return (await request<RuntimesResponse>('/v1/admin/runtimes', { raw: true })).data;
  },
  /** 一键安装；只对 ``install_kind`` ∈ {one-click, pip} 的 runtime 有效。 */
  async install(name: string): Promise<RuntimeInstallTask> {
    return (
      await request<RuntimeInstallTask>(
        `/v1/admin/runtimes/${encodeURIComponent(name)}/install`,
        { method: 'POST', raw: true },
      )
    ).data;
  },
  async status(taskId: string): Promise<RuntimeInstallTask> {
    return (
      await request<RuntimeInstallTask>(
        `/v1/admin/runtimes/install/${encodeURIComponent(taskId)}`,
        { raw: true },
      )
    ).data;
  },
};

export interface DefaultsCandidate {
  id: string;
  runtime: string | null;
  /** 后端 model_platform 的 platform_name(本地 sidecar 是 'local-<cap>',云厂商
   *  是 'deepseek' / 'openrouter' / 'qwen' 等)。前端按这个分组渲染 optgroup。
   *  老服务端可能没返回 → undefined,前端兜底归到「其它」。 */
  platform_name?: string | null;
  format: string | null;
  size_bytes: number;
  is_default: boolean;
}

export interface DefaultsResponse {
  capabilities: string[];          // ["chat","embedding","clip","rerank","t2i","t2v","tts","asr","ocr"]
  defaults: Record<string, string | null>;
  candidates: Record<string, DefaultsCandidate[]>;
}

export const aiPlatformDefaults = {
  async list(): Promise<DefaultsResponse> {
    return (await request<DefaultsResponse>('/v1/admin/defaults', { raw: true })).data;
  },
  async setMany(payload: Record<string, string>): Promise<{
    results: Record<string, { ok: boolean; model?: string; error?: string }>;
  }> {
    return (
      await request<{ results: Record<string, { ok: boolean; model?: string; error?: string }> }>(
        '/v1/admin/defaults',
        { method: 'POST', body: payload, raw: true },
      )
    ).data;
  },
};

export interface EmbeddingPreflight {
  ok: boolean;
  modality: 'text' | 'image';
  capability: Capability;
  model?: string | null;
  runtime?: string | null;
  setup?: {
    endpoint: string;
    query: Record<string, string>;
    panel: string;
    default: RecommendedModelDto | null;
  };
}

export const aiPlatformEmbeddingsPreflight = {
  /** "上传图片之前问一下后端：能不能向量化？" 走前端的 preflight，避免静默失败。
   *
   *  老版本 chayuan-server 没注册 `/v1/embeddings/preflight`（404），这里把
   *  404 视作"后端没接 preflight 能力" → 乐观放行（ok:true），让上传继续走。
   *  其他错（5xx/network）仍 throw，由 caller 处理。 */
  async check(modality: 'text' | 'image'): Promise<EmbeddingPreflight> {
    try {
      return (
        await request<EmbeddingPreflight>(
          `/v1/embeddings/preflight?modality=${modality}`,
          { raw: true },
        )
      ).data;
    } catch (err: unknown) {
      if (isBizError(err) && err.status === 404) {
        return {
          ok: true,
          modality,
          capability: modality === 'image' ? 'image-embedding' : 'text-embedding',
        };
      }
      throw err;
    }
  },
};

// ---------------------------------------------------------------------------
// 镜像源切换 —— 配置面板的 "模型镜像" 段落用
// ---------------------------------------------------------------------------

export interface MirrorOption {
  name: string;
  endpoint: string;
  kind: string;
}

export interface MirrorState {
  current: MirrorOption;
  available: MirrorOption[];
  is_custom: boolean;
}

export const aiPlatformMirror = {
  async get(): Promise<MirrorState> {
    return (await request<MirrorState>('/v1/admin/mirror', { raw: true })).data;
  },
  async setByName(name: string): Promise<{ ok: boolean; current: MirrorOption }> {
    return (
      await request<{ ok: boolean; current: MirrorOption }>('/v1/admin/mirror', {
        method: 'POST',
        body: { name },
        raw: true,
      })
    ).data;
  },
  async setByEndpoint(endpoint: string): Promise<{ ok: boolean; current: MirrorOption }> {
    return (
      await request<{ ok: boolean; current: MirrorOption }>('/v1/admin/mirror', {
        method: 'POST',
        body: { endpoint },
        raw: true,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// 一站式聚合
// ---------------------------------------------------------------------------

export const aiPlatform = {
  models: aiPlatformModels,
  chat: aiPlatformChat,
  embeddings: aiPlatformEmbeddings,
  rerank: aiPlatformRerank,
  images: aiPlatformImages,
  videos: aiPlatformVideos,
  audio: aiPlatformAudio,
  ocr: aiPlatformOcr,
  doctor: aiPlatformDoctor,
  mirror: aiPlatformMirror,
  recommended: aiPlatformRecommended,
  topology: aiPlatformTopology,
  embeddingsPreflight: aiPlatformEmbeddingsPreflight,
  runtimes: aiPlatformRuntimes,
  defaults: aiPlatformDefaults,
};

export default aiPlatform;
