/**
 * 预览面板的右侧浮动小工具栏(参考图右下角的 首页/反馈/下载)。
 *
 * 当前包括:刷新 / 下载 / 系统默认应用打开 / 复制链接。
 * 样式:固定在 panel 右下角的悬浮卡片,sm 尺寸下自动隐藏(只留下载)。
 */

import { getPlatform } from '@chayuan/platform-shared';
import { cn } from '@chayuan/ui';
import { Copy, Download, ExternalLink, RefreshCw } from 'lucide-react';
import type * as React from 'react';
import type { PreviewFile } from './types';
import { buildDownloadUrl, buildPreviewUrl } from './useFileUrl';

export const PreviewActionRail: React.FC<{
  file: PreviewFile;
  onRefresh?: () => void;
  className?: string;
}> = ({ file, onRefresh, className }) => {
  const onDownload = async () => {
    const url = await buildDownloadUrl(file);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.fileName;
    a.click();
  };
  const onOpenExternal = async () => {
    const url = await buildPreviewUrl(file);
    try {
      await getPlatform().shell.openExternal(url);
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };
  const onCopyLink = async () => {
    const url = await buildPreviewUrl(file);
    try {
      await getPlatform().clipboard.writeText(url);
    } catch {
      /* ignore */
    }
  };

  return (
    <div
      className={cn(
        'pointer-events-auto absolute right-3 top-1/2 z-[5] flex -translate-y-1/2 flex-col gap-1 rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]/85 p-1.5 shadow-lg backdrop-blur',
        className,
      )}
    >
      {onRefresh && <RailBtn onClick={onRefresh} icon={<RefreshCw />} label="刷新" />}
      <RailBtn onClick={onCopyLink} icon={<Copy />} label="复制链接" />
      <RailBtn onClick={onOpenExternal} icon={<ExternalLink />} label="外部打开" />
      <RailBtn onClick={onDownload} icon={<Download />} label="下载" emphasized />
    </div>
  );
};

const RailBtn: React.FC<{
  onClick(): void;
  icon: React.ReactNode;
  label: string;
  emphasized?: boolean;
}> = ({ onClick, icon, label, emphasized }) => (
  <button
    type="button"
    onClick={onClick}
    title={label}
    aria-label={label}
    className={cn(
      'group/rail inline-flex h-9 w-9 items-center justify-center rounded-xl text-[11px] font-medium transition-colors',
      emphasized
        ? 'bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow hover:brightness-110'
        : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
    )}
  >
    <span className="block h-4 w-4 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
  </button>
);
