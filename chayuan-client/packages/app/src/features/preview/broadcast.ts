/**
 * 主窗 ↔ 独立预览子窗的事件桥(BroadcastChannel)。
 *
 * 同 origin 的所有 tab/popup/Tauri WebviewWindow 共享 channel,
 * 不需要做对端发现/连接维护;消息按 type 分发,broadcast 自带广播语义。
 */

const CHANNEL = 'cy.preview';

export type PreviewBroadcastMessage =
  | { type: 'closed'; label?: string }
  | { type: 'redock'; label?: string }
  | { type: 'open-here'; label: string }
  | { type: 'ping' };

let chRef: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
  if (chRef) return chRef;
  if (typeof BroadcastChannel === 'undefined') return null;
  try {
    chRef = new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
  return chRef;
}

export function postPreview(msg: PreviewBroadcastMessage): void {
  const ch = getChannel();
  if (ch) ch.postMessage(msg);
}

export function onPreviewMessage(cb: (msg: PreviewBroadcastMessage) => void): () => void {
  const ch = getChannel();
  if (!ch) return () => undefined;
  const handler = (ev: MessageEvent<PreviewBroadcastMessage>) => cb(ev.data);
  ch.addEventListener('message', handler);
  return () => ch.removeEventListener('message', handler);
}
