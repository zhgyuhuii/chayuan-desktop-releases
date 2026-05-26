/**
 * useChayuanChat — 主对话状态机。
 *
 * 提供：
 *   messages / isStreaming / error
 *   sendMessage(content)        发送
 *   stop()                      中止当前流（不抛错）
 *   regenerate()                重新生成最后一条 assistant
 *   editMessage(id, newContent) 编辑某条 user / assistant 消息：
 *                               - user：截断它之后的所有消息，重发
 *                               - assistant：把 user 也保留，截断后重发
 *   branchFrom(id)              从某条消息分叉为新会话（保留 0..i 的快照）
 *   resumeWithDecision(...)     HIL 决策续跑
 *
 * 自动能力：
 *   - 网络恢复（online）后若上次发送是网络错误 → 自动重发一次（reconnectOnce）
 *   - 流式中检测到 visibilitychange 隐藏 → 不停（让 SSE 持续）
 *
 * 与 Langfuse 集成：trace 创建即 startTrace；first_token / complete / aborted /
 * regenerated / edited 事件 + scores 全部打点。
 */

import * as React from 'react';
import { uuid } from '@chayuan/platform-shared';
import {
  createChayuanTransport,
  type ChatRequestV2,
  type MountedContextSummary,
  type MountedSource,
  type StructuredSSEEvent,
  type ResumeDecision,
} from '@chayuan/transport';
import { Events, logEvent, logScore, startSpan, startTrace } from '@chayuan/observability';
import { useComposerStore } from '../../store/composer';

export interface ChatToolCall {
  id: string;
  name?: string;
  arguments?: string;
  result?: unknown;
  status?: 'start' | 'delta' | 'end';
}

export interface ChatCitation {
  title?: string;
  url?: string;
  snippet?: string;
  score?: number;
  chunks?: Array<{
    content?: string;
    snippet?: string;
    score?: number;
    page?: number | null;
    chunk_index?: number | null;
    retrieval_path?: string;
    title?: string;
  }>;
  /** 来自知识库:KB 名(供前端开预览) */
  kb_name?: string;
  /** 来自知识库:文件名 */
  file_name?: string;
  /** 来源类型;有 kb_name+file_name 时通常是 'kb' */
  source_kind?: 'kb' | 'web' | 'file' | string;
  /** 1-based 引用序号;与答案文本中 LLM 写的「[出处 N]」一一对应 */
  cite_index?: number;
  /** 命中片段数(同一文件多 chunk 命中时由后端聚合) */
  chunk_count?: number;
}

/**
 * 模态路由生成的产物附件(图/音/视)— v5 ``file`` part 落到消息上的载体。
 *
 * 字段贴近 v5 file part:``mediaType`` + ``url`` + ``metadata``。
 * 与 ``content``(文本)正交,可同时存在;``files`` 兜底为 ``undefined``,
 * 老消息不需要迁移。
 */
export interface ModalityFileAttachment {
  /** 通常 image/png / audio/wav / video/mp4 */
  mediaType: string;
  /** 后端 artifacts URL,形如 ``/v1/artifacts/<sha>.<ext>`` */
  url: string;
  /** width / height / prompt / seed / sha256 / model — Connector 自定义 */
  metadata?: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  reasoning?: string;
  toolCalls?: ChatToolCall[];
  citations?: ChatCitation[];
  mounted?: MountedContextSummary;
  mountedSources?: MountedSource[];
  interrupt?: { reason?: string; payload?: unknown };
  /** Langfuse trace；用于深链 / 反馈 */
  traceId?: string;
  createdAt: number;
  streaming?: boolean;
  error?: string;
  /** 编辑过的消息会带上 editedFrom（旧 traceId），便于 trace 内追溯 */
  editedFrom?: string;
  /** 模态路由产物附件(t2i/t2v/tts 生成的图/音/视);chat 模式不填,渲染层兼容 undefined */
  files?: ModalityFileAttachment[];
  /** 模态路由异步任务进度(t2v 30-90s 不能纯靠 text 显示);chat 模式不填 */
  taskProgress?: {
    percent: number;
    message?: string;
    eta_s?: number;
    task_id?: string;
  };
  /**
   * PR-9 D:模态路由任务 id —— 后端 TaskManager 在 ``data-modality-meta``
   * 事件里下发。落库后,会话重开时若 ``streaming === true``,前端按这个
   * id 续 ``/v1/modality/tasks/<id>/events`` SSE 流。
   */
  taskId?: string;
  /** 与 taskId 同时下发的 capability(t2i / image_edit / t2v / tts / asr / ...)。 */
  taskCapability?: string;
}

