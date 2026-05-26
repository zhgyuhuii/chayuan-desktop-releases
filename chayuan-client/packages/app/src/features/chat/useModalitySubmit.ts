/**
 * 模态路由提交 hook —— 非聊天 capability(t2i/t2v/tts/image_edit 等)的对话提交链路。
 *
 * 与 `useChayuanChat` 的关系:
 *   - 共享同一个 `setMessages`(传入),所以消息列表统一,不分两条 store
 *   - `isStreaming` 独立,父级用 `chat.isStreaming || modality.isStreaming` 给 ChatComposer
 *   - 失败/取消语义独立(t2i 的"取消"是 AbortController,chat 是 EventSource close)
 *
 * 事件 → 消息 part 映射:
 *   v5 `start`            → 创建 assistant 消息(streaming=true)
 *   v5 `text-delta`       → assistant.content += delta
 *   v5 `file`             → assistant.files.push({mediaType, url, metadata})
 *   v5 `source-*`         → assistant.citations.push(...)(暂未接,留 PR-5)
 *   v5 `data-task-progress` → 暂展示在 assistant.content 末尾(简单显示;
 *                            PR-4 引入独立 progress part 后剥离)
 *   v5 `error`            → assistant.error = errorText;streaming=false
 *   v5 `finish`           → streaming=false
 *
 * 注意:**只**处理非 chat capability。chat 模型继续走 useChayuanChat。
 */

import * as React from 'react';
import { uuid } from '@chayuan/platform-shared';
import {
  classifyModalityCapability,
  streamModality,
  streamModalityTaskEvents,
  type ModalityAttachment,
  type V5Event,
  isFilePart,
  isTextDelta,
  isDataPart,
  isErrorEvent,
} from '@chayuan/transport';
import type { ChatMessage, ModalityFileAttachment } from './useChayuanChat';

export interface UseModalitySubmitOptions {
  /** 共用 useChayuanChat.setMessages */
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  /** 后端 API base — chayuan-server 单机版固定 ``http://127.0.0.1:62581`` */
  baseUrl: string;
  /** 鉴权头 — 可选,跟随老 transport 一致 */
  authHeader?: string;
  /** 出错时上报(reportError 等)— 可选 */
  onError?(message: string): void;
  /**
   * PR-9 D:任务 id 第一次出现时回调(只触发一次)。
   *
   * 用途:ConversationView 收到 task_id 后立即把 user + assistant 消息对落库,
   * 避免"流式中关浏览器 → 消息没进 DB → 重开不存在"的窗口。
   */
  onTaskRegistered?(asstMsgId: string, taskId: string, capability: string): void;
}

export interface UseModalitySubmitReturn {
  /** 当前是否在流式生成 */
  isStreaming: boolean;
  /** 中止当前生成 — 内部 AbortController.abort() */
  stop(): void;
  /**
   * 判断给定 modelId 是否应走本 hook(capability 非 chat 且非 vl)。
   * vl 走老 chat 路径(content 数组)。
   */
  shouldHandle(modelId: string | null | undefined): boolean;
  /** 执行一次提交;前置:shouldHandle(modelId) === true */
  run(input: {
    text: string;
    modelId: string;
    params?: Record<string, unknown>;
    attachments?: ModalityAttachment[];
  }): Promise<void>;
  /**
   * PR-9 D:把一条已落库但状态为 streaming 的 assistant 消息接回后台任务事件流。
   *
   * 调用前 ChatMessage 应该已经在 messages 列表里(从 listMessages 读出来),
   * 本函数只负责挂 SSE → patchAsst → done 时把 streaming 清掉。
   */
  reconnect(input: { taskId: string; asstMsgId: string }): Promise<void>;
}

