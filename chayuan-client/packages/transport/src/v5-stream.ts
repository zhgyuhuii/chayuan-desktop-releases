/**
 * Vercel AI SDK v5 UI Message Stream — 客户端解析器。
 *
 * 后端 `/v1/modality/completions` 走 v5 协议:每帧 `data: <json>\n\n`,
 * JSON 含 `type` 字段。本模块把字节流 → 强类型 V5Event 异步流,与已有
 * `parseSSEFrames` 复用底层字节解析。
 *
 * 字段对齐:
 *   https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol#ui-message-stream-protocol
 *
 * 我们目前用到的 type:
 *   start / text-start / text-delta / text-end / file / source-url /
 *   source-document / data-<name> / error / finish-step / finish
 *
 * 未识别的 type 不丢弃,以 `{type, raw}` 形态透传,前端 switch 默认分支
 * 可以记日志或忽略 — 给 v5 未来扩展留余地。
 */

import { parseSSEFrames } from './sse-parser';

/** Vercel v5 标准事件 — 严格对齐协议;未识别 type 走 V5UnknownEvent。 */
export type V5Event =
  | V5StartEvent
  | V5TextStartEvent
  | V5TextDeltaEvent
  | V5TextEndEvent
  | V5FilePart
  | V5SourceUrl
  | V5SourceDocument
  | V5DataPart
  | V5ErrorEvent
  | V5FinishStep
  | V5Finish
  | V5UnknownEvent;

export interface V5StartEvent { type: 'start'; messageId?: string; }
export interface V5TextStartEvent { type: 'text-start'; id: string; }
export interface V5TextDeltaEvent { type: 'text-delta'; id: string; delta: string; }
export interface V5TextEndEvent { type: 'text-end'; id: string; }
export interface V5FilePart {
  type: 'file';
  mediaType: string;
  url: string;
  metadata?: Record<string, unknown>;
}
export interface V5SourceUrl { type: 'source-url'; sourceId: string; url: string; title?: string; }
export interface V5SourceDocument {
  type: 'source-document';
  sourceId: string;
  mediaType: string;
  title: string;
}
/** v5 typed data part — type 形如 `data-<name>`。 */
export interface V5DataPart {
  type: `data-${string}`;
  data: unknown;
  id?: string;
}
export interface V5ErrorEvent { type: 'error'; errorText: string; code?: string; }
export interface V5FinishStep { type: 'finish-step'; }
export interface V5Finish { type: 'finish'; }
export interface V5UnknownEvent { type: string; raw: unknown; }

/** 终止哨兵:后端可能附 ``[DONE]`` 兼容老 OpenAI 客户端。 */
const DONE_SENTINEL = '[DONE]';

/**
 * 从 fetch 拿到的 ReadableStream 解析出 v5 事件流。
 *
 * 错误处理:
 *   - JSON 解析失败 → 转 `error` 事件 + 原始 raw,流不中断
 *   - 网络中断 / abort → 抛出 (调用方 catch)
 */
export async function* parseV5Stream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<V5Event> {
  for await (const rec of parseSSEFrames(body, signal)) {
    const raw = rec.data;
    if (!raw || raw === DONE_SENTINEL) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      yield {
        type: 'error',
        errorText: `v5 stream JSON 解析失败: ${(e as Error).message}`,
      };
      continue;
    }
    if (!parsed || typeof parsed !== 'object' || !('type' in parsed)) {
      yield { type: 'error', errorText: 'v5 事件缺 type 字段', raw } as V5ErrorEvent;
      continue;
    }
    yield parsed as V5Event;
  }
}

/** 类型守卫,供前端 switch 取代繁琐的 in 检查。 */
export function isFilePart(ev: V5Event): ev is V5FilePart {
  return ev.type === 'file';
}
export function isTextDelta(ev: V5Event): ev is V5TextDeltaEvent {
  return ev.type === 'text-delta';
}
export function isDataPart(ev: V5Event): ev is V5DataPart {
  return typeof ev.type === 'string' && ev.type.startsWith('data-');
}
export function isErrorEvent(ev: V5Event): ev is V5ErrorEvent {
  return ev.type === 'error';
}
