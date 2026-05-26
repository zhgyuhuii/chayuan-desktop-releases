import { Button } from '@chayuan/ui';
import { AlertTriangle, Download, RefreshCw } from 'lucide-react';
import type * as React from 'react';

export const ErrorRenderer: React.FC<{
  error?: Error | null;
  onRetry?: () => void;
  onDownload?: () => void;
}> = ({ error, onRetry, onDownload }) => (
  <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 text-amber-600">
      <AlertTriangle className="h-6 w-6" />
    </div>
    <p className="text-sm font-medium text-[var(--cy-text-primary)]">预览失败</p>
    <p className="max-w-sm text-xs text-[var(--cy-text-secondary)]">
      {error?.message || '未知错误,可尝试下载后用本地软件打开。'}
    </p>
    <div className="mt-2 flex gap-2">
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> 重试
        </Button>
      )}
      {onDownload && (
        <Button size="sm" onClick={onDownload}>
          <Download className="mr-1.5 h-3.5 w-3.5" /> 下载
        </Button>
      )}
    </div>
  </div>
);
