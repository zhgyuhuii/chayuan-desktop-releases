/**
 * 察元 ChatGraph 适配器（POST /chat/v2/chat）。
 *
 * 输入：ChatRequestV2；输出：StructuredSSEEvent 流。
 *
 * 这一层是 ai SDK custom transport 与"裸用 SSE"两个调用方的公共底座。
 */

import { getPlatform, makeTraceparent } from '@chayuan/platform-shared';
import { getAccessToken, getClientConfig, refreshAccessToken } from '@chayuan/api';
import type { ChatRequestV2, ResumeDecision, SendOptions, StructuredSSEEvent } from './types';
import { runSSE } from './sse-runner';

async function authedSseFetch(path: string, init: RequestInit, traceId: string): Promise<Response> {
  const cfg = getClientConfig();
  const url = `${cfg.baseURL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
  const headers = new Headers(init.headers);
  const token = await getAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  headers.set('Accept', 'text/event-stream');
  if (init.body && !(init.body instanceof FormData) && typeof init.body !== 'string') {
    headers.set('Content-Type', 'application/json');
  } else if (typeof init.body === 'object' && init.body !== null && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('X-Trace-Id', traceId);
  headers.set('traceparent', makeTraceparent(traceId));

  const res = await getPlatform().net.sse(url, { ...init, headers });
  if (res.status === 401) {
    const fresh = await refreshAccessToken(async (refresh) => {
      const r = await getPlatform().net.fetch(`${cfg.baseURL.replace(/\/+$/, '')}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Trace-Id': traceId },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!r.ok) return null;
      return (await r.json()) as { access_token: string; refresh_token?: string };
    });
    if (!fresh) {
      const err = new Error('AuthRequired') as Error & { status?: number };
      err.status = 401;
      throw err;
    }
    headers.set('Authorization', `Bearer ${fresh}`);
    return getPlatform().net.sse(url, { ...init, headers });
  }
  return res;
}

export interface ChayuanTransport {
  chat(req: ChatRequestV2, opts: SendOptions): AsyncGenerator<StructuredSSEEvent>;
  /** 发起 HIL 决策（REST，不流）+ 重发同 conversation_id 的 v2 流 */
  resumeAndContinue(
    conversationId: string,
    decision: ResumeDecision,
    nextRequest: ChatRequestV2,
    opts: SendOptions,
  ): AsyncGenerator<StructuredSSEEvent>;
}

export function createChayuanTransport(opts?: { worker?: () => Worker }): ChayuanTransport {
  return {
    async *chat(req, sendOpts) {
      const body = JSON.stringify({ ...req, stream: true });
      const res = await authedSseFetch('/chat/v2/chat', {
        method: 'POST',
        body,
        signal: sendOpts.signal,
      }, sendOpts.traceId);

      if (!res.ok || !res.body) {
        const txt = await res.text().catch(() => '');
        throw new Error(`ChatGraph error ${res.status}: ${txt}`);
      }

      const worker = sendOpts.useWorker ? opts?.worker?.() : undefined;
      try {
        yield* runSSE(res.body, { signal: sendOpts.signal, worker });
      } finally {
        worker?.terminate();
      }
    },

    async *resumeAndContinue(conversationId, decision, nextRequest, sendOpts) {
      // step 1: REST resume (不返回 SSE)
      await getPlatform().net.fetch(
        `${getClientConfig().baseURL.replace(/\/+$/, '')}/chat/v2/chat/resume`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Trace-Id': sendOpts.traceId,
            'traceparent': makeTraceparent(sendOpts.traceId),
            ...(await authHeader()),
          },
          body: JSON.stringify({
            conversation_id: conversationId,
            approved: decision.approved,
            patch: decision.patch ?? {},
          }),
          signal: sendOpts.signal,
        },
      );

      // step 2: 重新发起 v2 流（同 conversation_id），让 LangGraph 从 checkpoint 续跑
      yield* this.chat({ ...nextRequest, conversation_id: conversationId }, sendOpts);
    },
  };
}

async function authHeader(): Promise<Record<string, string>> {
  const t = await getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
