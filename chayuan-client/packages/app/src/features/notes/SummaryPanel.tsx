/**
 * AI 笔记摘要面板 — 编辑器右侧滑出/固定 360 宽栏目。
 *
 * 渲染 markdown 文本(简单)— 用换行 + h2 + ul/li 朴素 CSS,不引第三方 markdown
 * 库省 bundle。如果以后要支持复杂 markdown(代码块、表格)再升 react-markdown。
 *
 * Loading 态:spinner + "AI 正在生成…",按钮藏起来。
 * Error 态:红色提示 + 重新生成按钮。
 * Empty 态:占位 + 「生成摘要」按钮(同 header 按钮等价)。
 */
import * as React from 'react';
import { Loader2, RefreshCw, Sparkles, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';

export interface SummaryPanelProps {
  open: boolean;
  summary: string;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRegenerate: () => void;
}

/** 朴素 markdown 渲染:仅认识 ## 标题 + - 项目符号 + 普通段落。 */
const renderMarkdown = (md: string): React.ReactNode => {
  if (!md) return null;
  const blocks: React.ReactNode[] = [];
  const lines = md.split(/\r?\n/);
  let bulletGroup: string[] = [];
  const flushBullets = () => {
    if (bulletGroup.length === 0) return;
    blocks.push(
      <ul key={blocks.length} className="list-disc space-y-1 pl-5 text-sm text-[var(--cy-text-primary)]">
        {bulletGroup.map((b, i) => (
          <li key={i}>{b}</li>
        ))}
      </ul>,
    );
    bulletGroup = [];
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushBullets();
      continue;
    }
    if (line.startsWith('## ')) {
      flushBullets();
      blocks.push(
        <h3 key={blocks.length} className="mt-3 text-sm font-semibold text-[var(--cy-text-primary)]">
          {line.slice(3).trim()}
        </h3>,
      );
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      bulletGroup.push(line.slice(2).trim());
    } else {
      flushBullets();
      blocks.push(
        <p key={blocks.length} className="text-sm leading-relaxed text-[var(--cy-text-primary)]">
          {line}
        </p>,
      );
    }
  }
  flushBullets();
  return blocks;
};

export const SummaryPanel: React.FC<SummaryPanelProps> = ({
  open, summary, loading, error, onClose, onRegenerate,
}) => {
  if (!open) return null;
  return (
    <aside
      className={cn(
        'flex w-[360px] shrink-0 flex-col border-l border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/50',
      )}
      aria-label="AI 摘要面板"
    >
      <header className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] px-4 py-2">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[var(--cy-brand-600)]" />
          <h2 className="text-sm font-semibold text-[var(--cy-text-primary)]">AI 摘要 & 要点</h2>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onRegenerate}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)] disabled:opacity-50"
            title="重新让 AI 生成摘要(覆盖现有)"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <RefreshCw className="h-3.5 w-3.5" />}
            重新生成
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            title="关闭面板(已生成的摘要会保留在草稿里,下次打开自动恢复)"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {loading && !summary && (
          <div className="flex flex-col items-center justify-center py-12 text-sm text-[var(--cy-text-tertiary)]">
            <Loader2 className="mb-3 h-6 w-6 animate-spin text-[var(--cy-brand-500)]" />
            AI 正在生成摘要…
            <span className="mt-1 text-xs">通常 ≤ 10 秒</span>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-rose-500/30 bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-950/30 dark:text-rose-200">
            <div className="mb-2 font-medium">生成失败</div>
            <div className="font-mono whitespace-pre-wrap break-words">{error}</div>
            <Button
              size="sm"
              variant="outline"
              onClick={onRegenerate}
              className="mt-2 h-7 text-xs"
              disabled={loading}
            >
              <RefreshCw className="h-3 w-3" /> 重试
            </Button>
          </div>
        )}

        {!loading && !error && !summary && (
          <div className="rounded-md border border-dashed border-[var(--cy-border-subtle)] p-4 text-center text-xs text-[var(--cy-text-tertiary)]">
            还没生成摘要。点右上角「重新生成」或编辑器顶部的「AI 摘要」按钮开始。
          </div>
        )}

        {summary && !error && (
          <div className="space-y-1">
            {renderMarkdown(summary)}
            {loading && (
              <div className="mt-3 inline-flex items-center gap-1.5 text-xs text-[var(--cy-text-tertiary)]">
                <Loader2 className="h-3 w-3 animate-spin" /> 重新生成中…
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="border-t border-[var(--cy-border-subtle)] px-4 py-2 text-[10px] text-[var(--cy-text-tertiary)]">
        摘要会随笔记草稿一起保存,下次打开自动加载。保存到知识中心时同步写入文档头部。
      </footer>
    </aside>
  );
};
