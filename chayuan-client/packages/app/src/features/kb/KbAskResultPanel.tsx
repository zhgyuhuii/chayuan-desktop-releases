/**
 * 跨 KB 提问结果面板。
 *
 * 输入:AskResponse(由 universe.ask 返回 / streaming)
 * 输出:按 kind 分块渲染:
 *   - document → 引用列表 + score + source
 *   - structured → 自由文本(含 SQL),代码块化
 *   - vector → hits 列表
 *   - image → 提示性占位(本期 ask 不做)
 *   - 错误 → 红色块
 *
 * 设计:
 *   - 每块一卡片,顶部显示 kb display_name(由调用方注入) + kind 徽章
 *   - 多个块按选择顺序排列
 *   - loading 状态由调用方控制(此组件无 query)
 */

import * as React from 'react';
import { AlertCircle, Boxes, ChevronDown, Database, FileText, Images, Loader2, MessageSquare, Sparkles, Square, ThumbsDown, ThumbsUp, Wand2 } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { annotationApi, askBlocksToSources, type AskBlock, type AskResponse, type KuItem, type KuKind, type RewriteTrace } from '@chayuan/api';
import { KbResultsView } from './components/KbResultsView';

interface KbAskResultPanelProps {
  query: string;
  result: AskResponse | null;
  itemsByKuId: Map<string, KuItem>;
  loading: boolean;
  onStop?: () => void;
  /** 本次提问选中的 ku_ids;loading 时按这个列表逐个渲染骨架,响应回来后按 result 顺序替换 */
  selectedKuIds?: string[];
  /** 综合总结状态(deepseek/千问 风格);text 是 LLM 写的最终答案,pending 表示 LLM 正在写 */
  summary?: { text: string; pending: boolean; error?: string; evidenceCount?: number };
}

const KIND_META: Record<KuKind, { icon: React.ComponentType<{ className?: string }>; tint: string; label: string }> = {
  document: { icon: FileText, tint: 'text-sky-600 bg-sky-50', label: '文档' },
  image: { icon: Images, tint: 'text-pink-600 bg-pink-50', label: '图像' },
  structured: { icon: Database, tint: 'text-emerald-600 bg-emerald-50', label: '结构化' },
  vector: { icon: Boxes, tint: 'text-amber-600 bg-amber-50', label: '向量' },
};