export interface UseChayuanChatOptions {
  conversationId?: string;
  buildRequest(input: { messages: ChatMessage[]; query: string }): ChatRequestV2;
  onTrace?(traceId: string): void;
  /** 创建分叉会话时回调 */
  onBranch?(newConvId: string, snapshot: ChatMessage[]): void;
  /** 网络恢复后是否自动重发一次失败请求；默认 true */
  autoReconnect?: boolean;
}

const transport = createChayuanTransport();

interface InternalState {
  /** 上次失败的发送内容，用于 online 自动重发 */
  lastFailedQuery?: string;
  lastFailedTraceId?: string;
}

export function useChayuanChat(opts: UseChayuanChatOptions): {
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  isStreaming: boolean;
  error: string | null;
  sendMessage(content: string): Promise<void>;
  stop(): void;
  regenerate(): Promise<void>;
  editMessage(id: string, newContent: string): Promise<void>;
  branchFrom(id: string): Promise<string>;
  resumeWithDecision(decision: ResumeDecision): Promise<void>;
  /** 流式开始时间戳；流结束后保留最近一次以便 UI 显示 */
  startedAt: number | null;
  firstTokenAt: number | null;
} {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [isStreaming, setStreaming] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [startedAt, setStartedAt] = React.useState<number | null>(null);
  const [firstTokenAt, setFirstTokenAt] = React.useState<number | null>(null);
  const setComposerStreaming = useComposerStore((s) => s.setStreaming);
  const abortRef = React.useRef<AbortController | null>(null);
  const lastUserContentRef = React.useRef<string>('');
  const internalRef = React.useRef<InternalState>({});
  const optsRef = React.useRef(opts);
  optsRef.current = opts;

  const sendMessage = React.useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || isStreaming) return;

      const traceId = uuid();
      const userMsg: ChatMessage = {
        id: uuid(),
        role: 'user',
        content: trimmed,
        createdAt: Date.now(),
        traceId,
      };
      const assistantMsg: ChatMessage = {
        id: uuid(),
        role: 'assistant',
        content: '',
        streaming: true,
        createdAt: Date.now(),
        traceId,
      };

      lastUserContentRef.current = trimmed;
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);
      setComposerStreaming(true);
      setStartedAt(Date.now());
      setFirstTokenAt(null);
      setError(null);

      const o = optsRef.current;
      const req = o.buildRequest({ messages: [...messages, userMsg], query: trimmed });
      startTrace({ traceId, conversationId: o.conversationId, model: req.model, mode: req.mode });
      logEvent(Events.ChatSend, { traceId, metadata: { model: req.model, mode: req.mode } });
      o.onTrace?.(traceId);
      const composeSpan = startSpan(traceId, 'client.compose');
      let firstTokenSpan: ReturnType<typeof startSpan> | null = null;

      abortRef.current = new AbortController();
      const ac = abortRef.current;

      try {
        let firstTokenSeen = false;
        const stream = transport.chat(req, { traceId, signal: ac.signal });
        for await (const ev of stream) {
          applyEventTo(setMessages, assistantMsg.id, ev);
          if (ev.type === 'token' && !firstTokenSeen) {
            firstTokenSeen = true;
            composeSpan.end();
            firstTokenSpan = startSpan(traceId, 'client.ttft');
            logEvent(Events.ChatFirstToken, { traceId });
            setFirstTokenAt(Date.now());
          }
          if (ev.type === 'done' || ev.type === 'error') break;
        }
        firstTokenSpan?.end();
        logEvent(Events.ChatComplete, { traceId });
        internalRef.current.lastFailedQuery = undefined;
        internalRef.current.lastFailedTraceId = undefined;
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') {
          logEvent(Events.ChatAborted, { traceId });
          await logScore({ traceId, name: 'aborted', value: 1 }).catch(() => undefined);
        } else {
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg);
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMsg.id ? { ...m, error: msg, streaming: false } : m)),
          );
          logEvent(Events.ErrorNetwork, { traceId, metadata: { message: msg } });
          // 记录给 reconnect 用
          internalRef.current.lastFailedQuery = trimmed;
          internalRef.current.lastFailedTraceId = traceId;
        }
      } finally {
        setMessages((prev) => prev.map((m) => (m.id === assistantMsg.id ? { ...m, streaming: false } : m)));
        setStreaming(false);
        setComposerStreaming(false);
        abortRef.current = null;
      }
    },
    [isStreaming, messages, setComposerStreaming],
  );

  const stop = React.useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const regenerate = React.useCallback(async () => {
    if (isStreaming || !lastUserContentRef.current) return;
    // 删除最后一条 assistant 消息
    setMessages((prev) => {
      const next = [...prev];
      const idx = [...next].reverse().findIndex((m) => m.role === 'assistant');
      if (idx >= 0) next.splice(next.length - 1 - idx, 1);
      return next;
    });
    const lastMsg = findLast(messages, (m) => m.role === 'assistant');
    if (lastMsg?.traceId) {
      await logScore({ traceId: lastMsg.traceId, name: 'regenerated', value: 1 }).catch(() => undefined);
      logEvent(Events.ChatRegenerated, { traceId: lastMsg.traceId });
    }
    await sendMessage(lastUserContentRef.current);
  }, [isStreaming, messages, sendMessage]);

  /**
   * 编辑消息：
   * - 若 id 命中 user 消息：截断它**之后**的所有消息，用新内容重发；
   * - 若 id 命中 assistant 消息：截断它**之前最近一条 user**之前 + 自己之后所有，
   *   即视为「重新提问紧邻的那条 user」；
   *
   * trace 上会带 editedFrom = 旧 traceId，便于在 Langfuse 看出编辑链。
   */
  const editMessage = React.useCallback(
    async (id: string, newContent: string) => {
      if (isStreaming) return;
      const idx = messages.findIndex((m) => m.id === id);
      if (idx === -1) return;
      const target = messages[idx]!;
      let cutTo = idx;
      let userText = newContent;

      if (target.role === 'user') {
        cutTo = idx; // 重发该 user
      } else if (target.role === 'assistant') {
        // 找紧邻的上一条 user
        const userIdx = idx - 1 >= 0 ? idx - 1 : -1;
        if (userIdx === -1 || messages[userIdx]!.role !== 'user') return;
        cutTo = userIdx;
        userText = messages[userIdx]!.content; // 编辑 assistant 时复用原 user 内容
        if (newContent.trim()) userText = newContent.trim();
      } else {
        return;
      }

      setMessages((prev) => prev.slice(0, cutTo));
      const editedFrom = target.traceId;
      if (editedFrom) {
        await logScore({ traceId: editedFrom, name: 'edited', value: 1 }).catch(() => undefined);
        logEvent(Events.ChatEdited, { traceId: editedFrom });
      }
      // 重发；新消息会自动获得新 traceId；UI 端追加 editedFrom 不破坏类型
      lastUserContentRef.current = userText;
      await sendMessage(userText);
      if (editedFrom) {
        // 标注新发的 user 消息携带 editedFrom（用于历史回看）
        setMessages((prev) =>
          prev.map((m, i, arr) => (i === arr.length - 2 && m.role === 'user' ? { ...m, editedFrom } : m)),
        );
      }
    },
    [isStreaming, messages, sendMessage],
  );

  /**
   * 分叉：以某条消息为锚点 fork 为一个新会话。
   * - 截取 0..idx 的快照，由调用方在 onBranch 回调中创建新会话并写库。
   * - 返回新 conversation_id（client 侧 uuid）；后端首次发送时会接管。
   */
  const branchFrom = React.useCallback(
    async (id: string) => {
      const idx = messages.findIndex((m) => m.id === id);
      if (idx === -1) return '';
      const snapshot = messages.slice(0, idx + 1);
      const newConvId = uuid();
      optsRef.current.onBranch?.(newConvId, snapshot);
      logEvent('chat.branched', { metadata: { fromTraceId: messages[idx]?.traceId, count: snapshot.length } });
      return newConvId;
    },
    [messages],
  );

  const resumeWithDecision = React.useCallback(
    async (decision: ResumeDecision) => {
      const last = findLast(messages, (m) => m.role === 'assistant' && !!m.interrupt);
      if (!last?.traceId || !optsRef.current.conversationId) return;
      const evtName = decision.approved ? Events.ToolApproved : Events.ToolRejected;
      logEvent(evtName, { traceId: last.traceId, metadata: { patch: decision.patch } });

      setStreaming(true);
      setComposerStreaming(true);
      abortRef.current = new AbortController();
      try {
        setMessages((prev) =>
          prev.map((m) => (m.id === last.id ? { ...m, interrupt: undefined, streaming: true } : m)),
        );
        const nextReq = optsRef.current.buildRequest({ messages, query: lastUserContentRef.current });
        const stream = transport.resumeAndContinue(optsRef.current.conversationId, decision, nextReq, {
          traceId: last.traceId,
          signal: abortRef.current.signal,
        });
        for await (const ev of stream) {
          applyEventTo(setMessages, last.id, ev);
          if (ev.type === 'done' || ev.type === 'error') break;
        }
      } finally {
        setMessages((prev) => prev.map((m) => (m.id === last.id ? { ...m, streaming: false } : m)));
        setStreaming(false);
        setComposerStreaming(false);
        abortRef.current = null;
      }
    },
    [messages, setComposerStreaming],
  );

  // ── 网络恢复自动重连 ─────────────────────────────────────────
  React.useEffect(() => {
    if (opts.autoReconnect === false) return;
    const onOnline = () => {
      const failed = internalRef.current.lastFailedQuery;
      if (!failed || isStreaming) return;
      logEvent('net.online_retry', {
        traceId: internalRef.current.lastFailedTraceId,
        metadata: { query: failed.slice(0, 80) },
      });
      void sendMessage(failed);
    };
    globalThis.addEventListener?.('online', onOnline);
    return () => globalThis.removeEventListener?.('online', onOnline);
  }, [isStreaming, opts.autoReconnect, sendMessage]);

  return {
    messages,
    setMessages,
    isStreaming,
    error,
    sendMessage,
    stop,
    regenerate,
    editMessage,
    branchFrom,
    resumeWithDecision,
    startedAt,
    firstTokenAt,
  };
}

