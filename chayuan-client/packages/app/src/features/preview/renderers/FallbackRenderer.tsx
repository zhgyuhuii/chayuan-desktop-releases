/**
 * 不支持的格式 → 给一个友好的"下载/外部打开"兜底,而不是空白屏。
 *
 * 触达场景:.doc(老二进制 Word)、.ppt、.wps/.et/.dps、不在白名单里的扩展。
 */

import { getPlatform } from '@chayuan/platform-shared';
import { Button } from '@chayuan/ui';
import { Download, ExternalLink, FileQuestion } from 'lucide-react';
import type * as React from 'react';
import { extLabel, humanSize } from '../detectKind';
import type { RendererProps } from '../types';
import { buildDownloadUrl } from '../useFileUrl';

export const FallbackRenderer: React.FC<RendererProps> = ({ file, url }) => {
  const onDownload = async () => {
    const dl = await buildDownloadUrl(file);
    const a = document.createElement('a');
    a.href = dl;
    a.download = file.fileName;
    a.click();
  };
  const onOpenExternal = async () => {
    try {
      await getPlatform().shell.openExternal(url);
    } catch {
      window.open(url, '_blank');
    }
  };
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-slate-100 to-slate-200 text-slate-500 shadow-sm">
        <FileQuestion className="h-8 w-8" />
        <span className="absolute -bottom-1 -right-1 rounded-md bg-slate-900 px-1.5 py-0.5 text-[10px] font-bold text-white shadow">
          {extLabel(file)}
        </span>
      </div>
      <p className="text-sm font-medium text-[var(--cy-text-primary)]">{file.fileName}</p>
      <p className="max-w-sm text-xs text-[var(--cy-text-secondary)]">
        当前格式暂不支持在线预览。可以下载到本地或用系统默认应用打开。
      </p>
      <p className="text-[11px] text-[var(--cy-text-tertiary)]">{humanSize(file.fileSize)}</p>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="outline" onClick={onOpenExternal}>
          <ExternalLink className="mr-1.5 h-3.5 w-3.5" /> 系统默认打开
        </Button>
        <Button size="sm" onClick={onDownload}>
          <Download className="mr-1.5 h-3.5 w-3.5" /> 下载
        </Button>
      </div>
    </div>
  );
};
