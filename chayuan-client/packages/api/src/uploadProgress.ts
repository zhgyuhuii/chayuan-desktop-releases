/**
 * XHR-based upload with progress events.
 *
 * 为什么不复用 client.ts 的 fetch:浏览器 fetch API **不支持 upload progress 事件**
 * (只支持 download 端的 ReadableStream)。要做上传百分比必须走 XMLHttpRequest。
 *
 * 与 client.ts 的能力对齐:
 *   - 自动注 Authorization / X-Trace-Id / traceparent
 *   - cookie 模式带 credentials
 *   - 401 不在这里处理(上传场景出现 401 通常是 token 过期,直接抛给上层重发)
 *   - 不剥响应壳:返原始 response,调用方按 raw 解析(可避免 envelope 双层 .data 坑)
 *
 * API:
 *   uploadWithProgress(path, formData, { onProgress, signal })
 *     → Promise<{ status, payload }>;status>=400 时 reject 带 BizError-shape error
 */

import { getPlatform, makeTraceparent, uuid } from '@chayuan/platform-shared';
import { getAccessToken } from './auth-store';
import { getClientConfig } from './client';

export interface UploadProgressEvent {
  /** 已发送字节数 */
  loaded: number;
  /** 总字节数(浏览器报告即用,否则 0) */
  total: number;
  /** 0..1;total=0 时为 0 */
  ratio: number;
}

export interface UploadOptions {
  /** 进度回调,主线程频度由浏览器驱动(通常每 N ms 一次) */
  onProgress?(ev: UploadProgressEvent): void;
  signal?: AbortSignal;
  timeoutMs?: number;
  traceId?: string;
}

export interface UploadResult<T = unknown> {
  status: number;
  /** 后端响应原文(JSON 解析失败时为 string) */
  payload: T | string;
}

/** 把 path 拼到 cfg.baseURL */
function joinUrl(base: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  if (!base) return path;
  return `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

/** 计算 FormData 总字节(只能近似:用 Files 累加) */
export function approximateFormDataSize(form: FormData): number {
  let n = 0;
  for (const [, v] of form.entries()) {
    if (v instanceof File) n += v.size;
    else if (typeof v === 'string') n += v.length;
  }
  return n;
}

export async function uploadWithProgress<T = unknown>(
  path: string,
  body: FormData,
  opts: UploadOptions = {},
): Promise<UploadResult<T>> {
  const cfg = getClientConfig();
  const url = joinUrl(cfg.baseURL, path);
  const traceId = opts.traceId ?? uuid();

  // Tauri:走 PAL.net.fetch,不支持 progress;此场景下退化为整体上传 + 0/100% 两段事件。
  // 我们 detect 是否在浏览器主线程并能用 XHR(window.XMLHttpRequest 存在)。
  //
  // ⚠ Tauri 2 webview(WebView2)里 XMLHttpRequest 对象**存在但发不出去** —
  // webview origin 是 tauri.localhost,目标是 127.0.0.1:62581,跨 scheme + CORS
  // 在生产装机版会被拦,XHR 直接触发 onerror("网络错误")。所以 Tauri 环境
  // 强制走 platform.net.fetch fallback(它走 Tauri 原生 fetch,不受 webview
  // 跨 origin 约束)。代价:上传进度只有 0/100% 两段,但能传成功。
  // 旁证:同样 endpoint 的图像上传走 platform.net.fetch 就稳;只有 doc 走
  // XHR 的链路炸。
  const platform = getPlatform();
  const isTauri = platform.kind === 'desktop';
  const hasXhr = typeof XMLHttpRequest !== 'undefined' && !isTauri;
  if (!hasXhr) {
    // 走 PAL.net.fetch 不带 progress:start = 0/total, end = total/total
    const totalGuess = approximateFormDataSize(body);
    opts.onProgress?.({ loaded: 0, total: totalGuess, ratio: 0 });
    const res = await getPlatform().net.fetch(url, {
      method: 'POST',
      body,
      signal: opts.signal,
      credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
      headers: await buildHeaders(cfg.authMode ?? 'bearer', traceId),
    } as RequestInit);
    const txt = await res.text();
    let payload: T | string = txt;
    try { payload = JSON.parse(txt) as T; } catch { /* keep text */ }
    opts.onProgress?.({ loaded: totalGuess, total: totalGuess, ratio: 1 });
    if (!res.ok) {
      const err: Error & { status?: number; payload?: unknown } = new Error(
        `HTTP ${res.status}: ${typeof payload === 'string' ? payload : JSON.stringify(payload)}`,
      );
      err.status = res.status;
      err.payload = payload;
      throw err;
    }
    return { status: res.status, payload };
  }

  return new Promise<UploadResult<T>>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);
    // header 注入(走 await,放在 send 之前)
    void buildHeaders(cfg.authMode ?? 'bearer', traceId).then((h) => {
      h.forEach((v, k) => xhr.setRequestHeader(k, v));
      // 从 abort signal 接桥
      if (opts.signal) {
        if (opts.signal.aborted) {
          xhr.abort();
          reject(new DOMException('aborted', 'AbortError'));
          return;
        }
        opts.signal.addEventListener('abort', () => xhr.abort(), { once: true });
      }
      // timeout
      if (opts.timeoutMs && opts.timeoutMs > 0) xhr.timeout = opts.timeoutMs;

      xhr.upload.onprogress = (e) => {
        const ratio = e.lengthComputable && e.total > 0 ? e.loaded / e.total : 0;
        opts.onProgress?.({ loaded: e.loaded, total: e.total, ratio });
      };
      xhr.upload.onloadstart = () => opts.onProgress?.({ loaded: 0, total: 0, ratio: 0 });

      xhr.onload = () => {
        const txt = xhr.responseText;
        let payload: T | string = txt;
        try { payload = JSON.parse(txt) as T; } catch { /* string */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          // 兜底再发一次 100%(部分浏览器 onprogress 不到 100)
          opts.onProgress?.({ loaded: 1, total: 1, ratio: 1 });
          resolve({ status: xhr.status, payload });
        } else {
          const err: Error & { status?: number; payload?: unknown } = new Error(
            `HTTP ${xhr.status}: ${typeof payload === 'string' ? payload.slice(0, 200) : JSON.stringify(payload)}`,
          );
          err.status = xhr.status;
          err.payload = payload;
          reject(err);
        }
      };
      xhr.onerror = () => reject(new Error('网络错误'));
      xhr.onabort = () => reject(new DOMException('aborted', 'AbortError'));
      xhr.ontimeout = () => reject(new DOMException('timeout', 'TimeoutError'));

      // cookie 模式
      if (cfg.authMode === 'cookie') xhr.withCredentials = true;
      xhr.send(body);
    }).catch(reject);
  });
}

async function buildHeaders(authMode: 'bearer' | 'cookie', traceId: string): Promise<Headers> {
  const h = new Headers();
  if (authMode === 'bearer') {
    const token = await getAccessToken();
    if (token) h.set('Authorization', `Bearer ${token}`);
  }
  h.set('X-Trace-Id', traceId);
  h.set('traceparent', makeTraceparent(traceId));
  // 不设 Content-Type:让浏览器自带 multipart boundary
  h.set('Accept', 'application/json');
  return h;
}
