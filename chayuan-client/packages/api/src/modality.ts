/**
 * 通用多模态 HTTP 客户端 — OCR / ASR。
 * 后端端点:POST /modality/ocr / /modality/transcribe(modality_routes.py)。
 */
import { request } from './client';

export interface OcrResult {
  text: string;
  lang: string;
  confidence: number;
  box_count: number;
  elapsed_ms: number;
}

export interface TranscribeResult {
  /** 兼容字段:
   *  - 无 sessionId 路径 → 整个 chunk 的转写文本
   *  - 有 sessionId 路径 → 本次 LocalAgreement-2 新 commit 的 stable_increment */
  text: string;
  language: string;
  // session 模式下的额外字段(无 session 时 undefined):
  /** 当前 whisper 对累积音频的完整猜测(整段 audio_pcm) */
  hypothesis?: string;
  /** 截至本次的全部 stable 文本(包括本次的 increment) */
  committed_text?: string;
  /** 当前未确认的尾部(LocalAgreement 还在等第二次确认) */
  volatile_suffix?: string;
  /** whisper 改写了之前 commit 的内容(罕见,前端不撤销;仅诊断用) */
  retroactive_change?: boolean;
  /** session 累积音频已达 30s 上限,下一个 chunk 应换 sessionId 重启 */
  is_full?: boolean;
  chunk_count?: number;
  audio_seconds?: number;
}

export interface AsrDiagSidecar {
  state: string;
  endpoint: string;
  reachable: boolean;
  http_status: number;
  last_error: string;
  probe_error: string;
}

export interface AsrDiagBackend {
  installed?: boolean;
  configured?: boolean;
  error: string;
}

export interface AsrDiagResult {
  sidecar: AsrDiagSidecar;
  faster_whisper: AsrDiagBackend;
  openai_whisper: AsrDiagBackend;
  openai_api: AsrDiagBackend;
  default_model: string;
  recommend: string;
}

/** call_log entry — preview/error 都已被后端截到 300 字符。 */
export interface CallLogEntry {
  ts: string;
  success: boolean;
  duration_ms: number;
  bytes_in: number;
  preview: string;
  error: string;
  // extra 透传字段(filename / language / lang / box_count / port / audio_format 等)
  [key: string]: unknown;
}

export interface CallLogResult {
  key: string;
  limit: number;
  entries: CallLogEntry[];
}

/** 文件上传转写的 segment(预留 speaker_diarization,当前后端 speaker 都是 speaker_0)。 */
export interface TranscribeFileSegment {
  start: number;
  end: number;
  text: string;
  speaker: string;
}

export interface TranscribeFileResult {
  text: string;
  language: string;
  audio_seconds: number;
  segments: TranscribeFileSegment[];
  /** 后端接入了真实说话人分离时为 true(FunASR sidecar + CAM++ 模型)。
   *  false 时所有 segment 都是 speaker_0,前端不应渲染"Speaker N:" 前缀。 */
  speaker_diarization_available: boolean;
  /** 实际分到的说话人数(diarize=true 时由后端填) */
  speaker_count?: number;
  diagnostics?: string;
}

/** RapidOCR sidecar 生命周期状态 — chayuan-server 启动时自动拉起;
 *  UI 给一个手动启停 / 重启的入口,跟其它 5 个本地 capability 视觉一致。 */
export interface OcrSidecarStatus {
  /** ready=端口在监听并可用 / starting=进程在跑但端口还没起 / stopped=未启动 / failed=spawn 直接挂 */
  state: 'ready' | 'starting' | 'stopped' | 'failed';
  pid: number | null;
  port: number;
  /** 后台 stdout/stderr 落盘位置;UI 显示出来给用户复制粘贴排查 */
  log_path: string;
  listening: boolean;
  /** chayuan-server 启动时是否自动拉起。持久化到 <CHAYUAN_ROOT>/data/sidecar_settings.json */
  auto_start: boolean;
  error?: string;
}