export const KbAskResultPanel: React.FC<KbAskResultPanelProps> = ({
  query, result, itemsByKuId, loading, onStop, selectedKuIds = [], summary,
}) => {
  // loading 时:按选中顺序渲染骨架(每个 KB 一行 spinner);
  // 收到 result 后:按 result.results 顺序 stagger 浮入
  const skeletonIds = loading
    ? selectedKuIds.length > 0
      ? selectedKuIds
      : (result?.results ?? []).map((b) => b.ku_id)
    : [];

  const hasBlocks = (result?.results?.length || 0) > 0;
  const showSummary = !!summary && (summary.pending || summary.text || summary.error);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-6 py-6">
      <UserBubble query={query} />

      {/* 顶部检索进度条:仅在还有 KB 在跑时显示;summary 阶段独立 */}
      {loading && (
        <ProgressHeader
          done={result?.results?.length ?? 0}
          total={skeletonIds.length || (result?.results?.length ?? 0)}
          onStop={onStop}
        />
      )}

      {/* 综合总结 — deepseek/千问 风格的最终答案,默认显示在所有源块之上 */}
      {showSummary && (
        <SummaryBubble
          text={summary?.text || ''}
          pending={!!summary?.pending}
          error={summary?.error}
          evidenceCount={summary?.evidenceCount}
        />
      )}

      {/* 来源面板 — 默认折叠,展开后逐项展示 KB → 文件/SQL/向量 */}
      {hasBlocks && (
        <SourcesFold
          blocks={result?.results ?? []}
          itemsByKuId={itemsByKuId}
          query={query}
          loading={loading}
          // 总结尚未到达时(包括 summary 不启用的旧后端),默认展开,保持当前阅读体验
          defaultOpen={!showSummary}
        />
      )}

      {/* 还在等待的块:渲染骨架卡(已返回的 ku_id 跳过) */}
      {loading && skeletonIds.map((kuId, i) => {
        const has = result?.results?.some((b) => b.ku_id === kuId);
        if (has) return null;
        const item = itemsByKuId.get(kuId);
        return (
          <PendingBlock
            key={`pending-${kuId}`}
            kbName={item?.display_name ?? kuId}
            kind={item?.kind ?? 'document'}
            delay={i * 80}
          />
        );
      })}

      <style>{`@keyframes kbBlockIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}`}</style>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// Summary bubble + Sources fold (deepseek/千问 风格的总结 + 折叠源)
// ──────────────────────────────────────────────────────────────

const SummaryBubble: React.FC<{
  text: string;
  pending: boolean;
  error?: string;
  evidenceCount?: number;
}> = ({ text, pending, error, evidenceCount }) => {
  return (
    <div
      className="rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-3 shadow-[var(--cy-shadow-sm)]"
      style={{ animation: 'kbBlockIn .4s ease-out backwards' }}
    >
      <div className="mb-1.5 inline-flex items-center gap-1.5 text-[11px] font-medium text-[var(--cy-brand-700)]">
        <Sparkles className="h-3 w-3" />
        AI 综合答案
        {evidenceCount != null && (
          <span className="rounded-full bg-[var(--cy-brand-50)] px-1.5 py-0.5 text-[10px] font-normal text-[var(--cy-brand-700)]">
            参考 {evidenceCount} 条来源
          </span>
        )}
      </div>
      {pending && !text ? (
        <div className="inline-flex items-center gap-2 text-[12px] text-[var(--cy-text-tertiary)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在综合多个来源生成答案…
        </div>
      ) : error ? (
        <div className="rounded-md bg-amber-50 px-3 py-2 text-[12px] text-amber-700">
          总结失败:{error}。下方仍可查看各来源的原始命中。
        </div>
      ) : text ? (
        <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-[var(--cy-text-primary)]">
          {text}
        </p>
      ) : (
        <div className="text-[12px] text-[var(--cy-text-tertiary)]">未生成总结。</div>
      )}
    </div>
  );
};

const SourcesFold: React.FC<{
  blocks: AskResponse['results'];
  itemsByKuId: Map<string, KuItem>;
  query: string;
  loading: boolean;
  defaultOpen?: boolean;
}> = ({ blocks, itemsByKuId, query, loading, defaultOpen = false }) => {
  const [open, setOpen] = React.useState(defaultOpen);
  React.useEffect(() => { setOpen(defaultOpen); }, [defaultOpen]);
  const total = blocks.length;
  const okCount = blocks.filter((b) => b.ok).length;
  return (
    <div className="border-l-2 border-[var(--cy-brand-300)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-2 py-1 text-left hover:bg-[var(--cy-bg-hover)]"
      >
        <span className="inline-flex min-w-0 flex-1 items-center gap-1.5 text-[11px] text-[var(--cy-text-tertiary)]">
          <span className="font-medium text-[var(--cy-text-secondary)]">知识来源</span>
          <span className="mx-1">·</span>
          已检索 {total} 个,成功 {okCount}
          {loading ? ' · 仍在补充' : ''}
        </span>
        <ChevronDown
          className={cn('h-3.5 w-3.5 flex-shrink-0 text-[var(--cy-text-tertiary)] transition-transform', open && 'rotate-180')}
        />
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-3">
          {blocks.map((block, i) => {
            const item = itemsByKuId.get(block.ku_id);
            const name = item?.display_name ?? block.ku_id;
            return (
              <div
                key={`${block.ku_id}-${i}`}
                style={{ animation: `kbBlockIn .35s ease-out ${i * 60}ms backwards` }}
              >
                <ResultBlock block={block} kbName={name} query={query} retrievalOpen={loading} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// User bubble + Progress
// ──────────────────────────────────────────────────────────────

const UserBubble: React.FC<{ query: string }> = ({ query }) => (
  <div className="flex justify-end">
    <div className="max-w-[80%] rounded-2xl rounded-tr-md bg-[var(--cy-brand-50)] px-4 py-2.5 text-sm text-[var(--cy-text-primary)] shadow-[var(--cy-shadow-sm)]">
      <div className="mb-1 inline-flex items-center gap-1 text-[10px] font-medium uppercase text-[var(--cy-brand-700)]">
        <MessageSquare className="h-3 w-3" /> 你
      </div>
      <p className="whitespace-pre-wrap break-words">{query}</p>
    </div>
  </div>
);

const ProgressHeader: React.FC<{ done: number; total: number; onStop?: () => void }> = ({ done, total, onStop }) => {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-dashed border-[var(--cy-brand-300)] bg-gradient-to-r from-[var(--cy-brand-50)] to-transparent px-4 py-2.5">
      <div className="flex items-center justify-between gap-2 text-xs">
        <div className="inline-flex items-center gap-2 text-[var(--cy-brand-700)]">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--cy-brand-500)] opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[var(--cy-brand-500)]" />
          </span>
          <span className="font-medium">正在并行检索知识库</span>
        </div>
        <div className="inline-flex items-center gap-2">
          <span className="font-mono text-[10px] text-[var(--cy-text-tertiary)]">
            {done}/{total}
          </span>
          {onStop && (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-600 hover:bg-red-100"
              aria-label="停止检索"
              title="停止本次对话"
            >
              <Square className="h-2.5 w-2.5" />
              停止
            </button>
          )}
        </div>
      </div>
      <div className="relative h-1 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-[var(--cy-brand-400)] to-[var(--cy-brand-600)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
};

const PendingBlock: React.FC<{ kbName: string; kind: KuKind; delay: number }> = ({ kbName, kind, delay }) => {
  const meta = KIND_META[kind] ?? KIND_META.document;
  const Icon = meta.icon;
  return (
    <div
      className="rounded-2xl border border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-3"
      style={{ animation: `kbBlockIn .35s ease-out ${delay}ms backwards` }}
    >
      <div className="flex items-center gap-3">
        <span className={cn('flex h-7 w-7 items-center justify-center rounded-md', meta.tint)}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--cy-text-primary)]">{kbName}</span>
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--cy-text-tertiary)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          查询中…
        </span>
      </div>
      {/* 内容骨架 */}
      <div className="mt-2.5 space-y-1.5">
        <div className="h-2.5 w-3/4 animate-pulse rounded-full bg-[var(--cy-surface-2)]" />
        <div className="h-2.5 w-5/6 animate-pulse rounded-full bg-[var(--cy-surface-2)]" />
        <div className="h-2.5 w-2/3 animate-pulse rounded-full bg-[var(--cy-surface-2)]" />
      </div>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 单块
// ──────────────────────────────────────────────────────────────

const ResultBlock: React.FC<{ block: AskBlock; kbName: string; query: string; retrievalOpen: boolean }> = ({
  block, kbName, query,
}) => {
  // 全部 kind 一律走统一组件 — KbResultsView 内部已按 kind 切换文档 / 结构化 / 向量 / 图像渲染。
  // 单 source 模式 + 隐藏顶部条:把 SourceCard 直接嵌进 ResultBlock 的卡片里。
  const sources = React.useMemo(() => {
    const labelByKuId = new Map<string, string>();
    labelByKuId.set(block.ku_id, kbName);
    return askBlocksToSources([block], labelByKuId);
  }, [block, kbName]);

  // document 类型保留改写策略胶囊(RewriteTraceBar 是这条会话特有的诊断 UI)
  const rewrites = block.kind === 'document' ? (block.rewritten_queries ?? []) : [];
  const trace = block.kind === 'document' ? block.rewrite_trace : undefined;

  return (
    <div className="rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] shadow-[var(--cy-shadow-sm)]">
      <div className="px-4 py-3 space-y-2.5">
        {!block.ok && block.error ? (
          <ErrorBody error={block.error} />
        ) : (
          <>
            {rewrites.length > 1 && <RewriteTraceBar queries={rewrites} trace={trace} />}
            <KbResultsView
              sources={sources}
              hideTopFold
              flattenSingleSource={false}
              defaultOpen
            />
          </>
        )}
        <QualityFeedback query={query} block={block} kbName={kbName} />
      </div>
    </div>
  );
};

const ERROR_TAGS = [
  { id: 'irrelevant', label: '召回不相关' },
  { id: 'missing_evidence', label: '缺少依据' },
  { id: 'wrong_answer', label: '答案错误' },
  { id: 'citation_error', label: '引用错误' },
  { id: 'unsafe', label: '风险内容' },
];

const QualityFeedback: React.FC<{ query: string; block: AskBlock; kbName: string }> = ({ query, block, kbName }) => {
  const [quality, setQuality] = React.useState<'good' | 'bad' | 'partial' | ''>('');
  const [tags, setTags] = React.useState<string[]>([]);
  const [comment, setComment] = React.useState('');
  const [status, setStatus] = React.useState<'idle' | 'sending' | 'done' | 'error'>('idle');

  const submit = React.useCallback(async (nextQuality: 'good' | 'bad' | 'partial') => {
    setQuality(nextQuality);
    setStatus('sending');
    try {
      await annotationApi.submitFeedback({
        source: 'kb_ask_user_feedback',
        target_type: 'kb_ask_block',
        target_id: block.ku_id,
        quality: nextQuality,
        rating: nextQuality === 'good' ? 1 : nextQuality === 'bad' ? -1 : 0,
        error_tags: tags,
        comment,
        task_type: 'kb_answer_quality_feedback',
        source_ref: {
          query,
          ku_id: block.ku_id,
          kind: block.kind,
          kb_name: kbName,
        },
        inputs: {
          query,
          ku_id: block.ku_id,
          kind: block.kind,
          kb_name: kbName,
        },
        model_output: {
          block: block as unknown as Record<string, unknown>,
        },
      });
      setStatus('done');
    } catch {
      setStatus('error');
    }
  }, [block, comment, kbName, query, tags]);

  return (
    <div className="mt-3 rounded-xl border border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-[var(--cy-text-tertiary)]">这条结果质量如何？</span>
        <button
          type="button"
          onClick={() => void submit('good')}
          className={cn(
            'inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px]',
            quality === 'good'
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-[var(--cy-border-subtle)] text-[var(--cy-text-secondary)]',
          )}
        >
          <ThumbsUp className="h-3 w-3" /> 好
        </button>
        <button
          type="button"
          onClick={() => void submit('partial')}
          className={cn(
            'rounded-full border px-2 py-1 text-[11px]',
            quality === 'partial'
              ? 'border-amber-300 bg-amber-50 text-amber-700'
              : 'border-[var(--cy-border-subtle)] text-[var(--cy-text-secondary)]',
          )}
        >
          部分正确
        </button>
        <button
          type="button"
          onClick={() => void submit('bad')}
          className={cn(
            'inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px]',
            quality === 'bad'
              ? 'border-red-300 bg-red-50 text-red-700'
              : 'border-[var(--cy-border-subtle)] text-[var(--cy-text-secondary)]',
          )}
        >
          <ThumbsDown className="h-3 w-3" /> 坏
        </button>
        {status === 'sending' && <span className="text-[11px] text-[var(--cy-text-tertiary)]">提交中…</span>}
        {status === 'done' && <span className="text-[11px] text-emerald-600">已记录为训练反馈</span>}
        {status === 'error' && <span className="text-[11px] text-red-600">提交失败</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {ERROR_TAGS.map((tag) => {
          const active = tags.includes(tag.id);
          return (
            <button
              key={tag.id}
              type="button"
              onClick={() => setTags((prev) => active ? prev.filter((x) => x !== tag.id) : [...prev, tag.id])}
              className={cn(
                'rounded-full border px-2 py-0.5 text-[10px]',
                active
                  ? 'border-[var(--cy-brand-300)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                  : 'border-[var(--cy-border-subtle)] text-[var(--cy-text-tertiary)]',
              )}
            >
              {tag.label}
            </button>
          );
        })}
      </div>
      <input
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="可选：补充说明，提交好/坏时一起记录"
        className="mt-2 w-full rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-1 text-[11px] outline-none focus:border-[var(--cy-brand-400)]"
      />
    </div>
  );
};

const StatusPill: React.FC<{ block: AskBlock }> = ({ block }) => {
  if (!block.ok) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-600">
        <AlertCircle className="h-3 w-3" /> 失败
      </span>
    );
  }
  let count = 0;
  if (block.kind === 'document') count = block.results?.length ?? 0;
  else if (block.kind === 'vector') count = block.hits?.length ?? 0;
  return (
    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
      {count > 0 ? `${count} 命中` : '完成'}
    </span>
  );
};

const ErrorBody: React.FC<{ error: string }> = ({ error }) => (
  <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>
);


const RewriteTraceBar: React.FC<{ queries: string[]; trace?: RewriteTrace }> = ({ queries, trace }) => {
  const strategy = trace?.strategy_used ?? 'passthrough';
  const strategyLabel: Record<string, string> = {
    passthrough: '原样', rewrite: '改写', multi_query: '多查询',
    hyde: 'HyDE', decompose: '分解', off: '关闭',
  };
  return (
    <div className="rounded-lg border border-violet-200/60 bg-violet-50/40 px-2.5 py-1.5 dark:border-violet-500/30 dark:bg-violet-950/20">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium text-violet-700 dark:text-violet-300">
        <Wand2 className="h-3 w-3" />
        改写策略 <span className="rounded bg-violet-100 px-1.5 py-0.5 dark:bg-violet-900/60">{strategyLabel[strategy] ?? strategy}</span>
        {trace ? <span className="text-violet-500/80">· {trace.elapsed_ms}ms · 共 {trace.n} 条 query</span> : null}
      </div>
      <div className="flex flex-wrap gap-1">
        {queries.map((q, i) => (
          <span
            key={i}
            className={cn(
              'max-w-full truncate rounded-full px-2 py-0.5 text-[11px]',
              i === 0
                ? 'border border-violet-300 bg-white text-violet-900 dark:bg-violet-950/40 dark:text-violet-100'
                : 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-200',
            )}
            title={q}
          >
            {i === 0 ? '原始' : `改写${i}`} · {q.length > 28 ? `${q.slice(0, 28)}…` : q}
          </span>
        ))}
      </div>
    </div>
  );
};

