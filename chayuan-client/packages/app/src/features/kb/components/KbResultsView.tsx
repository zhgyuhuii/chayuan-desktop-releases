/**
 * KbResultsView — 知识检索结果统一展示组件。
 *
 * 三层折叠层级:
 *   ┌ 顶部条 (chevron)         ── 一个 RetrievalSource[] 总入口,默认折叠
 *   │
 *   ├─ Source 卡 (chevron)     ── 一个 KB / 一个外链桶
 *   │   ┌ kind 图标 + 名称 + kind 徽章 + 命中段数
 *   │   │
 *   │   ├─ Group 卡 (chevron)  ── 一份文件 / 一个集合 / 一张表 / 一个站点
 *   │   │   ┌ [N] 文件名 / 表名 [score] [预览] [下载]
 *   │   │   │
 *   │   │   ├─ Snippet 列表    ── 段 / 行 / SQL / 向量 metadata
 *   │
 * 适用三处:
 *   - chat 消息引用 (MessageBubble)
 *   - /kb 跨 KB 提问 (KbAskResultPanel)
 *   - 单 KB 试问 (KbResultBlock)
 */

import * as React from 'react';
import {
  Boxes,
  ChevronDown,
  Copy,
  Database,
  Download as DownloadIcon,
  ExternalLink,
  FileText,
  Globe,
  Images,
} from 'lucide-react';
import { cn } from '@chayuan/ui';
import {
  getAccessToken,
  getClientConfig,
  type RetrievalGroup,
  type RetrievalKind,
  type RetrievalSnippet,
  type RetrievalSource,
  summarizeSources,
} from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { usePreviewStore } from '../../preview/previewStore';

const KIND_META: Record<
  RetrievalKind,
  { icon: React.ComponentType<{ className?: string }>; tint: string; label: string }
> = {
  document: { icon: FileText, tint: 'text-sky-600 bg-sky-50', label: '文档' },
  structured: { icon: Database, tint: 'text-emerald-600 bg-emerald-50', label: '结构化' },
  vector: { icon: Boxes, tint: 'text-amber-600 bg-amber-50', label: '向量' },
  image: { icon: Images, tint: 'text-pink-600 bg-pink-50', label: '图像' },
  web: { icon: Globe, tint: 'text-slate-600 bg-slate-100', label: '外链' },
  file: { icon: FileText, tint: 'text-slate-600 bg-slate-100', label: '文件' },
};

export interface KbResultsViewProps {
  sources: RetrievalSource[];
  /** 顶部状态:'检索中' 时在标题后追加 hint */
  streaming?: boolean;
  /** 顶部条只展示一行 hint;默认 "引用依据" */
  title?: string;
  /** 强制顶部初始展开(详情试问用 true,聊天 citation 用 false) */
  defaultOpen?: boolean;
  /** 单 source 时跳过 source 一层、直接展开 groups(详情试问用) */
  flattenSingleSource?: boolean;
  /** 隐藏顶部折叠条本身,只渲染内层(KbAskResultPanel 已经有自己的 ProgressHeader) */
  hideTopFold?: boolean;
  /** 没有任何 source 时给一个空态文案;不传则不渲染 */
  emptyHint?: React.ReactNode;
  className?: string;
}