export const modality = {
  /**
   * 对一张图做 OCR。
   * blob 应该是 image/* (jpeg/png/webp 等浏览器原生支持的格式)。
   * 失败时 throws (server returns 502/503)。
   */
  async ocr(file: Blob, opts: { signal?: AbortSignal } = {}): Promise<OcrResult> {
    const form = new FormData();
    form.append('file', file, (file as File).name || 'image.bin');
    const r = await request<{ data: OcrResult }>(`/modality/ocr`, {
      method: 'POST',
      body: form,
      signal: opts.signal,
      raw: true,
    });
    return r.data?.data as OcrResult;
  },

  /**
   * 对一段音频做 ASR 转写。
   * blob:audio/webm 或 audio/wav 等。失败永远返回 text=""(不 throw)。
   */
  async transcribe(
    blob: Blob,
    opts: {
      language?: string;
      signal?: AbortSignal;
      /** 传 session_id 走流式 LocalAgreement-2 路径,后端会用累积音频跨 chunk 校验,
       *  返 stable_increment(本次新 commit)+ volatile_suffix(未确认尾部) */
      sessionId?: string;
    } = {},
  ): Promise<TranscribeResult> {
    const form = new FormData();
    form.append('file', blob, (blob as File).name || 'audio.webm');
    if (opts.language) form.append('language', opts.language);
    if (opts.sessionId) form.append('session_id', opts.sessionId);
    // 显式 45s timeout —— 后端硬上限是 audio_pcm ≤ 15s + ~5s whisper(最坏),
    // 25s 兜够;给 25s × 1.8 ≈ 45s 留余量,避免 24s 慢请求被错杀。比客户端
    // 默认 30s 长 — 这是因为 ASR sidecar 在低端 CPU 上首 chunk 偶尔 ~20s 冷启动。
    const r = await request<{ data: TranscribeResult }>(`/modality/transcribe`, {
      method: 'POST',
      body: form,
      signal: opts.signal,
      timeoutMs: 45_000,
      raw: true,
    });
    return r.data?.data ?? { text: '', language: opts.language ?? 'auto' };
  },

  /**
   * 实时探测 4 层 ASR 后端可用性 — sidecar / faster-whisper / openai-whisper / OpenAI API。
   * 用于"录完没文字"时给用户精确诊断,不读 registry 缓存。
   */
  async asrDiag(): Promise<AsrDiagResult> {
    const r = await request<{ data: AsrDiagResult }>(`/modality/asr/diag`, {
      method: 'GET',
      raw: true,
    });
    return r.data?.data as AsrDiagResult;
  },

  /**
   * 读取某 capability 最近的调用日志。
   * 当前后端支持 key: 'asr' / 'ocr'(后续若加 chat/embedding record,key 同名)。
   */
  /** 整段音频文件转写(非流式)。
   *
   *  - diarize=false(默认):后端 ffmpeg → wav → whisper.cpp /inference,单 speaker。
   *  - diarize=true:后端先试 FunASR sidecar(CAM++ 说话人分离),失败回退 whisper。
   *
   *  对大文件 latency 较高(>1 min 可能 10s+,FunASR 首次模型懒加载 30s+),
   *  caller 应给 loading 反馈。 */
  async transcribeFile(
    file: File | Blob,
    opts: { language?: string; signal?: AbortSignal; diarize?: boolean } = {},
  ): Promise<TranscribeFileResult> {
    const form = new FormData();
    form.append('file', file, (file as File).name || 'upload.audio');
    if (opts.language) form.append('language', opts.language);
    if (opts.diarize) form.append('diarize', 'true');
    const r = await request<{ data: TranscribeFileResult }>(`/modality/transcribe-file`, {
      method: 'POST',
      body: form,
      signal: opts.signal,
      raw: true,
    });
    return r.data?.data ?? {
      text: '', language: opts.language ?? 'auto',
      audio_seconds: 0, segments: [],
      speaker_diarization_available: false,
    };
  },

  async callLog(key: string, limit = 200): Promise<CallLogResult> {
    const r = await request<{ data: CallLogResult }>(
      `/modality/call-log/${encodeURIComponent(key)}?limit=${limit}`,
      { method: 'GET', raw: true },
    );
    return r.data?.data ?? { key, limit, entries: [] };
  },

  /** RapidOCR daemon 启停查 — 给「设置 → 本地模型服务」用。
   *  chayuan-server 启动时已经自动调过一次 start;UI 只负责显示状态 + 让用户
   *  手动重启 / 停;status 5s 轮询。 */
  ocrSidecar: {
    async status(): Promise<OcrSidecarStatus> {
      const r = await request<{ data: OcrSidecarStatus }>(
        `/modality/sidecar/ocr/status`, { method: 'GET', raw: true },
      );
      return r.data!.data;
    },
    async start(): Promise<OcrSidecarStatus> {
      const r = await request<{ data: OcrSidecarStatus }>(
        `/modality/sidecar/ocr/start`, { method: 'POST', raw: true },
      );
      return r.data!.data;
    },
    async stop(): Promise<OcrSidecarStatus> {
      const r = await request<{ data: OcrSidecarStatus }>(
        `/modality/sidecar/ocr/stop`, { method: 'POST', raw: true },
      );
      return r.data!.data;
    },
    async setAutoStart(enabled: boolean): Promise<OcrSidecarStatus> {
      const r = await request<{ data: OcrSidecarStatus }>(
        `/modality/sidecar/ocr/auto-start`,
        { method: 'POST', body: JSON.stringify({ enabled }), raw: true,
          headers: { 'Content-Type': 'application/json' } },
      );
      return r.data!.data;
    },
  },
};
