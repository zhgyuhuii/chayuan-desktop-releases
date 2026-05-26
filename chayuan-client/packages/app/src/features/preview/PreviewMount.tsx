/**
 * 顶层挂载点。Shell 渲染一次,内部按 store.current 决定是否真正渲染面板。
 *
 * 标准模式:监听 store.current,有则 portal 到 document.body 渲染 PreviewPanel。
 * 独立子窗模式:由 PreviewStandaloneRoute 直接挂 <PreviewPanel standalone /> 接管。
 */

import * as React from 'react';
import { createPortal } from 'react-dom';
import { PreviewPanel } from './PreviewPanel';
import { onPreviewMessage, postPreview } from './broadcast';
import { usePreviewStore } from './previewStore';

export const PreviewMount: React.FC = () => {
  const current = usePreviewStore((s) => s.current);
  const standalone = usePreviewStore((s) => s.standalone);
  /**
   * 当下是否有页面内嵌 host(KbDetailPage 之类)在用预览面板。
   * >0 时全局抽屉退让 — 否则同一个文件会被全局抽屉 + 内嵌面板各渲一份,
   * 双 iframe / 视频 / DOM 既费资源又有 bug(两个声音轨,两个视频时间线)。
   */
  const inlineOwners = usePreviewStore((s) => s.inlineOwners);

  // 监听 ESC 关闭(只在主窗 dock/float 时;子窗用窗口关闭)
  React.useEffect(() => {
    if (standalone) return;
    const close = usePreviewStore.getState().close;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && usePreviewStore.getState().current) {
        close();
        e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [standalone]);

  // 跨窗:子窗主动关闭 → 通知主窗
  React.useEffect(() => {
    if (!standalone) return;
    const onUnload = () => postPreview({ type: 'closed' });
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, [standalone]);

  // 主窗在子窗收到 'open-here' 时不做事;占位
  React.useEffect(() => {
    return onPreviewMessage(() => undefined);
  }, []);

  if (!current && !standalone) return null;
  if (typeof document === 'undefined') return null;
  // 有内嵌宿主在 → 全局抽屉不渲(ESC 关闭逻辑仍生效,共享同一个 store.close)
  if (inlineOwners > 0 && !standalone) return null;
  return createPortal(<PreviewPanel standalone={standalone} />, document.body);
};
