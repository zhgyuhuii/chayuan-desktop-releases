/**
 * KbResultBlock — 详情页"试问"返回结果的展示。
 *
 * 内部完全委托给 <KbResultsView>(单 source、扁平、隐藏顶部折叠条),
 * 与 chat 引用面板、KbBoard 跨 KB 提问保持视觉与交互一致。
 *
 * 状态:
 *   - empty:无 block(还没问)→ 不渲染
 *   - error:block.error 非空或 block.ok=false → ErrorBlock + 重试按钮
 *   - data:走 KbResultsView,默认展开第一组 group
 */

import * as React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { askBlocksToSources, type AskBlock } from '@chayuan/api';
import { KbResultsView } from '../components/KbResultsView';

export interface KbResultBlockProps {
  block: AskBlock | null;
  /** 提交的查询(供失败/空态文案显示) */
  query?: string;
  /** 失败时点击重试 */
  onRetry?(): void;
  className?: string;
}

export const KbResultBlock: React.FC<KbResultBlockProps> = ({ block, query, onRetry, className }) => {
  const sources = React.useMemo(() => (block ? askBlocksToSources([block]) : []), [block]);
  if (!block) return null;
  if (!block.ok || block.error) {
    return (
      <ErrorBlock
        message={block.error ?? '未知错误'}
        query={query}
        onRetry={onRetry}
        className={className}
      />
    );
  }
  return (
    <KbResultsView
      sources={sources}
      hideTopFold
      flattenSingleSource
      defaultOpen
      className={className}
      emptyHint={<EmptyHint text="没有命中,试着换个问法或扩大检索范围。" />}
    />
  );
};

// ──────────────────────────────────────────────────────────────
// Empty / Error 仅这两个轻态保留在本地;主要展示走 KbResultsView。
// ──────────────────────────────────────────────────────────────

const EmptyHint: React.FC<{ text: string; className?: string }> = ({ text, className }) => (
  <div className={cn('rounded-lg border border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-3 text-xs text-[var(--cy-text-secondary)]', className)}>
    {text}
  </div>
);

const ErrorBlock: React.FC<{ message: string; query?: string; onRetry?(): void; className?: string }> = ({ message, query, onRetry, className }) => (
  <div className={cn('flex items-start gap-2 rounded-lg border border-red-200 bg-red-50/60 px-3 py-2.5 text-xs text-red-700 dark:border-red-500/40 dark:bg-red-950/30 dark:text-red-200', className)}>
    <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
    <div className="min-w-0 flex-1">
      <p className="font-medium">查询失败</p>
      {query && <p className="mt-0.5 line-clamp-1 text-red-600/80">问题：{query}</p>}
      <p className="mt-1 break-words font-mono text-[10.5px] leading-relaxed">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1.5 inline-flex items-center gap-1 rounded border border-red-300 bg-white px-2 py-0.5 text-[11px] text-red-700 hover:bg-red-50 dark:border-red-500/40 dark:bg-red-950/40 dark:text-red-200"
        >
          <RefreshCw className="h-3 w-3" /> 重试
        </button>
      )}
    </div>
  </div>
);
