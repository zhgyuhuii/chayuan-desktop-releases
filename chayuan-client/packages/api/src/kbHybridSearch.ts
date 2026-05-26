/**
 * KB 多路混合检索 + SSE 进度。
 *
 * 用法:
 *   const cancel = subscribeHybridSearch(
 *     { query, knowledge_base_name, top_k, rerank: true },
 *     (frame) => render(frame),
 *   );
 *
 * 帧类型见 HybridSearchFrame。
 *
 * 取消订阅:返回的 cancel() 会终止流(AbortController)。
 */
import { getAccessToken } from './auth-store';
import { getClientConfig } from './client';

export interface HybridSearchRequest {
  query: string;
  knowledge_base_name: string;
  top_k?: number;
  score_threshold?: number;
  rerank?: boolean;
  weights?: Partial<Record<'vector' | 'bm25' | 'title' | 'section', number>>;
}

export interface HybridSearchChunk {
  id?: string;
  content: string;
  file_name?: string;
  title?: string;
  section_path?: string[] | string;
  page?: number;
  char_offset_start?: number;
  char_offset_end?: number;
  rerank_score?: number;
  route_score?: number;
}

export type HybridSearchFrame =
  | { type: 'plan'; intent: string; routes: string[] }
  | { type: 'hyde'; rewritten: string }
  | { type: 'route_start'; route: string }
  | { type: 'route_done'; route: string; count: number; duration_ms: number }
  | { type: 'fuse'; total_unique: number }
  | { type: 'rerank'; top_k: number; duration_ms: number }
  | { type: 'summary'; summary: string }
  | { type: 'results'; chunks: HybridSearchChunk[] }
  | { type: 'done' }
  | { type: 'error'; message: string };

export function subscribeHybridSearch(
  body: HybridSearchRequest,
  onFrame: (frame: HybridSearchFrame) => void,
  onError?: (e: unknown) => void,
): () => void {
  const ac = new AbortController();
  void (async () => {
    try {
      const cfg = getClientConfig();
      const token = getAccessToken();
      const url = `${cfg.baseURL.replace(/\/+$/, '')}/knowledge_base/hybrid_search`;
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
        signal: ac.signal,
        credentials: cfg.authMode === 'cookie' ? 'include' : 'same-origin',
      });
      if (!resp.ok || !resp.body) {
        onError?.(new Error(`hybrid_search HTTP ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 帧以 \n\n 分割,每帧形如 'data: {...}'
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const line = raw
            .split('\n')
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trim())
            .join('\n');
          if (!line) continue;
          try {
            const frame = JSON.parse(line) as HybridSearchFrame;
            onFrame(frame);
          } catch (e) {
            onError?.(e);
          }
        }
      }
    } catch (e) {
      if ((e as { name?: string })?.name !== 'AbortError') onError?.(e);
    }
  })();
  return () => ac.abort();
}
