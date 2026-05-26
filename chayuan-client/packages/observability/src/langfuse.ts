/**
 * Langfuse 客户端单例 + 高级包装。
 *
 * - 公钥 only；secret key 永不进客户端打包。
 * - 失败入 outbox；上线后 flush。
 * - 业务用 startTrace / logEvent / logSpan / logScore，不直接碰 langfuse SDK。
 *
 * 注：langfuse-js 自身有 batching；我们把它的 fetch 封一层并把失败转交 outbox。
 */

import { Langfuse } from 'langfuse';
import { feedback, type FeedbackBody } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import type { EventName } from './events';
import { Outbox } from './outbox';

export interface ObservabilityConfig {
  enabled: boolean;
  host: string;
  publicKey: string;
  projectId: string;
  release: string; // e.g. 'desktop@0.1.0'
  /** 同 Langfuse 的 environment 概念 */
  env?: 'dev' | 'staging' | 'prod';
}

let lfInstance: Langfuse | null = null;
let cfg: ObservabilityConfig = {
  enabled: false,
  host: '',
  publicKey: '',
  projectId: '',
  release: 'unknown',
};
let outbox: Outbox | null = null;
let userId: string | undefined;

export function configureObservability(c: ObservabilityConfig): void {
  cfg = c;
  if (!c.enabled || !c.publicKey || !c.host) {
    lfInstance = null;
    outbox?.stop();
    outbox = null;
    return;
  }
  lfInstance = new Langfuse({
    publicKey: c.publicKey,
    baseUrl: c.host,
    release: c.release,
    flushAt: 20,
    flushInterval: 5_000,
    // 将网络请求委托给 PAL.fetch，便于桌面绕 CORS、附 traceparent
    fetch: async (url: string, init?: RequestInit) => {
      try {
        return await getPlatform().net.fetch(url, init);
      } catch (e: unknown) {
        // 推送到 outbox
        outbox?.enqueue({ kind: 'lf-raw', url, init: serializeInit(init) }).catch(() => undefined);
        throw e;
      }
    },
  } as unknown as ConstructorParameters<typeof Langfuse>[0]);

  outbox = new Outbox(async (payload) => {
    const p = payload as { kind: string; url: string; init?: { method?: string; headers?: Record<string, string>; body?: string } };
    if (p.kind !== 'lf-raw') return false;
    try {
      const r = await getPlatform().net.fetch(p.url, p.init);
      return r.ok;
    } catch {
      return false;
    }
  });
  outbox.start(30_000);
}

function serializeInit(init?: RequestInit): { method?: string; headers?: Record<string, string>; body?: string } | undefined {
  if (!init) return undefined;
  const headers: Record<string, string> = {};
  if (init.headers instanceof Headers) {
    init.headers.forEach((v, k) => (headers[k] = v));
  } else if (Array.isArray(init.headers)) {
    for (const [k, v] of init.headers) headers[k] = v;
  } else if (init.headers) {
    Object.assign(headers, init.headers as Record<string, string>);
  }
  return {
    method: init.method,
    headers,
    body: typeof init.body === 'string' ? init.body : undefined,
  };
}

export function setUser(id: string): void {
  userId = id;
}
export function getCurrentUserId(): string | undefined {
  return userId;
}

export function deepLinkTrace(traceId: string): string {
  if (!cfg.host || !cfg.projectId) return '';
  return `${cfg.host.replace(/\/+$/, '')}/project/${cfg.projectId}/traces/${traceId}`;
}

// ──────────────────────────────────────────────────────────────
// Trace / span / score 高级 API
// ──────────────────────────────────────────────────────────────

export interface StartTraceInput {
  traceId: string;
  conversationId?: string;
  model?: string;
  mode?: string;
  /** 把当前用户角色等元数据带上，用于看板切片 */
  metadata?: Record<string, unknown>;
}

export function startTrace(input: StartTraceInput): void {
  if (!lfInstance) return;
  try {
    lfInstance.trace({
      id: input.traceId,
      name: 'chat',
      sessionId: input.conversationId,
      userId,
      release: cfg.release,
      metadata: { model: input.model, mode: input.mode, source: cfg.release.split('@')[0], env: cfg.env, ...input.metadata },
    });
  } catch {
    /* noop */
  }
}

export function logEvent(name: EventName | string, attrs?: { traceId?: string; metadata?: Record<string, unknown> }): void {
  if (!lfInstance) return;
  try {
    if (attrs?.traceId) {
      lfInstance.event({
        traceId: attrs.traceId,
        name,
        metadata: attrs.metadata,
      });
    } else {
      // 单独的客户端事件：起一个 short trace
      const tid = `evt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      lfInstance.trace({ id: tid, name, userId, release: cfg.release });
      lfInstance.event({ traceId: tid, name, metadata: attrs?.metadata });
    }
  } catch {
    /* noop */
  }
}

export interface SpanHandle {
  end(metadata?: Record<string, unknown>): void;
}

export function startSpan(traceId: string, name: string, metadata?: Record<string, unknown>): SpanHandle {
  if (!lfInstance) return { end() {} };
  let span: ReturnType<Langfuse['span']> | null = null;
  try {
    span = lfInstance.span({ traceId, name, metadata });
  } catch {
    /* ignore */
  }
  return {
    end(meta) {
      try {
        span?.end({ metadata: meta });
      } catch {
        /* ignore */
      }
    },
  };
}

export interface FeedbackScoreInput {
  traceId: string;
  conversationId?: string;
  messageId?: string;
  name: 'user_feedback' | 'aborted' | 'regenerated' | 'edited' | string;
  value: number;
  comment?: string;
}

/**
 * 用户反馈：走后端 /chat/feedback，由后端写 Langfuse score（保持 secret key 在服务端）。
 * 客户端单测/admin 也可以直接 lfInstance.score；这里统一过后端，便于审计。
 */
export async function logScore(input: FeedbackScoreInput): Promise<void> {
  const body: FeedbackBody = {
    trace_id: input.traceId,
    conversation_id: input.conversationId,
    message_id: input.messageId,
    score: input.value,
    name: input.name,
    comment: input.comment,
  };
  try {
    await feedback.submit(body);
  } catch {
    // 离线降级：写到 outbox，等下次 flush
    outbox?.enqueue({ kind: 'feedback', body }).catch(() => undefined);
  }
}

export async function shutdown(): Promise<void> {
  outbox?.stop();
  try {
    await lfInstance?.flushAsync();
  } catch {
    /* noop */
  }
}
