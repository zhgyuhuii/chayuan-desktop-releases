/**
 * 独立窗口路由 — 解析 URL ?source=&kb=&file= 后注入 store,再渲染 PreviewPanel。
 *
 * - 子窗与主窗共享 localStorage(同 origin),token 自动可用
 * - 子窗自己的 zustand store 实例独立(不会和主窗 store 冲突)
 * - 关闭时通过 BroadcastChannel 通知主窗清 mode=window 状态
 */

import * as React from 'react';
import { PreviewPanel } from '../PreviewPanel';
import { usePreviewStore } from '../previewStore';
import type { PreviewFile } from '../types';

export const PreviewStandaloneRoute: React.FC = () => {
  const setStandalone = usePreviewStore((s) => s.setStandalone);
  const open = usePreviewStore((s) => s.open);

  React.useEffect(() => {
    setStandalone(true);
    const file = parseUrl();
    if (file) open(file, { mode: 'dock', resetHistory: true });
  }, [setStandalone, open]);

  return (
    <div className="h-screen w-screen bg-[var(--cy-surface-base)]">
      <PreviewPanel standalone />
    </div>
  );
};

function parseUrl(): PreviewFile | null {
  if (typeof window === 'undefined') return null;
  const q = new URLSearchParams(window.location.search);
  const source = q.get('source');
  const fileName = q.get('file') ?? q.get('name') ?? '';
  if (!fileName) return null;
  if (source === 'kb-doc') {
    const kb = q.get('kb');
    if (!kb) return null;
    return { source: 'kb-doc', kbName: kb, fileName };
  }
  if (source === 'direct') {
    const url = q.get('url');
    if (!url) return null;
    return { source: 'direct', url, fileName };
  }
  return null;
}
