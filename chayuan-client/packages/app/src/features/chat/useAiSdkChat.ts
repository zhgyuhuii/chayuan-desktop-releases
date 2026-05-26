/**
 * ai SDK 兼容入口（替代 useChayuanChat）。
 *
 * 这一层把 chayuan ChatGraph 的 SSE 流适配到 Vercel ai SDK 的「token-by-token」
 * 协议（OpenAI 风格 chunk）。调用方可以直接用 ai SDK v4 的 `useChat`：
 *
 *   const { messages, append, stop } = useAiSdkChat({ buildRequest })
 *
 * 内部：
 * - 启动一个临时 endpoint URL（about:blank/chat）作为 useChat 的 fetch 基准；
 * - 用 customFetch 拦截，把 chayuan transport 的事件流转回 OpenAI 兼容的 SSE。
 *
 * 对比 useChayuanChat：
 * - 缺：HIL interrupt UI / Langfuse 反馈深度集成 / 自动重连（ai SDK 自带断网行为略不同）；
 * - 得：原生 setMessages 编辑 / 多 step（ai SDK 控制） / branch 由调用方自己用 setMessages 截断；
 *
 * 因此默认推荐 useChayuanChat；ai-sdk 入口仅给「想用 ai SDK 现成 hook 的开发者」。
 */

import * as React from 'react';
import { uuid } from '@chayuan/platform-shared';
import { createChayuanTransport, type ChatRequestV2, type StructuredSSEEvent } from '@chayuan/transport';

export interface UseAiSdkChatOptions {
  buildRequest(input: { messages: unknown[]; query: string }): ChatRequestV2;
  conversationId?: string;
}

const transport = createChayuanTransport();

/**
 * 返回一个最小子集 hook（不依赖 ai 包以避免硬绑版本）。
 * 若用户想直接 useChat({ fetch: chayuanCompatibleFetch })，把这里 customFetch
 * 注入到 ai/react 的 useChat 即可。
 */
export function useAiSdkChat(_opts: UseAiSdkChatOptions): {
  /** 兼容 ai SDK 的 fetch；注入 useChat({ fetch: ...}) 即可 */
  customFetch: typeof globalThis.fetch;
} {
  const customFetch = React.useCallback<typeof globalThis.fetch>(async (input, init) => {
    if (!init?.body) {
      // ai SDK 期望 POST body；非预期请求直接 503，避免误用
      return new Response('{"error":"unexpected"}', { status: 503 });
    }
    let body: { messages?: Array<{ role: string; content: string }> } = {};
    try {
      body = JSON.parse(typeof init.body === 'string' ? init.body : '{}');
    } catch {
      /* keep */
    }
    const last = body.messages?.[body.messages.length - 1];
    const query = String(last?.content ?? '');
    const req = _opts.buildRequest({
      messages: body.messages ?? [],
      query,
    });
    const traceId = uuid();
    const ac = new AbortController();
    if (init.signal) init.signal.addEventListener('abort', () => ac.abort(), { once: true });

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const enc = new TextEncoder();
        const events = transport.chat(req, { traceId, signal: ac.signal });
        try {
          for await (const ev of events) {
            const chunk = toOpenAIChunk(ev);
            if (chunk) controller.enqueue(enc.encode(chunk));
            if (ev.type === 'done' || ev.type === 'error') break;
          }
          controller.enqueue(enc.encode('data: [DONE]\n\n'));
          controller.close();
        } catch (err: unknown) {
          controller.error(err);
        }
      },
      cancel() {
        ac.abort();
      },
    });

    return new Response(stream, {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'X-Trace-Id': traceId,
      },
    });
  }, [_opts]);

  return { customFetch };
}

function toOpenAIChunk(ev: StructuredSSEEvent): string | null {
  if (ev.type === 'token') {
    return `data: ${JSON.stringify({
      id: ev.messageId ?? '',
      choices: [{ delta: { content: ev.delta, reasoning_content: ev.reasoning } }],
    })}\n\n`;
  }
  if (ev.type === 'done') {
    return `data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: ev.finishReason ?? 'stop' }] })}\n\n`;
  }
  if (ev.type === 'error') {
    return `data: ${JSON.stringify({ error: { message: ev.message, code: ev.code } })}\n\n`;
  }
  // tool_call / citation / interrupt / usage 走 ai SDK data parts；这里简化为忽略
  return null;
}