export function useModalitySubmit(opts: UseModalitySubmitOptions): UseModalitySubmitReturn {
  const { setMessages, baseUrl, authHeader, onError, onTaskRegistered } = opts;
  const [isStreaming, setStreaming] = React.useState(false);
  // run() 和 reconnect() 都注册自己的 AbortController;多条 reconnect 共存,
  // 用 Map<asstMsgId, ctrl> 记录,stop() 把全部 abort 掉。
  const abortRef = React.useRef<AbortController | null>(null);
  const reconnectCtrlsRef = React.useRef<Map<string, AbortController>>(new Map());

  const stop = React.useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    for (const ctrl of reconnectCtrlsRef.current.values()) {
      try { ctrl.abort(); } catch { /* ignore */ }
    }
    reconnectCtrlsRef.current.clear();
    setStreaming(false);
  }, []);

  // 把 SSE 事件应用到一条 assistant 消息上 — run() 和 reconnect() 共用,
  // 这样两条路径的事件 → 状态映射严格一致(避免行为漂移)。
  const consumeStream = React.useCallback(
    async (
      stream: AsyncIterator<V5Event>,
      asstMsgId: string,
      signal: AbortSignal,
    ): Promise<void> => {
      const patchAsst = (mutator: (m: ChatMessage) => ChatMessage) => {
        setMessages((prev) => prev.map((m) => (m.id === asstMsgId ? mutator(m) : m)));
      };
      let registeredTaskId: string | null = null;
      try {
        while (true) {
          const { value: ev, done } = await stream.next();
          if (done) break;
          if (signal.aborted) break;

          if (isTextDelta(ev)) {
            patchAsst((m) => ({ ...m, content: m.content + ev.delta }));
            continue;
          }
          if (isFilePart(ev)) {
            // 后端 file part 给的是 ``/v1/artifacts/<sha>.<ext>`` 相对路径(content-addressed)。
            // Tauri 桌面文档源是 ``tauri://localhost``,相对 URL 会落到错的 host →
            // <img>/<audio> 加载 404。这里统一拼成绝对 URL,Web/Desktop 都正常。
            // 已经是绝对 URL(http://…)的直接透传 — 兼容上游直接给 CDN 链接。
            const absUrl = ev.url.startsWith('/')
              ? `${baseUrl.replace(/\/+$/, '')}${ev.url}`
              : ev.url;
            const attachment: ModalityFileAttachment = {
              mediaType: ev.mediaType,
              url: absUrl,
              metadata: ev.metadata,
            };
            patchAsst((m) => {
              // 续流场景 message.files 已有同 url 的产物时去重,避免 replay buffer
              // + DB 合成事件双跑导致同一张图渲染两次
              const exists = (m.files ?? []).some((f) => f.url === absUrl);
              if (exists) return m;
              return { ...m, files: [...(m.files ?? []), attachment] };
            });
            continue;
          }
          if (isErrorEvent(ev)) {
            patchAsst((m) => ({ ...m, error: ev.errorText, streaming: false }));
            onError?.(ev.errorText);
            continue;
          }
          if (isDataPart(ev)) {
            if (ev.type === 'data-modality-meta') {
              // PR-9 B 起后端在 meta 事件里下发 task_id + capability
              const data = ev.data as
                | { task_id?: string; capability?: string }
                | null;
              const tid = data?.task_id;
              const cap = data?.capability;
              if (tid && !registeredTaskId) {
                registeredTaskId = tid;
                patchAsst((m) => ({ ...m, taskId: tid, taskCapability: cap }));
                if (cap) {
                  onTaskRegistered?.(asstMsgId, tid, cap);
                }
              }
              continue;
            }
            if (ev.type === 'data-task-progress') {
              // 写入独立的 taskProgress 字段,MessageBubble 渲染进度条;
              // 不再污染 content(早期 PR-3 是用 content 当兜底)。
              const data = ev.data as
                | { percent?: number; message?: string; eta_s?: number; task_id?: string }
                | null;
              if (data) {
                patchAsst((m) => ({
                  ...m,
                  taskProgress: {
                    percent: Math.max(0, Math.min(100, data.percent ?? 0)),
                    message: data.message,
                    eta_s: data.eta_s,
                    task_id: data.task_id,
                  },
                }));
              }
              continue;
            }
          }
          // 其它事件(start / finish / source-* / usage 等)不影响渲染,忽略
        }
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') {
          patchAsst((m) => ({ ...m, streaming: false }));
          return;
        }
        const msg = e instanceof Error ? e.message : String(e);
        patchAsst((m) => ({ ...m, error: msg, streaming: false }));
        onError?.(msg);
      } finally {
        patchAsst((m) => ({ ...m, streaming: false }));
      }
    },
    [setMessages, baseUrl, onError, onTaskRegistered],
  );

  const shouldHandle = React.useCallback((modelId: string | null | undefined): boolean => {
    const cap = classifyModalityCapability(modelId);
    // chat / vl 走老 chat 链路(VL 用多模态 content list,老 chat 支持)
    return cap !== 'chat' && cap !== 'vl';
  }, []);

  const run = React.useCallback(
    async (input: {
      text: string;
      modelId: string;
      params?: Record<string, unknown>;
      attachments?: ModalityAttachment[];
    }) => {
      const text = input.text.trim();
      // 大多数 modality 模式必须有 prompt;ASR / image_edit 等场景 prompt 可为空,
      // 由附件承担"输入"角色 — 这种情况下只要有附件就允许提交。
      const hasAttachments = (input.attachments?.length ?? 0) > 0;
      if (!text && !hasAttachments) return;
      const now = Date.now();

      // 1. 推 user + assistant 占位 — 空 prompt 的 ASR 场景下,user 气泡显示一句友好占位
      const userBubble = text || (hasAttachments ? '【音频文件】请转写' : '');
      const userMsg: ChatMessage = {
        id: uuid(),
        role: 'user',
        content: userBubble,
        createdAt: now,
      };
      const asstId = uuid();
      const asstMsg: ChatMessage = {
        id: asstId,
        role: 'assistant',
        content: '',
        createdAt: now + 1,
        streaming: true,
        files: [],
      };
      setMessages((prev) => [...prev, userMsg, asstMsg]);

      // 2. 启动 SSE 流
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setStreaming(true);

      try {
        const stream = streamModality(
          {
            model: input.modelId,
            prompt: text,
            attachments: input.attachments,
            params: input.params,
            baseUrl,
            authHeader,
          },
          ctrl.signal,
        );
        await consumeStream(
          stream as AsyncGenerator<V5Event>,
          asstId,
          ctrl.signal,
        );
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [setMessages, baseUrl, authHeader, consumeStream],
  );

  // PR-9 D:把已落库但 streaming=true 的消息接回后台事件流
  const reconnect = React.useCallback(
    async (input: { taskId: string; asstMsgId: string }) => {
      // 同一条消息重复 reconnect 直接忽略 — 避免会话 tab 切换/effect 抖动导致
      // 多份 SSE 同时跑(buffer replay 阶段会双倍消费 file/text-delta 事件)
      if (reconnectCtrlsRef.current.has(input.asstMsgId)) return;
      const ctrl = new AbortController();
      reconnectCtrlsRef.current.set(input.asstMsgId, ctrl);
      setStreaming(true);
      try {
        const stream = streamModalityTaskEvents(
          { taskId: input.taskId, baseUrl, authHeader },
          ctrl.signal,
        );
        await consumeStream(stream as AsyncGenerator<V5Event>, input.asstMsgId, ctrl.signal);
      } finally {
        reconnectCtrlsRef.current.delete(input.asstMsgId);
        // 没有正在跑的 SSE 时才清 streaming(避免 run + reconnect 并存时误清)
        if (
          !abortRef.current && reconnectCtrlsRef.current.size === 0
        ) {
          setStreaming(false);
        }
      }
    },
    [baseUrl, authHeader, consumeStream],
  );

  return { isStreaming, stop, shouldHandle, run, reconnect };
}
