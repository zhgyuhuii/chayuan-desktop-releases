/**
 * KB 远端同步 API 客户端 — 对应后端 /knowledge_base/remote_sources/*。
 *
 * 设计:
 * - source kind 是开放枚举(目前 minio | fastdfs),前端用字符串而不是 union 收死;
 *   后端 /kinds 端点动态返回可用列表,UI 据此渲染 tab。
 * - SSE 进度流封装为 subscribeJob() — 自动断线、自动 unsubscribe、传 onEvent
 *   回调,业务侧不直接碰 EventSource。
 */

import { getPlatform } from '@chayuan/platform-shared';
import { getAccessToken } from './auth-store';
import { getClientConfig, http, request } from './client';

// ── 类型 ─────────────────────────────────────────────────────────────

export type SourceKind = 'minio' | 'fastdfs' | string;

export interface SourceKindInfo {
  kind: SourceKind;
  available: boolean;
  label: string;
}

/** MinIO 连接配置 */
export interface MinioOptions {
  endpoint: string;       // 'minio:9000' 或 'https://s3.example.com'
  access_key: string;
  secret_key: string;
  bucket: string;
  secure?: boolean;
  region?: string;
}

/** FastDFS 连接配置 */
export interface FastDFSOptions {
  trackers: string[] | string;   // ['10.0.0.1:22122'] 或 '10.0.0.1:22122,10.0.0.2:22122'
  manifest_file_id?: string;
  local_root?: string;
}

export type SourceOptions = MinioOptions | FastDFSOptions | Record<string, unknown>;

export interface RemoteFile {
  key: string;
  name: string;
  size: number;
  modified: string | null;
  is_dir: boolean;
  etag?: string;
}

export interface BrowseResult {
  cwd: string;
  parent: string | null;
  entries: RemoteFile[];
  truncated: boolean;
  next_marker: string | null;
}

export interface PreflightResult {
  total: number;
  bytes_total: number;
  sample: RemoteFile[];
}

export interface JobSnapshot {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  kb_name: string;
  source_kind: SourceKind;
  created_at: number;
  updated_at: number;
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  bytes_done: number;
  last_file: string;
  error: string;
  cancel_requested: boolean;
}

export interface SyncFilterInput {
  extensions?: string[];
  max_size_bytes?: number;
  include_globs?: string[];
  exclude_globs?: string[];
}

export interface StartJobInput {
  kb_name: string;
  kind: SourceKind;
  options: SourceOptions;
  paths: string[];
  filter?: SyncFilterInput;
  concurrency?: number;
  override?: boolean;
  to_vector_store?: boolean;
}

// ── SSE 事件 ─────────────────────────────────────────────────────────

export type JobEventType = 'meta' | 'progress' | 'log' | 'status' | 'download' | 'ping' | 'done';

export interface DownloadEvent {
  file: string;
  key: string;
  downloaded: number;
  total: number;
}

export interface MetaEvent {
  kb_name: string;
  source_kind: SourceKind;
  paths: string[];
  concurrency: number;
  override: boolean;
  filter: Required<SyncFilterInput>;
}

export interface ProgressEvent {
  file: string;
  key: string;
  size: number;
  status: 'ok' | 'failed' | 'skipped';
  error?: string | null;
  processed: number;
  total: number;
}

export interface StatusEvent {
  status: JobSnapshot['status'];
  error?: string;
  cancel_requested?: boolean;
}

export interface LogEvent { msg: string; }

export type JobEventPayload = MetaEvent | ProgressEvent | StatusEvent | LogEvent | DownloadEvent | Record<string, unknown>;

export interface JobEvent<T extends JobEventPayload = JobEventPayload> {
  type: JobEventType;
  data: T;
}

// ── API ──────────────────────────────────────────────────────────────

const BASE = '/knowledge_base/remote_sources';