/**
 * 把 SSE 事件应用到对应 message 上。纯函数式 setState，避免 race。
 */
function applyEventTo(
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  msgId: string,
  ev: StructuredSSEEvent,
): void {
  setMessages((prev) =>
    prev.map((m) => {
      if (m.id !== msgId) return m;
      switch (ev.type) {
        case 'token':
          return {
            ...m,
            content: m.content + (ev.delta ?? ''),
            reasoning: ev.reasoning ? (m.reasoning ?? '') + ev.reasoning : m.reasoning,
          };
        case 'tool_call': {
          if (!ev.id) return m;
          const list = m.toolCalls ?? [];
          const idx = list.findIndex((t) => t.id === ev.id);
          if (idx === -1) {
            return {
              ...m,
              toolCalls: [
                ...list,
                {
                  id: ev.id,
                  name: ev.name,
                  arguments: ev.arguments ?? '',
                  status: ev.status,
                  result: ev.result,
                },
              ],
            };
          }
          const next = [...list];
          const cur = next[idx]!;
          next[idx] = {
            ...cur,
            name: ev.name ?? cur.name,
            arguments: (cur.arguments ?? '') + (ev.arguments ?? ''),
            status: ev.status ?? cur.status,
            result: ev.result ?? cur.result,
          };
          return { ...m, toolCalls: next };
        }
        case 'citation':
          return { ...m, citations: [...(m.citations ?? []), ...ev.sources] };
        case 'mounted':
          return {
            ...m,
            mounted: { ...(m.mounted ?? {}), ...ev.summary },
            mountedSources: [...(m.mountedSources ?? []), ...(ev.sources ?? [])],
          };
        case 'interrupt':
          return { ...m, interrupt: { reason: ev.reason, payload: ev.payload } };
        case 'usage':
          return m;
        case 'error':
          return { ...m, error: ev.message };
        case 'done':
          return m;
      }
    }),
  );
}

function findLast<T>(arr: T[], pred: (v: T) => boolean): T | undefined {
  for (let i = arr.length - 1; i >= 0; i--) if (pred(arr[i]!)) return arr[i];
  return undefined;
}
