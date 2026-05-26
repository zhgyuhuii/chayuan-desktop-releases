/**
 * 预览面板顶栏 — 文件名 / 模式切换 / 弹窗 / 关闭 / 刷新。
 *
 * 设计:
 *   - 左:返回(history)/ 文件 icon + 名称
 *   - 中:空白(拖拽区,float 模式下能拖动整个面板)
 *   - 右:模式按钮 dock <-> float、折叠、弹窗、关闭
 */

import { getPlatform } from '@chayuan/platform-shared';
import { cn } from '@chayuan/ui';
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Maximize2,
  PanelRightClose,
  Pin,
  PinOff,
  X,
} from 'lucide-react';
import type * as React from 'react';
import { postPreview } from './broadcast';
import { extLabel } from './detectKind';
import { usePreviewStore } from './previewStore';
import { previewFileKey } from './types';
import type { PreviewFile } from './types';

export interface PreviewToolbarProps {
  file: PreviewFile;
  /** 拖拽区:float 模式下抓握后整体平移 */
  onDragStart?: (e: React.MouseEvent) => void;
  /** standalone 模式下不展示 dock/float 切换 */
  standalone?: boolean;
  /**
   * 内嵌模式:也不展示 dock/float/折叠按钮(模式切换在内嵌中无意义),
   * 但保留独立窗口按钮 — 用户可以从内嵌"弹"成独立 OS 窗口。
   */
  embedded?: boolean;
}

export const PreviewToolbar: React.FC<PreviewToolbarProps> = ({
  file,
  onDragStart,
  standalone,
  embedded,
}) => {
  const mode = usePreviewStore((s) => s.mode);
  const setMode = usePreviewStore((s) => s.setMode);
  const close = usePreviewStore((s) => s.close);
  const toggleCollapse = usePreviewStore((s) => s.toggleCollapse);
  const collapsed = usePreviewStore((s) => s.collapsed);
  const history = usePreviewStore((s) => s.history);
  const cursor = usePreviewStore((s) => s.cursor);
  const go = usePreviewStore((s) => s.go);

  const onPopOut = async () => {
    const platform = getPlatform();
    if (!platform.preview) {
      // 退化到内部 float
      setMode('float');
      return;
    }
    try {
      const params = new URLSearchParams();
      if (file.source === 'kb-doc') {
        params.set('source', 'kb-doc');
        params.set('kb', file.kbName);
        params.set('file', file.fileName);
      } else {
        params.set('source', 'direct');
        params.set('url', file.url);
        params.set('name', file.fileName);
      }
      const label = `preview-${hash(previewFileKey(file))}`;
      await platform.preview.openWindow({
        path: `/preview-window?${params.toString()}`,
        title: file.fileName,
        label,
        width: 980,
        height: 720,
      });
      // 弹出后主窗放掉 current,但保留模式偏好
      setMode('window');
      close();
      postPreview({ type: 'open-here', label });
    } catch (e) {
      console.warn('[preview] 弹出独立窗口失败,回退 float:', e);
      setMode('float');
    }
  };

  const onClose = () => {
    if (standalone) {
      // 关掉自身窗口
      void getPlatform().window.close?.();
      return;
    }
    close();
  };

  const canBack = cursor > 0;
  const canFwd = cursor >= 0 && cursor < history.length - 1;

  return (
    <div
      className={cn(
        'group/toolbar relative flex h-11 flex-shrink-0 items-center gap-1.5 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/80 pl-2 pr-1 backdrop-blur',
        mode === 'float' && 'cursor-grab active:cursor-grabbing',
      )}
      onMouseDown={mode === 'float' ? onDragStart : undefined}
    >
      {/* history nav */}
      <button
        type="button"
        disabled={!canBack}
        onClick={() => go(-1)}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] disabled:opacity-30 hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        title="上一个"
      >
        <ArrowLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        disabled={!canFwd}
        onClick={() => go(1)}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] disabled:opacity-30 hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        title="下一个"
      >
        <ArrowRight className="h-4 w-4" />
      </button>

      {/* ext + filename */}
      <span className="ml-1 inline-flex h-5 items-center justify-center rounded bg-gradient-to-br from-indigo-500 to-violet-600 px-1.5 text-[9px] font-bold text-white shadow-sm">
        {extLabel(file)}
      </span>
      <span
        className="min-w-0 flex-1 select-none truncate text-[13px] font-medium text-[var(--cy-text-primary)]"
        title={file.fileName}
      >
        {file.fileName}
      </span>

      {/* modes — 内嵌模式只保留"独立窗口";standalone 全隐 */}
      {!standalone && (
        <>
          <Sep />
          {!embedded && (
            <>
              <ToolBtn active={mode === 'dock'} onClick={() => setMode('dock')} title="停靠">
                <Pin className="h-3.5 w-3.5" />
              </ToolBtn>
              <ToolBtn active={mode === 'float'} onClick={() => setMode('float')} title="漂浮">
                <PinOff className="h-3.5 w-3.5" />
              </ToolBtn>
              <ToolBtn onClick={toggleCollapse} title={collapsed ? '展开' : '折叠'}>
                {collapsed ? (
                  <Maximize2 className="h-3.5 w-3.5" />
                ) : (
                  <PanelRightClose className="h-3.5 w-3.5" />
                )}
              </ToolBtn>
            </>
          )}
          <ToolBtn onClick={onPopOut} title="独立窗口">
            <ExternalLink className="h-3.5 w-3.5" />
          </ToolBtn>
        </>
      )}
      <Sep />
      <ToolBtn onClick={onClose} title="关闭" intent="close">
        <X className="h-4 w-4" />
      </ToolBtn>
    </div>
  );
};

const ToolBtn: React.FC<{
  active?: boolean;
  intent?: 'close';
  onClick(): void;
  title: string;
  children: React.ReactNode;
}> = ({ active, intent, onClick, title, children }) => (
  <button
    type="button"
    onClick={onClick}
    title={title}
    aria-label={title}
    className={cn(
      'inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors',
      active
        ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
        : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      intent === 'close' && 'hover:bg-red-50 hover:text-red-600',
    )}
  >
    {children}
  </button>
);

const Sep: React.FC = () => <span className="mx-0.5 h-4 w-px bg-[var(--cy-border-subtle)]" />;

function hash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36);
}
