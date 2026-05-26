/**
 * useCrossWindowSync —— 主窗口 + 独立窗口之间用 BroadcastChannel 同步关键 query。
 *
 * 用法:
 *   - Chrome / DetachedShell 顶层各挂一次。
 *   - mutation onSuccess 里调 publishCrossWindow({type:'apps-invalidate', ...})。
 *
 * 收到消息后会 queryClient.invalidateQueries(matchingKey),保证两窗 UI 一致。
 *
 * 收的消息只是触发 invalidate,数据仍然走后端真值;不传 payload 也安全。
 */
import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTabsStore } from '../../store/tabs';
import { isDetachedWindow } from './detachedWindow';

const CHANNEL_NAME = 'chayuan';

export type CrossWindowMessage =
  | { type: 'kb-updated'; kbName?: string }
  | { type: 'composer-state'; key: string; value: unknown }
  | { type: 'detach-return'; path: string; title?: string };

let _channel: BroadcastChannel | null = null;
function getChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  if (!_channel) {
    try {
      _channel = new BroadcastChannel(CHANNEL_NAME);
    } catch {
      _channel = null;
    }
  }
  return _channel;
}

export function publishCrossWindow(msg: CrossWindowMessage): void {
  const c = getChannel();
  if (!c) return;
  try {
    c.postMessage(msg);
  } catch {
    /* ignore */
  }
}

export function useCrossWindowSync(): void {
  const queryClient = useQueryClient();
  const open = useTabsStore((s) => s.open);
  React.useEffect(() => {
    const c = getChannel();
    if (!c) return;
    const detached = isDetachedWindow();
    const onMessage = (ev: MessageEvent<CrossWindowMessage>) => {
      const msg = ev.data;
      if (!msg || typeof msg !== 'object') return;
      switch (msg.type) {
        case 'kb-updated':
          void queryClient.invalidateQueries({ queryKey: ['kb'] });
          break;
        case 'detach-return':
          // 主窗收到独立窗"返回主窗"请求 → 在主窗 tabsStore 打开同 path,
          // 独立窗自己负责 window.close()。
          if (!detached) {
            open(msg.path, msg.title ? { title: msg.title } : undefined);
          }
          break;
        default:
          break;
      }
    };
    c.addEventListener('message', onMessage);
    return () => c.removeEventListener('message', onMessage);
  }, [queryClient, open]);
}