export const KbResultsView: React.FC<KbResultsViewProps> = ({
  sources,
  streaming = false,
  title = '引用依据',
  defaultOpen = false,
  flattenSingleSource = false,
  hideTopFold = false,
  emptyHint,
  className,
}) => {
  const [open, setOpen] = React.useState(defaultOpen);

  if (!sources?.length) {
    return emptyHint ? <div className={className}>{emptyHint}</div> : null;
  }

  const summary = summarizeSources(sources);
  const flatten = flattenSingleSource && sources.length === 1;

  if (hideTopFold) {
    return (
      <div className={cn('flex flex-col gap-2', className)}>
        {flatten
          ? sources[0]!.groups.map((g) => (
              <GroupCard
                key={g.key}
                source={sources[0]!}
                group={g}
                defaultOpen={sources[0]!.groups.length === 1}
              />
            ))
          : sources.map((s) => <SourceCard key={s.id} source={s} />)}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'w-full max-w-full overflow-hidden border-l-2 border-indigo-300 bg-transparent',
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-2 py-1 text-left hover:bg-[var(--cy-bg-hover)]"
      >
        <span className="inline-flex min-w-0 flex-1 items-center gap-1.5">
          <span className="inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
            <FileText className="h-3 w-3" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--cy-text-tertiary)]">
            <span className="font-medium text-[var(--cy-text-secondary)]">{title}</span>
            <span className="mx-1">·</span>
            {summary.sourceCount} 个来源 / {summary.groupCount} 项 / {summary.snippetCount} 段
            {streaming ? ' · 检索中' : ''}
          </span>
        </span>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 flex-shrink-0 text-[var(--cy-text-tertiary)] transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 py-2">
          {flatten
            ? sources[0]!.groups.map((g) => (
                <GroupCard
                  key={g.key}
                  source={sources[0]!}
                  group={g}
                  defaultOpen={sources[0]!.groups.length === 1}
                />
              ))
            : sources.map((s) => <SourceCard key={s.id} source={s} />)}
        </div>
      )}
    </div>
  );
};

// ── Source-level card ─────────────────────────────────────────────────────

const SourceCard: React.FC<{ source: RetrievalSource }> = ({ source }) => {
  const [open, setOpen] = React.useState(true); // 顶部已折叠;source 默认展开
  const meta = KIND_META[source.kind] ?? KIND_META.document;
  const Icon = meta.icon;
  const totalHits = source.groups.reduce(
    (sum, g) => sum + Math.max(g.count || 0, g.snippets.length),
    0,
  );

  return (
    <section className="rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-[var(--cy-bg-hover)]"
      >
        <span className="inline-flex min-w-0 items-center gap-2">
          <span className={cn('flex h-6 w-6 items-center justify-center rounded-md', meta.tint)}>
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="truncate text-sm font-medium text-[var(--cy-text-primary)]">
            {source.label}
          </span>
          <span className="rounded-full bg-[var(--cy-surface-2)] px-2 py-0.5 text-[10px] text-[var(--cy-text-tertiary)]">
            {meta.label}
          </span>
          {!source.ok && (
            <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-600">
              失败
            </span>
          )}
        </span>
        <span className="inline-flex items-center gap-2">
          {source.ok && totalHits > 0 && (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
              {source.groups.length} 项 · {totalHits} 段
            </span>
          )}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 flex-shrink-0 text-[var(--cy-text-tertiary)] transition-transform',
              open && 'rotate-180',
            )}
          />
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--cy-border-subtle)] px-3 py-2">
          {source.error ? (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{source.error}</p>
          ) : source.groups.length === 0 ? (
            <p className="text-xs text-[var(--cy-text-tertiary)]">没有命中,可调整查询范围或换个问法。</p>
          ) : (
            source.groups.map((g) => <GroupCard key={g.key} source={source} group={g} />)
          )}
          {source.diagnostic && (
            <p className="text-[11px] text-[var(--cy-text-tertiary)]">诊断:{source.diagnostic}</p>
          )}
        </div>
      )}
    </section>
  );
};

// ── Group-level card ──────────────────────────────────────────────────────

