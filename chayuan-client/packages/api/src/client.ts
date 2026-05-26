/**
 * 统一 HTTP 客户端。
 *
 * 设计：
 * - 走 PAL.net.fetch（桌面绕 CORS / web 直 fetch）。
 * - 自动注入 Authorization、X-Trace-Id、traceparent。
 * - 401 → /auth/refresh 单飞重试一次；失败 throw BizError.auth()。
 * - 后端响应壳 {code, msg, data}：code != 0 抛 BizError.biz；2xx + 直接对象的端点（/auth/*、/v1/models）原样返回。
 * - 超时：默认 30s，可 per-request 覆盖；与外部 AbortSignal 合并。
 */

import { getPlatform, makeTraceparent, uuid } from '@chayuan/platform-shared';
import { BizError } from './errors';
import {
  clearTokens,
  getAccessToken,
  refreshAccessToken,
} from './auth-store';

export interface ClientConfig {
  /** 后端 API base，如 'http://127.0.0.1:62581' 或 '/api' */
  baseURL: string;
  /** 默认超时毫秒 */
  timeoutMs?: number;
  /**
   * 鉴权模式:
   * - 'bearer'(默认):JS 持 token,放 Authorization 头
   * - 'cookie':依赖后端 HttpOnly cookie + credentials='include',JS 不持 token
   *   该模式更抗 XSS,但需要后端 CORS 允许 credentials,且跨域不能用 *
   */
  authMode?: 'bearer' | 'cookie';
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Record<string, unknown>;
  body?: unknown; // FormData / object / string / undefined
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** 用于 Langfuse / OTel 关联；不传则自动生成 */
  traceId?: string;
  /** 是否禁用响应壳剥离（适用于不返回 {code,msg,data} 的端点） */
  raw?: boolean;
}

let cfg: ClientConfig = { baseURL: '', timeoutMs: 30_000 };

export function configureClient(c: Partial<ClientConfig>): void {
  cfg = { ...cfg, ...c };
}

export function getClientConfig(): ClientConfig {
  return cfg;
}

function joinUrl(base: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  if (!base) return path;
  return `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

function toQueryString(q: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) for (const x of v) parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(x))}`);
    else parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

function withTimeout(external: AbortSignal | undefined, ms: number): { signal: AbortSignal; cancel: () => void } {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new DOMException('timeout', 'TimeoutError')), ms);
  const cancel = () => clearTimeout(timer);
  if (external) {
    if (external.aborted) ctl.abort();
    else external.addEventListener('abort', () => ctl.abort(), { once: true });
  }
  ctl.signal.addEventListener('abort', cancel, { once: true });
  return { signal: ctl.signal, cancel };
}

async function buildHeaders(opts: RequestOptions, traceId: string): Promise<Headers> {
  const h = new Headers(opts.headers);
  // cookie 模式下不设 Authorization;后端 middleware 已支持 cookie fallback
  if ((cfg.authMode ?? 'bearer') === 'bearer') {
    const token = await getAccessToken();
    if (token) h.set('Authorization', `Bearer ${token}`);
  }
  h.set('X-Trace-Id', traceId);
  h.set('traceparent', makeTraceparent(traceId));
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
    h.set('Content-Type', 'application/json');
  }
  if (!h.has('Accept')) h.set('Accept', 'application/json');
  return h;
}

function encodeBody(body: unknown): BodyInit | undefined {
  if (body === undefined || body === null) return undefined;
  if (body instanceof FormData) return body;
  if (typeof body === 'string') return body;
  return JSON.stringify(body);
}

/** 后端响应壳判断：包含 'code' 字段且为 number；'data' 才是真负载 */
interface BackendEnvelope<T> {
  code: number;
  msg?: string;
  message?: string;
  data?: T;
}
function isEnvelope<T>(v: unknown): v is BackendEnvelope<T> {
  return typeof v === 'object' && v !== null && 'code' in (v as object) && typeof (v as { code: unknown }).code === 'number';
}

export interface RequestResult<T> {
  data: T;
  traceId: string;
  status: number;
  headers: Headers;
}

/**
 * 单次请求；不重试。被 request() 包装一层用于 401 刷新。
 */
