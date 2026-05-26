/**
 * SSE 解析 Worker。
 *
 * 主线程把 ReadableStream（postMessage 转交所有权）发给我，
 * 我吐回结构化事件。流量大时显著降低主线程压力。
 *
 * 协议：
 *   main → worker:  { type: 'parse', stream: ReadableStream<Uint8Array> }
 *                   { type: 'abort' }
 *   worker → main:  { type: 'event', event: StructuredSSEEvent }
 *                   { type: 'done' }
 *                   { type: 'error', message }
 */

import { parseStructuredSSE } from './sse-parser';
import type { StructuredSSEEvent } from './types';

let abortCtl: AbortController | null = null;

self.onmessage = async (e: MessageEvent) => {
  const msg = e.data as
    | { type: 'parse'; stream: ReadableStream<Uint8Array> }
    | { type: 'abort' };

  if (msg.type === 'abort') {
    abortCtl?.abort();
    return;
  }

  if (msg.type === 'parse') {
    abortCtl = new AbortController();
    try {
      for await (const ev of parseStructuredSSE(msg.stream, abortCtl.signal)) {
        (self as unknown as Worker).postMessage({ type: 'event', event: ev } satisfies WorkerOutbound);
      }
      (self as unknown as Worker).postMessage({ type: 'done' } satisfies WorkerOutbound);
    } catch (err: unknown) {
      (self as unknown as Worker).postMessage({
        type: 'error',
        message: err instanceof Error ? err.message : String(err),
      } satisfies WorkerOutbound);
    } finally {
      abortCtl = null;
    }
  }
};

export type WorkerOutbound =
  | { type: 'event'; event: StructuredSSEEvent }
  | { type: 'done' }
  | { type: 'error'; message: string };