const GroupCard: React.FC<{
  source: RetrievalSource;
  group: RetrievalGroup;
  defaultOpen?: boolean;
}> = ({ source, group, defaultOpen = false }) => {
  const [open, setOpen] = React.useState(defaultOpen);
  const openPreview = usePreviewStore((s) => s.open);
  const meta = KIND_META[group.kind] ?? KIND_META.document;
  const totalHits = Math.max(group.count || 0, group.snippets.length);
  const idx = group.citeIndex;

  const onDownload = React.useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!group.download) return;
      const cfg = getClientConfig();
      const base = (cfg.baseURL || '').replace(/\/+$/, '');
      const qs = new URLSearchParams({
        knowledge_base_name: group.download.kbName,
        file_name: group.download.fileName,
      });
      const tok = await getAccessToken();
      if (tok) qs.set('token', tok);
      const url = `${base}/knowledge_base/download_doc?${qs.toString()}`;
      // Tauri webview 下 ``<a download>`` 不可靠 — WebView2 默认拦截下载、不走
      // 标准浏览器下载流程,用户感知就是"点了下载没反应"。改走 platform.shell.openExternal
      // 把 URL 交给系统默认浏览器,浏览器拿到 Content-Disposition: attachment 自然
      // 触发下载,文件落到用户的下载目录。
      // platform-web 的 openExternal 兜底 window.open,行为对 Web 端也合理(新 tab 触发下载)。
      void getPlatform().shell.openExternal(url);
    },
    [group.download],
  );

  const onPreview = React.useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!group.preview) return;
      openPreview({
        source: 'kb-doc',
        kbName: group.preview.kbName,
        fileName: group.preview.fileName,
      });
    },
    [group.preview, openPreview],
  );

  return (
    <article className="rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]">
      {/* 用 div[role=button] 而非 <button> —— 展开头里嵌了「原文 / 下载」按钮和
          外链 <a>,<button> 套 <button> 是非法 HTML(React 会报 nesting 错)。 */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-start gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--cy-bg-hover)]"
      >
        {idx ? (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--cy-brand-600)] px-1 text-[10px] font-bold text-white">
            {idx}
          </span>
        ) : (
          <span className={cn('mt-0.5 flex h-4 w-4 items-center justify-center rounded', meta.tint)}>
            <meta.icon className="h-2.5 w-2.5" />
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2 text-[12px] font-medium text-[var(--cy-text-primary)]">
            <span className="max-w-[260px] truncate">{group.label}</span>
            {totalHits > 1 && (
              <span className="rounded-full bg-[var(--cy-surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--cy-text-tertiary)]">
                命中 {totalHits} 段
              </span>
            )}
            {group.bestScore != null && group.bestScore > 0 && (
              <span className="font-mono text-[10px] text-[var(--cy-text-tertiary)]">
                score {group.bestScore.toFixed(3)}
              </span>
            )}
            {group.kind === 'web' && group.url && (
              <a
                href={group.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1 text-[10px] text-[var(--cy-brand-600)] hover:underline"
              >
                <ExternalLink className="h-3 w-3" /> 打开
              </a>
            )}
          </span>
          {!open && group.snippets[0]?.text && (
            <span className="mt-0.5 line-clamp-1 text-[11px] text-[var(--cy-text-tertiary)]">
              {group.snippets[0].text}
            </span>
          )}
        </span>
        <span className="inline-flex flex-shrink-0 items-center gap-1">
          {group.preview && (
            <button
              type="button"
              onClick={onPreview}
              title="查看原文"
              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] text-[var(--cy-text-tertiary)] hover:bg-white/70 hover:text-[var(--cy-brand-600)]"
              aria-label="查看原文"
            >
              <ExternalLink className="h-3 w-3" />
              原文
            </button>
          )}
          {group.download && (
            <button
              type="button"
              onClick={onDownload}
              title="下载"
              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] text-[var(--cy-text-tertiary)] hover:bg-white/70 hover:text-[var(--cy-brand-600)]"
              aria-label="下载"
            >
              <DownloadIcon className="h-3 w-3" />
              下载
            </button>
          )}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-[var(--cy-text-tertiary)] transition-transform',
              open && 'rotate-180',
            )}
          />
        </span>
      </div>
      {open && (
        <div className="space-y-2 border-t border-[var(--cy-border-subtle)] px-2.5 py-2">
          {group.summary && (
            <div className="rounded-lg bg-[var(--cy-surface-1)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--cy-text-secondary)] whitespace-pre-wrap">
              {group.summary}
            </div>
          )}
          {group.kind === 'structured' && group.sqlText && (
            <SqlBlock sql={group.sqlText} />
          )}
          {group.kind === 'structured' && group.rows && group.rows.length > 0 && (
            <RowsTable rows={group.rows} columns={group.columns} truncationHint={group.truncationHint} />
          )}
          {group.snippets.length > 0 && (
            <SnippetList snippets={group.snippets} kind={group.kind} />
          )}
          {/* 占位:有 source/group 但完全没填充内容 */}
          {!group.summary && !group.sqlText && !group.rows?.length && !group.snippets.length && (
            <p className="text-[11px] text-[var(--cy-text-tertiary)]">无可展示内容</p>
          )}
        </div>
      )}
    </article>
  );
};

