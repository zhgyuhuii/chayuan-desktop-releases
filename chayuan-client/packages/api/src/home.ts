/**
 * 首页相关 API。
 *
 * 目前只有一个:产品介绍 SSE 流式生成 — 用于 HomePage 流式介绍区。
 * 后端: ``POST /admin/home/intro/stream``;LLM 选择策略复用模型补全那一套
 * (优先 deepseek/zhipu/openai 等已配齐的厂商)。
 *
 * 前端在 LLM 不可用(后端 emit ``event: error``)时,会捕获 Error 让 HomePage
 * 走本地静态文案 fallback,所以本方法只暴露最小成功路径 + 错误抛出。
 */
import { getPlatform } from '@chayuan/platform-shared';
import { getClientConfig } from './client';
import { getAccessToken } from './auth-store';

export interface IntroStreamHandlers {
  onChunk(text: string): void;
  onDone?(): void;
  onError?(err: Error): void;
}

async function fetchHomeIntroStream(
  handlers: IntroStreamHandlers,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const cfg = getClientConfig();
  const url = `${(cfg.baseURL || '').replace(/\/+$/, '')}/admin/home/intro/stream`;
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
    signal: opts.signal,
    credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
  } as RequestInit);
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => '');
    throw new Error(`home/intro/stream HTTP ${res.status}: ${txt || res.statusText}`);
  }
  // 极简 SSE 解析(同 modelPlatform.describeStream 套路)
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      if (opts.signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sepIdx: number;
      // eslint-disable-next-line no-cond-assign
      while (
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
        if (dataLines.length === 0) continue;
        const dataStr = dataLines.join('\n');
        if (event === 'chunk') {
          try {
            const obj = JSON.parse(dataStr) as { text?: string };
            if (obj.text) handlers.onChunk(obj.text);
          } catch (e) {
            handlers.onError?.(e as Error);
          }
        } else if (event === 'done') {
          handlers.onDone?.();
          return;
        } else if (event === 'error') {
          try {
            const obj = JSON.parse(dataStr) as { message?: string };
            throw new Error(obj.message || 'home intro stream error');
          } catch (e) {
            throw e instanceof Error ? e : new Error(String(e));
          }
        }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }
}

export const home = {
  introStream: fetchHomeIntroStream,
};
