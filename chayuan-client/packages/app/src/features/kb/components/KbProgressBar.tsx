/**
 * 卡片底部进度条 — 聚合一个 KB 的全部上传 jobs。
 *
 * 形态:
 *   - 2px 高的横线 + brand 渐变,百分比按字节加权
 *   - 上方一行小字:`X/Y 文件 · 35%` / `失败 1`(若有)
 *   - 全部完成后 800ms 自动淡出 + 清状态(由调用方调 clearFinishedFor)
 *
 * 性能:订阅一次 selectKuSummary;同 KB 内文件进度变更不会触发其他卡片重渲。
 */

import * as React from 'react';
import { AlertCircle, Check, Loader2, X as XIcon } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { selectKuSummary, useKbUploadStore } from '../upload/kbUploadStore';

export interface KbProgressBarProps {
  kuId: string;
  /** 全完成后是否自动淡出并清状态;默认 true */
  autoClear?: boolean;
}

export const KbProgressBar: React.FC<KbProgressBarProps> = ({ kuId, autoClear = true }) => {
  const summary = useKbUploadStore(selectKuSummary(kuId));
  const clear = useKbUploadStore((s) => s.clearFinishedFor);
  const cancel = useKbUploadStore((s) => s.cancel);
  const [hidden, setHidden] = React.useState(false);

  // 全部完成 / 全部清后 → 淡出
  React.useEffect(() => {
    if (!autoClear) return;
    if (summary.total === 0) return;
    if (summary.active) { setHidden(false); return; }
    // 全部已结束(ok / error / cancelled)→ 800ms 后清
    const timer = window.setTimeout(() => {
      clear(kuId);
      setHidden(false);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [autoClear, summary.active, summary.total, kuId, clear]);

  if (summary.total === 0 || hidden) return null;
  const pct = Math.round(summary.ratio * 100);

  return (
    <div className="absolute inset-x-0 bottom-0 z-10 flex flex-col gap-0.5 bg-gradient-to-t from-white/95 to-white/0 px-3 pb-1.5 pt-3 transition-opacity">
      <div className="flex items-center justify-between text-[10px]">
        <span className="inline-flex items-center gap-1 text-[var(--cy-text-tertiary)]">
          {summary.active ? (
            <Loader2 className="h-3 w-3 animate-spin text-[var(--cy-brand-500)]" />
          ) : summary.failed > 0 ? (
            <AlertCircle className="h-3 w-3 text-red-500" />
          ) : (
            <Check className="h-3 w-3 text-emerald-500" />
          )}
          <span>
            {summary.done}/{summary.total}
            {summary.failed > 0 && (
              <span className="ml-1 text-red-600">· 失败 {summary.failed}</span>
            )}
          </span>
        </span>
        <span className="font-mono text-[var(--cy-text-tertiary)]">{pct}%</span>
      </div>
      <div className="relative h-1 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
        <div
          className={cn(
            'absolute inset-y-0 left-0 rounded-full transition-[width] duration-200',
            summary.failed > 0 && !summary.active
              ? 'bg-gradient-to-r from-red-400 to-red-500'
              : 'bg-gradient-to-r from-[var(--cy-brand-400)] to-[var(--cy-brand-600)]',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      {/* 正在跑的文件名 chip(只显示第一个 uploading 的) */}
      {summary.active && (
        <div className="flex items-center gap-1 truncate text-[10px] text-[var(--cy-text-tertiary)]">
          <span className="truncate">
            {summary.jobs.find((j) => j.status === 'uploading')?.fileName ?? '准备中…'}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              for (const j of summary.jobs) if (j.status === 'queued' || j.status === 'uploading') cancel(j.jobId);
            }}
            className="ml-auto inline-flex h-3.5 w-3.5 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            aria-label="取消上传"
            title="取消"
          >
            <XIcon className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  );
};