// ── Snippet list ──────────────────────────────────────────────────────────

const SnippetList: React.FC<{ snippets: RetrievalSnippet[]; kind: RetrievalKind }> = ({
  snippets,
  kind,
}) => {
  const [showAll, setShowAll] = React.useState(false);
  const visible = showAll ? snippets : snippets.slice(0, 4);
  return (
    <div className="space-y-1.5">
      {visible.map((snippet, i) => {
        const text = snippet.text?.trim();
        if (!text && !snippet.imageUrl) return null;
        return (
          <blockquote
            key={i}
            className="rounded-lg border-l-2 border-indigo-300 bg-white/60 px-2 py-1.5 text-[11px] leading-relaxed text-[var(--cy-text-secondary)]"
          >
            <div className="mb-0.5 flex flex-wrap gap-1.5 text-[10px] text-[var(--cy-text-tertiary)]">
              <span>{kind === 'structured' ? '行' : kind === 'vector' ? '向量' : '段'} {i + 1}</span>
              {snippet.page != null && <span>第 {Number(snippet.page) + 1} 页</span>}
              {snippet.chunkIndex != null && <span>#{snippet.chunkIndex}</span>}
              {snippet.retrievalPath && <span>{snippet.retrievalPath}</span>}
              {snippet.collection && <span>collection: {snippet.collection}</span>}
              {snippet.vectorId && <span>id: {snippet.vectorId}</span>}
              {snippet.score != null && (
                <span className="font-mono">score {snippet.score.toFixed(3)}</span>
              )}
            </div>
            {snippet.imageUrl && (
              <img
                src={snippet.imageUrl}
                alt={text || 'image'}
                className="mb-1 max-h-32 rounded"
                loading="lazy"
              />
            )}
            {text && <p className="line-clamp-4 whitespace-pre-wrap break-words">{text}</p>}
          </blockquote>
        );
      })}
      {snippets.length > visible.length && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="text-[10px] text-[var(--cy-brand-600)] hover:underline"
        >
          展开剩余 {snippets.length - visible.length} 条
        </button>
      )}
    </div>
  );
};

// ── Structured: SQL + rows ────────────────────────────────────────────────

const SqlBlock: React.FC<{ sql: string }> = ({ sql }) => {
  const [copied, setCopied] = React.useState(false);
  const onCopy = React.useCallback(() => {
    void navigator.clipboard.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [sql]);
  return (
    <div className="rounded-lg bg-[var(--cy-code-bg,#0f172a)] text-[var(--cy-code-fg,#e2e8f0)]">
      <div className="flex items-center justify-between border-b border-white/10 px-2 py-1 text-[10px] uppercase tracking-wider text-white/60">
        <span>SQL</span>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-white/10"
        >
          <Copy className="h-3 w-3" />
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="overflow-x-auto px-2 py-2 text-[11px] leading-snug">
        <code>{sql.trim()}</code>
      </pre>
    </div>
  );
};

const RowsTable: React.FC<{
  rows: Array<Record<string, unknown>>;
  columns?: string[];
  truncationHint?: string;
}> = ({ rows, columns, truncationHint }) => {
  const cols = columns?.length ? columns : Object.keys(rows[0] || {});
  return (
    <div className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] text-[11px]">
      <div className="overflow-x-auto">
        <table className="w-full table-auto">
          <thead className="bg-[var(--cy-surface-2)] text-left text-[var(--cy-text-tertiary)]">
            <tr>
              {cols.map((c) => (
                <th key={c} className="px-2 py-1 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-t border-[var(--cy-border-subtle)]">
                {cols.map((c) => (
                  <td key={c} className="px-2 py-1 align-top text-[var(--cy-text-secondary)]">
                    {formatCell(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncationHint && (
        <p className="border-t border-[var(--cy-border-subtle)] px-2 py-1 text-[10px] text-[var(--cy-text-tertiary)]">
          {truncationHint}
        </p>
      )}
    </div>
  );
};

function formatCell(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
