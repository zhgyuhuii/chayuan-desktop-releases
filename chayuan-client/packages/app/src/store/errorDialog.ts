/**
 * 全局信息 / 错误弹窗 store。
 *
 * 用途:任何位置(loader / fetcher / 路由 / EventEmitter / unhandledrejection)
 * 抛出异常或想给用户展示一段提示,都通过 useErrorDialog.getState().show() 唤起,
 * 替代 `alert()` 以及 "/chat/<uuid> {detail: Not Found}" 这种裸页 / 浏览器原生 alert。
 *
 * 设计取舍:
 * - 单条只显示一个 modal;新条 push 进 queue,关掉一条后展示下一条。
 *   这样并发提示(SSE 中断 + token 过期)不会刷屏。
 * - kind: 'error' / 'info' / 'success';ErrorDialog 据此换图标 + 配色。
 * - autoDismissMs:可选自动关闭,info / success 推荐 2~3s。
 */

import { create } from 'zustand';

export type EntryKind = 'error' | 'info' | 'success';

export interface ErrorEntry {
  id: string;
  kind: EntryKind;
  title: string;
  detail?: string;
  /** 可选:展示原始堆栈/响应体,默认收起 */
  raw?: string;
  /** 可选:超时自动关 */
  autoDismissMs?: number;
}

export interface ShowOptions {
  detail?: string;
  raw?: string;
  autoDismissMs?: number;
  kind?: EntryKind;
}

interface ErrorDialogState {
  queue: ErrorEntry[];
  current: ErrorEntry | null;
  /** 通用入口,kind 默认 'error'(向后兼容旧 show(title, detail)) */
  show(title: string, detail?: string, raw?: string, autoDismissMs?: number, kind?: EntryKind): void;
  dismiss(): void;
  clear(): void;
}

let _id = 0;
const nextId = () => `err-${Date.now().toString(36)}-${(_id += 1).toString(36)}`;

export const useErrorDialog = create<ErrorDialogState>((set, get) => ({
  queue: [],
  current: null,

  show(title, detail, raw, autoDismissMs, kind = 'error') {
    const entry: ErrorEntry = {
      id: nextId(),
      kind,
      title,
      ...(detail !== undefined ? { detail } : {}),
      ...(raw !== undefined ? { raw } : {}),
      ...(autoDismissMs !== undefined ? { autoDismissMs } : {}),
    };
    const { current } = get();
    if (current) {
      set((s) => ({ queue: [...s.queue, entry] }));
    } else {
      set({ current: entry });
    }
  },

  dismiss() {
    const { queue } = get();
    if (queue.length > 0) {
      const [next, ...rest] = queue;
      set({ current: next ?? null, queue: rest });
    } else {
      set({ current: null });
    }
  },

  clear() {
    set({ queue: [], current: null });
  },
}));

/** 快捷:把 unknown 错误转成可读 (title, detail) 二元组,kind=error */
export function reportError(err: unknown, fallbackTitle = '出错了'): void {
  const { show } = useErrorDialog.getState();
  if (err instanceof Error) {
    show(err.name || fallbackTitle, err.message, err.stack, undefined, 'error');
    return;
  }
  if (typeof err === 'string') {
    show(fallbackTitle, err, undefined, undefined, 'error');
    return;
  }
  if (err && typeof err === 'object') {
    const o = err as { detail?: string; message?: string; msg?: string };
    show(
      fallbackTitle,
      o.detail ?? o.message ?? o.msg ?? JSON.stringify(err).slice(0, 500),
      undefined,
      undefined,
      'error',
    );
    return;
  }
  show(fallbackTitle, String(err), undefined, undefined, 'error');
}

/** 快捷:展示一条信息提示(蓝),默认 2.5s 自动关 */
export function notifyInfo(title: string, detail?: string, autoDismissMs = 2500): void {
  useErrorDialog.getState().show(title, detail, undefined, autoDismissMs, 'info');
}

/** 快捷:展示一条成功提示(绿),默认 2s 自动关 */
export function notifySuccess(title: string, detail?: string, autoDismissMs = 2000): void {
  useErrorDialog.getState().show(title, detail, undefined, autoDismissMs, 'success');
}
