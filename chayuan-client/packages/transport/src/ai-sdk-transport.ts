/**
 * Vercel ai SDK v5 自定义 Transport 适配。
 *
 * 关键点：
 * - 把 chayuan SSE 事件流转成 ai SDK 的 UI message stream（chat-init / text-start / text-delta /
 *   tool-input-start / tool-input-delta / tool-output / source / data-* / finish）。
 * - 业务侧使用：const transport = new ChayuanAiTransport({ ... }) → useChat({ transport })
 *   完整接管发送、流式、停止、重发、编辑分支。
 *
 * 注：ai SDK v5 的 transport 接口为 `ChatTransport<UIMessage>`，本实现通过
 * `submitMessages` 发送、`reconnectToStream` 实现断线续接。运行时类型为 ai/dist。
 */

import { uuid } from '@chayuan/platform-shared';
import type { ChayuanTransport } from './chayuan-transport';
import type { ChatRequestV2, StructuredSSEEvent, SendOptions } from './types';

export interface ChayuanAiTransportOptions {
  transport: ChayuanTransport;
  /** 由业务侧根据当前会话状态构造请求体（model/mode/tools/kb/...） */
  buildRequest(input: {
    messages: AiSDKMessage[];
    body?: unknown;
  }): ChatRequestV2;
  /** trace_id 钩子；用于 Langfuse */
  onTrace?(traceId: string, info: { conversationId?: string }): void;
  useWorker?: boolean;
}

/** 简化的 ai SDK UIMessage 结构（避免硬依赖 ai 包的类型版本） */
export interface AiSDKMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  parts: Array<{ type: string } & Record<string, unknown>>;
}

/** ai SDK v5 transport 协议的 chunk 类型（手写最小子集） */
export type UIChunk =
  | { type: 'start'; messageId: string }
  | { type: 'start-step' }
  | { type: 'text-start'; id: string }
  | { type: 'text-delta'; id: string; delta: string }
  | { type: 'text-end'; id: string }
  | { type: 'reasoning-start'; id: string }
  | { type: 'reasoning-delta'; id: string; delta: string }
  | { type: 'reasoning-end'; id: string }
  | { type: 'tool-input-start'; toolCallId: string; toolName: string }
  | { type: 'tool-input-delta'; toolCallId: string; inputTextDelta: string }
  | { type: 'tool-input-available'; toolCallId: string; toolName: string; input: unknown }
  | { type: 'tool-output-available'; toolCallId: string; output: unknown }
  | { type: 'source-url'; sourceId: string; url: string; title?: string }
  | { type: 'data-interrupt'; data: { reason?: string; payload?: unknown } }
  | { type: 'data-usage'; data: { promptTokens?: number; completionTokens?: number; totalTokens?: number; costUsd?: number } }
  | { type: 'error'; errorText: string }
  | { type: 'finish-step' }
  | { type: 'finish' };

/**
 * 把 StructuredSSEEvent 流翻译为 UIChunk 流。
 * 状态机：text-start → text-delta* → text-end；tool-input 类似。
 */
