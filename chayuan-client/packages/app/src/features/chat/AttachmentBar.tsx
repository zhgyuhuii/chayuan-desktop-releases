import * as React from 'react';
import { X, FileText, ImageIcon, Loader2, AlertCircle, Music } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import type { AttachmentDraft } from '../../store/composer';

export interface AttachmentBarProps {
  items: AttachmentDraft[];
  onRemove(id: string): void;
}

/**
 * Composer 顶部的附件预览条。
 * - 图片显示缩略图；其他文件显示文件图标 + 文件名 + 大小。
 * - 上传中转圈、失败红色 + 错误信息。
 */
export const AttachmentBar: React.FC<AttachmentBarProps> = ({ items, onRemove }) => {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-2 border-b px-3 py-2">
      {items.map((a) => (
        <div
          key={a.id}
          className={cn(
            'group relative flex items-center gap-2 rounded-md border bg-background px-2 py-1 text-xs',
            a.status === 'error' && 'border-destructive/60 bg-destructive/10',
          )}
        >
          {a.type === 'image' && a.previewUrl ? (
            <img
              src={a.previewUrl}
              alt={a.name}
              className="h-6 w-6 rounded object-cover"
            />
          ) : a.type === 'image' ? (
            <ImageIcon className="h-4 w-4 text-muted-foreground" />
          ) : a.type === 'audio' ? (
            <Music className="h-4 w-4 text-cyan-600" />
          ) : (
            <FileText className="h-4 w-4 text-muted-foreground" />
          )}

          <div className="flex flex-col">
            <span className="max-w-[14ch] truncate font-medium">{a.name}</span>
            <span className="text-[10px] text-muted-foreground">
              {a.status === 'uploading' && '上传中…'}
              {a.status === 'ready' && formatBytes(a.size)}
              {a.status === 'error' && (a.error ?? '失败')}
              {a.status === 'pending' && '等待…'}
            </span>
            {a.type === 'image' && a.ocrStatus && (
              <span className="text-[10px]">
                {a.ocrStatus === 'pending' && (
                  <span className="text-amber-600">OCR 中…</span>
                )}
                {a.ocrStatus === 'ok' && (
                  <span className="text-emerald-600" title={a.ocrText}>
                    +{(a.ocrText ?? '').length} 字
                  </span>
                )}
                {a.ocrStatus === 'failed' && (
                  <span className="text-red-600" title={a.ocrError}>OCR 失败</span>
                )}
              </span>
            )}
          </div>

          {a.status === 'uploading' && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
          {a.status === 'error' && <AlertCircle className="h-3 w-3 text-destructive" />}

          <Button
            variant="ghost"
            size="icon"
            className="h-5 w-5 opacity-60 group-hover:opacity-100"
            onClick={() => onRemove(a.id)}
            aria-label={`移除 ${a.name}`}
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      ))}
    </div>
  );
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
