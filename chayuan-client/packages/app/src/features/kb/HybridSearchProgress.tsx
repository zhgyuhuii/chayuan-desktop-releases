/**
 * HybridSearchProgress —— 折叠式检索进度卡片(对应 Block B 反馈 16)。
 *
 * 在对话回答里嵌入展示:
 *   ▼ 🔍 检索中…  (折叠;展开后看每路状态)
 *      ✓ 向量召回 (18 条, 240ms)
 *      ✓ BM25     (12 条, 80ms)
 *      ✓ 标题命中 (3 条, 30ms)
 *      ✓ 章节定位 (5 条, 40ms)
 *      ◯ 融合 24 → Rerank → top 6 (600ms)
 *
 *   📚 总结:从《限购政策 2024》§3 找到 6 条相关内容…
 *
 *   📄 出处:[1] xxx.pdf · §3 第 12 页 (0.86) [预览]
 *
 * 用法:外层订阅 SSE,把帧推到本组件 props.frames(数组追加即可)。
 */
import * as React from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  FileText,
  Sparkles,
} from 'lucide-react';
import { cn } from '@chayuan/ui';
import type { HybridSearchChunk, HybridSearchFrame } from '@chayuan/api';

const ROUTE_LABEL: Record<string, string> = {
  vector: '语义检索',
  bm25: '关键词检索',
  title: '标题命中',
  section: '章节定位',
};

interface RouteState {
  status: 'pending' | 'running' | 'done';
  count?: number;
  duration_ms?: number;
}

export interface HybridSearchProgressProps {
  frames: HybridSearchFrame[];
  /** 用户点击"预览"时的回调;承载文档预览 */
  onPreviewChunk?: (chunk: HybridSearchChunk) => void;
  /** 默认折叠显示总结;false 默认展开 */
  defaultCollapsed?: boolean;
}

export const HybridSearchProgress: React.FC<HybridSearchProgressProps> = ({
  frames,
  onPreviewChunk,
  defaultCollapsed = true,
}) => {
  const [expanded, setExpanded] = React.useState(!defaultCollapsed);

  // 解析 frames 成视图状态
  const view = React.useMemo(() => {
    const routes: Record<string, RouteState> = {
      vector: { status: 'pending' },
      bm25: { status: 'pending' },
      title: { status: 'pending' },
      section: { status: 'pending' },
    };
    let fuseTotal: number | undefined;
    let rerankDuration: number | undefined;
    let rerankTopK: number | undefined;
    let summary = '';
    let chunks: HybridSearchChunk[] = [];
    let hyde: string | undefined;
    let isDone = false;
    let isError: string | undefined;
    for (const f of frames) {
      switch (f.type) {
        case 'route_start': {
          const r = routes[f.route];
          if (r) r.status = 'running';
          break;
        }
        case 'route_done':
          if (routes[f.route]) {
            routes[f.route] = {
              status: 'done',
              count: f.count,
              duration_ms: f.duration_ms,
            };
          }
          break;
        case 'fuse':
          fuseTotal = f.total_unique;
          break;
        case 'rerank':
          rerankDuration = f.duration_ms;
          rerankTopK = f.top_k;
          break;
        case 'summary':
          summary = f.summary;
          break;
        case 'results':
          chunks = f.chunks;
          break;
        case 'hyde':
          hyde = f.rewritten;
          break;
        case 'done':
          isDone = true;
          break;
        case 'error':
          isError = f.message;
          break;
        default:
          break;
      }
    }
    return { routes, fuseTotal, rerankDuration, rerankTopK, summary, chunks, hyde, isDone, isError };
  }, [frames]);

  return (
    <div className="my-2 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
      {/* 标题行 */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        <Sparkles className="h-3.5 w-3.5 text-[var(--cy-brand-500)]" />
        <span>
          {view.isError
            ? '检索失败'
            : view.isDone
              ? `检索完成 · ${view.chunks.length} 条出处`
              : '检索中…'}
        </span>
      </button>

      {/* 总结 + 出处(始终显示) */}
      {view.summary && (
        <div className="border-t border-[var(--cy-border-subtle)] px-3 py-2 text-xs text-[var(--cy-text-primary)]">
          📚 {view.summary}
        </div>
      )}

      {/* 展开:每路状态 */}
      {expanded && (
        <div className="border-t border-[var(--cy-border-subtle)] px-3 py-2 text-xs">
          {view.hyde && (
            <p className="mb-2 text-[var(--cy-text-tertiary)]">
              <strong>问题改写:</strong> {view.hyde}
            </p>
          )}
          <ul className="space-y-1">
            {Object.entries(view.routes).map(([route, st]) => (
              <li key={route} className="flex items-center gap-2">
                {st.status === 'done' ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                ) : (
                  <CircleDashed
                    className={cn(
                      'h-3.5 w-3.5',
                      st.status === 'running'
                        ? 'animate-spin text-[var(--cy-brand-500)]'
                        : 'text-[var(--cy-text-tertiary)]',
                    )}
                  />
                )}
                <span className="text-[var(--cy-text-secondary)]">
                  {ROUTE_LABEL[route] ?? route}
                </span>
                {st.status === 'done' && (
                  <span className="text-[var(--cy-text-tertiary)]">
                    ({st.count} 条, {st.duration_ms}ms)
                  </span>
                )}
              </li>
            ))}
            {view.fuseTotal !== undefined && (
              <li className="flex items-center gap-2">
                {view.isDone ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                ) : (
                  <CircleDashed className="h-3.5 w-3.5 animate-spin text-[var(--cy-brand-500)]" />
                )}
                <span className="text-[var(--cy-text-secondary)]">
                  融合 {view.fuseTotal} 条
                  {view.rerankTopK !== undefined && (
                    <> → 重排后保留前 {view.rerankTopK} 条({view.rerankDuration}ms)</>
                  )}
                </span>
              </li>
            )}
          </ul>
          {view.isError && (
            <p className="mt-2 text-destructive">检索错误:{view.isError}</p>
          )}
        </div>
      )}

      {/* 出处列表 */}
      {view.chunks.length > 0 && (
        <ol className="border-t border-[var(--cy-border-subtle)] px-3 py-2 text-xs">
          <p className="mb-1 font-medium text-[var(--cy-text-primary)]">📄 出处</p>
          {view.chunks.map((c, i) => (
            <li key={c.id ?? i} className="my-0.5 flex items-center gap-2">
              <span className="text-[var(--cy-text-tertiary)]">[{i + 1}]</span>
              <FileText className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />
              <span className="truncate text-[var(--cy-text-primary)]">
                {c.file_name || c.title || '(无名)'}
              </span>
              {c.section_path && (
                <span className="text-[var(--cy-text-tertiary)]">
                  · §{Array.isArray(c.section_path) ? c.section_path.join(' / ') : c.section_path}
                </span>
              )}
              {c.page !== undefined && (
                <span className="text-[var(--cy-text-tertiary)]">第 {c.page} 页</span>
              )}
              {(c.rerank_score ?? c.route_score) !== undefined && (
                <span className="text-[var(--cy-text-tertiary)]">
                  ({(c.rerank_score ?? c.route_score)?.toFixed(2)})
                </span>
              )}
              {onPreviewChunk && (
                <button
                  type="button"
                  onClick={() => onPreviewChunk(c)}
                  className="ml-auto text-[var(--cy-brand-600)] hover:underline"
                >
                  预览
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};