async function* toUIChunks(
  events: AsyncGenerator<StructuredSSEEvent>,
  messageId: string,
): AsyncGenerator<UIChunk> {
  yield { type: 'start', messageId };
  yield { type: 'start-step' };

  let textOpenId: string | null = null;
  let reasoningOpenId: string | null = null;
  const toolBuf = new Map<string, { name?: string; args: string }>();

  const closeText = (): UIChunk[] => {
    if (!textOpenId) return [];
    const id = textOpenId;
    textOpenId = null;
    return [{ type: 'text-end', id }];
  };
  const closeReasoning = (): UIChunk[] => {
    if (!reasoningOpenId) return [];
    const id = reasoningOpenId;
    reasoningOpenId = null;
    return [{ type: 'reasoning-end', id }];
  };

  for await (const ev of events) {
    switch (ev.type) {
      case 'token': {
        if (ev.reasoning) {
          if (!reasoningOpenId) {
            reasoningOpenId = uuid();
            yield { type: 'reasoning-start', id: reasoningOpenId };
          }
          yield { type: 'reasoning-delta', id: reasoningOpenId, delta: ev.reasoning };
        }
        if (ev.delta) {
          for (const c of closeReasoning()) yield c;
          if (!textOpenId) {
            textOpenId = uuid();
            yield { type: 'text-start', id: textOpenId };
          }
          yield { type: 'text-delta', id: textOpenId, delta: ev.delta };
        }
        break;
      }
      case 'tool_call': {
        if (!ev.id) break;
        const cur = toolBuf.get(ev.id) ?? { args: '' };
        if (ev.status === 'start' || (!cur.name && ev.name)) {
          cur.name = ev.name;
          toolBuf.set(ev.id, cur);
          yield { type: 'tool-input-start', toolCallId: ev.id, toolName: ev.name ?? '' };
        }
        if (ev.arguments) {
          cur.args += ev.arguments;
          toolBuf.set(ev.id, cur);
          yield { type: 'tool-input-delta', toolCallId: ev.id, inputTextDelta: ev.arguments };
        }
        if (ev.status === 'end') {
          let parsed: unknown = cur.args;
          try {
            parsed = JSON.parse(cur.args);
          } catch {
            /* keep as string */
          }
          yield { type: 'tool-input-available', toolCallId: ev.id, toolName: cur.name ?? '', input: parsed };
          if (ev.result !== undefined) {
            yield { type: 'tool-output-available', toolCallId: ev.id, output: ev.result };
          }
        }
        break;
      }
      case 'citation': {
        let i = 0;
        for (const s of ev.sources) {
          if (s.url) {
            yield { type: 'source-url', sourceId: `${i++}`, url: s.url, title: s.title };
          }
        }
        break;
      }
      case 'interrupt':
        for (const c of closeText()) yield c;
        for (const c of closeReasoning()) yield c;
        yield { type: 'data-interrupt', data: { reason: ev.reason, payload: ev.payload } };
        break;
      case 'usage':
        yield {
          type: 'data-usage',
          data: {
            promptTokens: ev.promptTokens,
            completionTokens: ev.completionTokens,
            totalTokens: ev.totalTokens,
            costUsd: ev.costUsd,
          },
        };
        break;
      case 'error':
        for (const c of closeText()) yield c;
        for (const c of closeReasoning()) yield c;
        yield { type: 'error', errorText: ev.message };
        break;
      case 'done':
        // 走到下方统一收尾
        break;
    }
    if (ev.type === 'done') break;
  }
  for (const c of closeText()) yield c;
  for (const c of closeReasoning()) yield c;
  yield { type: 'finish-step' };
  yield { type: 'finish' };
}

/**
 * ai SDK v5 transport 实现的最小有效形态。
 *
 * `useChat({ transport })` 会调用 sendMessages(...)；返回一个 ReadableStream 即可。
 * 这里把异步迭代器封装为 ReadableStream<UIChunk>。
 */
export class ChayuanAiTransport {
  private readonly opts: ChayuanAiTransportOptions;
  constructor(opts: ChayuanAiTransportOptions) {
    this.opts = opts;
  }

  async sendMessages(args: {
    chatId: string;
    messages: AiSDKMessage[];
    abortSignal?: AbortSignal;
    body?: unknown;
  }): Promise<ReadableStream<UIChunk>> {
    const traceId = uuid();
    const req = this.opts.buildRequest({ messages: args.messages, body: args.body });
    this.opts.onTrace?.(traceId, { conversationId: req.conversation_id });

    const sendOpts: SendOptions = {
      signal: args.abortSignal,
      traceId,
      useWorker: this.opts.useWorker,
    };
    const events = this.opts.transport.chat(req, sendOpts);
    const messageId = uuid();

    return iterToStream(toUIChunks(events, messageId));
  }

  // 占位：编辑/重生 / 中止由 useChat 控制；reconnectToStream 在断网续接时使用
  async reconnectToStream(): Promise<ReadableStream<UIChunk> | null> {
    return null;
  }
}

function iterToStream<T>(it: AsyncGenerator<T>): ReadableStream<T> {
  return new ReadableStream<T>({
    async pull(controller) {
      try {
        const { value, done } = await it.next();
        if (done) {
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (e: unknown) {
        controller.error(e);
      }
    },
    cancel() {
      void it.return?.(undefined as unknown as T);
    },
  });
}