async function rawRequest<T>(path: string, opts: RequestOptions, traceId: string): Promise<RequestResult<T>> {
  const url = joinUrl(cfg.baseURL, path) + (opts.query ? toQueryString(opts.query) : '');
  const { signal, cancel } = withTimeout(opts.signal, opts.timeoutMs ?? cfg.timeoutMs ?? 30_000);
  const headers = await buildHeaders(opts, traceId);

  let res: Response;
  try {
    res = await getPlatform().net.fetch(url, {
      method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
      headers,
      body: encodeBody(opts.body),
      signal,
      // cookie 模式必须带凭据,浏览器才会回传 HttpOnly cookie;
      // bearer 模式 same-origin 默认行为已经够用,显式声明也无害
      credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
    } as RequestInit);
  } catch (e: unknown) {
    cancel();
    if (e instanceof DOMException && (e.name === 'AbortError' || e.name === 'TimeoutError')) {
      throw new BizError('请求已中止', { kind: 'timeout', url, traceId, cause: e });
    }
    throw new BizError('网络请求失败', { kind: 'http', url, traceId, cause: e });
  } finally {
    cancel();
  }

  // 204 / 205：无 body
  if (res.status === 204 || res.status === 205) {
    return { data: undefined as T, traceId, status: res.status, headers: res.headers };
  }

  const ctype = res.headers.get('content-type') ?? '';
  let payload: unknown;
  if (ctype.includes('application/json')) {
    try {
      payload = await res.json();
    } catch (e: unknown) {
      throw new BizError('响应 JSON 解析失败', { kind: 'parse', status: res.status, url, traceId, cause: e });
    }
  } else {
    payload = await res.text();
  }

  if (!res.ok) {
    if (res.status === 401) throw BizError.auth(url, traceId);
    if (res.status === 403) throw new BizError('无权限', { kind: 'forbidden', status: 403, url, traceId, detail: payload });
    const detailField = payload && typeof payload === 'object'
      ? ((payload as { detail?: unknown }).detail)
      : undefined;
    const msg =
      (typeof detailField === 'string' && detailField) ||
      (payload && typeof payload === 'object' && (
        (payload as { msg?: string }).msg ||
        (payload as { message?: string }).message
      )) || `HTTP ${res.status}`;
    // 把 FastAPI 的 detail（可能是对象，e.g. {reason:'etag_conflict', current: {...}}）
    // 透到 BizError.detail，让 UI 能区分 409 子类型并展示对方版本。
    throw new BizError(String(msg), {
      kind: 'http',
      status: res.status,
      url,
      traceId,
      detail: detailField !== undefined ? detailField : payload,
    });
  }

  // 拆壳：{code, msg, data}；code != 0 视为业务错误。raw 模式跳过。
  // env.data 缺失时回落到整 envelope —— 兼容 {code, msg, items, total} 列表形态、
  // 以及 caller 写 ItemsResp/SingleResp 包装、期望 r.items/r.total 字段的代码。
  if (!opts.raw && isEnvelope<T>(payload)) {
    const env = payload;
    if (env.code !== 0 && env.code !== 200) {
      throw BizError.biz(env.code, env.msg || env.message || '业务错误', env, traceId);
    }
    // 只有当 envelope 严格只是 {code, msg, data, message?} 时才拆 .data；
    // 一旦带其它字段（如 items/total）就保留原 envelope，让 caller 用 r.items 等。
    const ENVELOPE_KEYS = new Set(['code', 'msg', 'message', 'data', 'trace_id']);
    const extra = Object.keys(env as object).some((k) => !ENVELOPE_KEYS.has(k));
    if (!extra && env.data !== undefined) {
      return { data: env.data as T, traceId, status: res.status, headers: res.headers };
    }
    // 列表形态（含 items/total）或 data 缺失：原样返回整 envelope
    return { data: payload as T, traceId, status: res.status, headers: res.headers };
  }
  return { data: payload as T, traceId, status: res.status, headers: res.headers };
}

/**
 * 主入口：自动 401 刷新一次。
 */
export async function request<T = unknown>(path: string, opts: RequestOptions = {}): Promise<RequestResult<T>> {
  const traceId = opts.traceId ?? uuid();
  try {
    return await rawRequest<T>(path, opts, traceId);
  } catch (e: unknown) {
    if (e instanceof BizError && e.kind === 'auth') {
      const fresh = await refreshAccessToken(async (refresh) => {
        const r = await getPlatform().net.fetch(joinUrl(cfg.baseURL, '/auth/refresh'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Trace-Id': traceId },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!r.ok) return null;
        return (await r.json()) as { access_token: string; refresh_token?: string };
      });
      if (!fresh) {
        await clearTokens();
        throw BizError.auth(path, traceId);
      }
      // 用新 token 重发
      return await rawRequest<T>(path, opts, traceId);
    }
    throw e;
  }
}

// ──────────────────────────────────────────────────────────────
// 便捷 verb shortcuts
// ──────────────────────────────────────────────────────────────

export const http = {
  get: <T>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...opts, method: 'GET' }).then((r) => r.data),
  post: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...opts, method: 'POST', body }).then((r) => r.data),
  patch: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...opts, method: 'PATCH', body }).then((r) => r.data),
  put: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...opts, method: 'PUT', body }).then((r) => r.data),
  del: <T>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...opts, method: 'DELETE' }).then((r) => r.data),
};