export const remoteSync = {
  listKinds: () =>
    http.get<SourceKindInfo[]>(`${BASE}/kinds`),

  testConnection: (kind: SourceKind, options: SourceOptions) =>
    http.post<{ ok: boolean; msg: string; [k: string]: unknown }>(
      `${BASE}/test`, { kind, options },
    ),

  browse: (
    kind: SourceKind, options: SourceOptions,
    path = '', marker?: string, limit = 200,
  ) =>
    http.post<BrowseResult>(
      `${BASE}/browse`, { kind, options, path, marker, limit },
    ),

  preflight: (
    kind: SourceKind, options: SourceOptions,
    paths: string[], filter: SyncFilterInput = {}, sample = 20,
  ) =>
    http.post<PreflightResult>(
      `${BASE}/preflight`,
      { kind, options, paths, sample, ...filter },
    ),

  start: (input: StartJobInput) =>
    http.post<JobSnapshot>(`${BASE}/jobs`, {
      kb_name: input.kb_name,
      kind: input.kind,
      options: input.options,
      paths: input.paths,
      ...(input.filter || {}),
      concurrency: input.concurrency ?? 4,
      override: input.override ?? false,
      to_vector_store: input.to_vector_store ?? true,
    }),

  get: (jobId: string) =>
    http.get<JobSnapshot>(`${BASE}/jobs/${encodeURIComponent(jobId)}`),

  cancel: (jobId: string) =>
    http.post<void>(`${BASE}/jobs/${encodeURIComponent(jobId)}/cancel`),

  /**
   * 订阅 job SSE 流。返回 unsubscribe();业务侧组件 unmount 时调用。
   *
   * 内部用 platform.net.fetch + ReadableStream 解析,绕开 EventSource(Electron
   * 主线程鉴权头注入麻烦;fetch + Authorization 一致)。
   */
  subscribeJob(
    jobId: string,
    handlers: {
      onEvent: (e: JobEvent) => void;
      onError?: (e: Error) => void;
      onClose?: () => void;
    },
  ): () => void {
    const ctrl = new AbortController();
    void runSse(jobId, ctrl.signal, handlers);
    return () => ctrl.abort();
  },
};

async function runSse(
  jobId: string,
  signal: AbortSignal,
  h: {
    onEvent: (e: JobEvent) => void;
    onError?: (e: Error) => void;
    onClose?: () => void;
  },
) {
  try {
    const cfg = getClientConfig();
    const url = `${(cfg.baseURL || '').replace(/\/+$/, '')}${BASE}/jobs/${encodeURIComponent(jobId)}/stream`;
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    if ((cfg.authMode ?? 'bearer') === 'bearer') {
      const tok = await getAccessToken();
      if (tok) headers.Authorization = `Bearer ${tok}`;
    }
    const res = await getPlatform().net.fetch(url, {
      method: 'GET',
      headers,
      signal,
      credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
    } as RequestInit);
    if (!res.ok || !res.body) {
      throw new Error(`SSE HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      if (signal.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = pickFrameEnd(buf)) !== -1) {
        const isCRLF = buf.startsWith('\r\n\r\n', idx);
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + (isCRLF ? 4 : 2));
        const parsed = parseFrame(frame);
        if (parsed) {
          h.onEvent(parsed);
          if (parsed.type === 'done') {
            try { reader.cancel(); } catch { /* ignore */ }
            h.onClose?.();
            return;
          }
        }
      }
    }
    h.onClose?.();
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') h.onError?.(e as Error);
  }
}

function pickFrameEnd(buf: string): number {
  const a = buf.indexOf('\n\n');
  const b = buf.indexOf('\r\n\r\n');
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function parseFrame(frame: string): JobEvent | null {
  let event: JobEventType = 'progress';
  const dataLines: string[] = [];
  for (const raw of frame.split(/\r?\n/)) {
    if (!raw || raw.startsWith(':')) continue;
    const colon = raw.indexOf(':');
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let val = colon === -1 ? '' : raw.slice(colon + 1);
    if (val.startsWith(' ')) val = val.slice(1);
    if (field === 'event') event = val as JobEventType;
    else if (field === 'data') dataLines.push(val);
  }
  if (dataLines.length === 0) return null;
  let data: Record<string, unknown>;
  try { data = JSON.parse(dataLines.join('\n')); } catch { return null; }
  return { type: event, data: data as JobEventPayload };
}
