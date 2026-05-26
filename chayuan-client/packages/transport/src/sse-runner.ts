/**
 * 把 Worker / 主线程解析统一封装成 AsyncGenerator。
 *
 * 业务侧 `for await (const ev of runSSE(res, opts))` 拿到归一事件；
 * 不需要管 worker 是否启用、是否被打断。
 */

import { parseStructuredSSE } from './sse-parser';
import type { StructuredSSEEvent } from './types';
import type { WorkerOutbound } from './sse-worker';

export interface RunSSEOptions {
  signal?: AbortSignal;
  /** 启用 worker；调用方负责把 worker 实例传进来（供构建器各自决定 import.meta.url） */
  worker?: Worker;
}

export async function* runSSE(
  body: ReadableStream<Uint8Array>,
  opts: RunSSEOptions = {},
): AsyncGenerator<StructuredSSEEvent> {
  if (!opts.worker) {
    yield* parseStructuredSSE(body, opts.signal);
    return;
  }

  const w = opts.worker;
  const queue: StructuredSSEEvent[] = [];
  let resolveNext: ((v: StructuredSSEEvent | null) => void) | null = null;
  let finished = false;
  let error: Error | null = null;

  const onMessage = (e: MessageEvent) => {
    const msg = e.data as WorkerOutbound;
    if (msg.type === 'event') {
      if (resolveNext) {
        const r = resolveNext;
        resolveNext = null;
        r(msg.event);
      } else {
        queue.push(msg.event);
      }
    } else if (msg.type === 'done') {
      finished = true;
      resolveNext?.(null);
      resolveNext = null;
    } else if (msg.type === 'error') {
      error = new Error(msg.message);
      finished = true;
      resolveNext?.(null);
      resolveNext = null;
    }
  };

  w.addEventListener('message', onMessage);
  // 把 stream 的所有权转交给 worker
  w.postMessage({ type: 'parse', stream: body }, [body as unknown as Transferable]);

  const onAbort = () => w.postMessage({ type: 'abort' });
  opts.signal?.addEventListener('abort', onAbort, { once: true });

  try {
    while (true) {
      if (queue.length) {
        yield queue.shift()!;
        continue;
      }
      if (finished) break;
      const next = await new Promise<StructuredSSEEvent | null>((r) => (resolveNext = r));
      if (next === null) break;
      yield next;
    }
    if (error) throw error;
  } finally {
    w.removeEventListener('message', onMessage);
    opts.signal?.removeEventListener('abort', onAbort);
  }
}
