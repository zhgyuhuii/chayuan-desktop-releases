/**
 * 结构化 KB 详情(SQL / Mongo / ES)。
 *
 * 设计:
 *   - 左侧表/集合清单(可搜索 + 备注)
 *   - 右侧顶部 tab:字段 / 数据 / NL→SQL 试问
 *     · 字段:列定义表(主键/类型/可空/注释)
 *     · 数据:分页拉真实行数据(走 POST /knowledge_universe/structured/rows)
 *     · 问一问:NL 自然语言 → text2sql 试运行(沿用既有 useKbAskTrial)
 *   - degraded 状态在顶部条幅给出
 */

import * as React from 'react';
import {
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Database,
  Hash,
  Loader2,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Search,
  Table2,
  Type,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import {
  knowledgeUniverse,
  type AskBlock,
  type KuStructuredDetail,
  type StructuredField,
  type StructuredTable,
  type StructuredRowsResponse,
} from '@chayuan/api';
import type { DetailPanelProps } from './types';
import { ChatComposer } from '../../composer/ChatComposer';
import { KbResultBlock } from '../shared/KbResultBlock';
import { reportError } from '../../../store/errorDialog';
import { useComposerStore } from '../../../store/composer';

type RightTab = 'fields' | 'rows' | 'ask';

interface StructuredChatMessage {
  id: string;
  role: 'user' | 'assistant';
  query: string;
  block?: Extract<AskBlock, { kind: 'structured' }> | null;
  error?: string | null;
  status: 'running' | 'done' | 'error';
  scopeNames: string[];
}

/** 兼容老/新两套字段名 */
function tableColumns(t: StructuredTable): StructuredField[] {
  return t.columns ?? t.fields ?? [];
}

export const StructuredKbDetail: React.FC<DetailPanelProps<KuStructuredDetail>> = ({
  detail,
}) => {
  const tables = detail.tables ?? [];
  const [activeName, setActiveName] = React.useState<string | null>(tables[0]?.name ?? null);
  const [keyword, setKeyword] = React.useState('');
  const [rightTab, setRightTab] = React.useState<RightTab>('fields');
  const [leftCollapsed, setLeftCollapsed] = React.useState(false);
  const [rightCollapsed, setRightCollapsed] = React.useState(false);
  const [leftWidth, setLeftWidth] = React.useState(288);
  const [rightWidth, setRightWidth] = React.useState(420);
  const [selectedNames, setSelectedNames] = React.useState<Set<string>>(() => new Set(tables.map((t) => t.name)));
  const [messages, setMessages] = React.useState<StructuredChatMessage[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const modelId = useComposerStore((s) => s.modelId);
  const modelIdRef = React.useRef(modelId);
  modelIdRef.current = modelId;

  const filtered = React.useMemo(() => {
    if (!keyword.trim()) return tables;
    const q = keyword.toLowerCase();
    return tables.filter(
      (t) => t.name.toLowerCase().includes(q) || (t.comment ?? '').toLowerCase().includes(q),
    );
  }, [tables, keyword]);

  const active = React.useMemo(
    () => tables.find((t) => t.name === activeName) ?? null,
    [tables, activeName],
  );

  React.useEffect(() => {
    setSelectedNames((prev) => {
      const valid = new Set(tables.map((t) => t.name));
      const next = new Set([...prev].filter((name) => valid.has(name)));
      if (next.size === 0 && tables.length > 0) tables.forEach((t) => next.add(t.name));
      return next;
    });
    if (!activeName && tables[0]) setActiveName(tables[0].name);
  }, [activeName, tables]);

  React.useEffect(() => () => abortRef.current?.abort(), []);

  const allSelected = tables.length > 0 && tables.every((t) => selectedNames.has(t.name));
  const visibleAllSelected = filtered.length > 0 && filtered.every((t) => selectedNames.has(t.name));
  const selectedScopeNames = React.useMemo(
    () => allSelected ? [] : tables.map((t) => t.name).filter((name) => selectedNames.has(name)),
    [allSelected, selectedNames, tables],
  );
  const scopeText = allSelected
    ? `全部${scopeUnitLabel(detail.sub_kind)}`
    : selectedScopeNames.length === 1
      ? `${scopeUnitLabel(detail.sub_kind)}「${selectedScopeNames[0]}」`
      : `已选 ${selectedScopeNames.length} 个${scopeUnitLabel(detail.sub_kind)}`;

  const toggleOne = (name: string) => {
    setActiveName(name);
    setRightTab('fields');
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name) && next.size > 1) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const selectOnly = (name: string) => {
    setActiveName(name);
    setRightTab('fields');
    setSelectedNames(new Set([name]));
  };
  const toggleVisibleAll = () => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (visibleAllSelected) filtered.forEach((t) => next.delete(t.name));
      else filtered.forEach((t) => next.add(t.name));
      if (next.size === 0 && activeName) next.add(activeName);
      return next;
    });
  };
  const selectAll = () => setSelectedNames(new Set(tables.map((t) => t.name)));

  const startResize = React.useCallback((side: 'left' | 'right', event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = side === 'left' ? leftWidth : rightWidth;
    const min = 220;
    const max = Math.min(560, Math.max(260, window.innerWidth - 520));
    const onMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      const next = side === 'left'
        ? startWidth + delta
        : startWidth - delta;
      const width = Math.min(max, Math.max(min, next));
      if (side === 'left') setLeftWidth(width);
      else setRightWidth(width);
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp, { once: true });
  }, [leftWidth, rightWidth]);

  const submit = async (query: string) => {
    const q = query.trim();
    if (!q || streaming) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const scopeNames = allSelected ? [] : selectedScopeNames;
    const userId = `structured-user-${Date.now()}`;
    const assistantId = `structured-assistant-${Date.now()}`;
    setMessages((items) => [
      ...items,
      { id: userId, role: 'user', query: q, status: 'done', scopeNames },
      { id: assistantId, role: 'assistant', query: q, status: 'running', scopeNames },
    ]);
    setStreaming(true);
    try {
      const data = await Promise.race([
        knowledgeUniverse.ask(q, [detail.ku_id], 5, {
          model: modelIdRef.current,
          structuredScopes: scopeNames.length ? { [detail.ku_id]: scopeNames } : undefined,
        }),
        new Promise<never>((_, rej) => {
          ctrl.signal.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')), { once: true });
        }),
      ]);
      if (ctrl.signal.aborted) return;
      const block = (data.results || []).find((b) => b.ku_id === detail.ku_id && b.kind === 'structured') as Extract<AskBlock, { kind: 'structured' }> | undefined;
      setMessages((items) => items.map((m) => (
        m.id === assistantId
          ? { ...m, status: block?.ok ? 'done' : 'error', block: block ?? null, error: block?.error ?? null }
          : m
      )));
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') return;
      setMessages((items) => items.map((m) => (
        m.id === assistantId ? { ...m, status: 'error', error: (e as Error).message || String(e) } : m
      )));
      reportError(e, '结构化知识库对话失败');
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setStreaming(false);
    }
  };

  if (tables.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-[var(--cy-text-tertiary)]">
        <Database className="h-8 w-8" />
        <p>没有可读的表/集合</p>
        {detail.degraded ? (
          <p className="max-w-md break-words text-center text-[11px] text-amber-700">
            内省失败:{detail.degraded}
          </p>
        ) : (
          <p className="text-xs">检查权限或 connection 状态</p>
        )}
      </div>
    );
  }

  const cols = active ? tableColumns(active) : [];

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      {detail.degraded && (
        <div className="flex-shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-[11px] text-amber-800">
          部分内省失败,数据可能不全:{detail.degraded}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <aside className={cn(
          'flex flex-shrink-0 flex-col border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] transition-all',
          leftCollapsed && 'w-12',
        )}
        style={leftCollapsed ? undefined : { width: leftWidth }}
        >
          <div className="flex h-11 items-center gap-2 border-b border-[var(--cy-border-subtle)] px-2">
            <button
              type="button"
              onClick={() => setLeftCollapsed((v) => !v)}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
              title={leftCollapsed ? '展开表清单' : '折叠表清单'}
            >
              {leftCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
            {!leftCollapsed && (
              <div className="min-w-0 flex-1 text-xs font-medium text-[var(--cy-text-primary)]">
                {scopeUnitLabel(detail.sub_kind)}清单 · 已选 {selectedNames.size}
              </div>
            )}
          </div>
          {leftCollapsed ? (
            <button
              type="button"
              onClick={() => setLeftCollapsed(false)}
              className="m-2 flex flex-1 items-center justify-center rounded-xl text-[11px] text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
              title="展开表清单"
            >
              <Database className="h-4 w-4" />
            </button>
          ) : (
            <>
              <div className="space-y-2 border-b border-[var(--cy-border-subtle)] px-3 py-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder={`搜索 ${scopeUnitLabel(detail.sub_kind)}`}
                    className="h-8 w-full rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-7 pr-3 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
                  />
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={toggleVisibleAll}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
                  >
                    <CheckSquare className="h-3 w-3" />
                    {visibleAllSelected ? '取消当前全选' : '全选当前'}
                  </button>
                  <button
                    type="button"
                    onClick={selectAll}
                    className="rounded-full px-2 py-1 text-[11px] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
                  >
                    全库
                  </button>
                </div>
              </div>
              <ul className="flex-1 overflow-y-auto py-1">
                {filtered.map((t) => {
                  const fcount = tableColumns(t).length;
                  const checked = selectedNames.has(t.name);
                  return (
                    <li key={t.name}>
                      <div
                        className={cn(
                          'group flex w-full items-center gap-2 px-2 py-1.5 text-xs transition-colors',
                          activeName === t.name
                            ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                            : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => toggleOne(t.name)}
                          className={cn(
                            'flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                            checked ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-500)] text-white' : 'border-[var(--cy-border-subtle)]',
                          )}
                          title={checked ? '取消选择' : '选择'}
                        >
                          {checked ? <CheckSquare className="h-3 w-3" /> : null}
                        </button>
                        <button
                          type="button"
                          onClick={() => { setActiveName(t.name); setRightTab('fields'); }}
                          onDoubleClick={() => selectOnly(t.name)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left"
                          title="点击查看字段和数据，双击仅选择此项"
                        >
                          <Database className="h-3 w-3 flex-shrink-0" />
                          <span className="min-w-0 flex-1 truncate font-medium">{t.name}</span>
                          {fcount > 0 && <span className="text-[10px] opacity-60">{fcount}</span>}
                        </button>
                      </div>
                      {activeName === t.name && t.comment && (
                        <div className="px-7 pb-2 text-[10px] text-[var(--cy-text-tertiary)]">{t.comment}</div>
                      )}
                    </li>
                  );
                })}
                {filtered.length === 0 && (
                  <li className="px-3 py-4 text-center text-[11px] text-[var(--cy-text-tertiary)]">没有匹配</li>
                )}
              </ul>
            </>
          )}
        </aside>
        {!leftCollapsed && (
          <div
            role="separator"
            aria-orientation="vertical"
            title="拖动调整左侧宽度"
            onPointerDown={(event) => startResize('left', event)}
            className="group relative z-10 w-1 cursor-col-resize bg-transparent"
          >
            <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-[var(--cy-brand-400)]" />
          </div>
        )}

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--cy-surface-base)]">
          <header className="flex h-11 flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">结构化对话</p>
              <p className="truncate text-[11px] text-[var(--cy-text-tertiary)]">范围：{scopeText}</p>
            </div>
            <button
              type="button"
              onClick={() => setMessages([])}
              className="text-[11px] text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]"
            >
              清空消息
            </button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full w-full flex-col items-center justify-center text-center text-sm text-[var(--cy-text-tertiary)]">
                <MessageSquare className="mb-3 h-8 w-8" />
                <p className="font-medium text-[var(--cy-text-secondary)]">在下方输入问题，直接对结构化数据提问</p>
                <p className="mt-1 text-xs">左侧可全选或单选{scopeUnitLabel(detail.sub_kind)}，右侧查看当前字段和数据。</p>
              </div>
            ) : (
              <div className="flex w-full flex-col gap-4">
                {messages.map((m) => (
                  <StructuredMessageBubble
                    key={m.id}
                    message={m}
                    detail={detail}
                    onRetry={() => void submit(m.query)}
                  />
                ))}
              </div>
            )}
          </div>
          <div className="flex-shrink-0 border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-3">
            <ChatComposer
              isStreaming={streaming}
              onSend={submit}
              onStop={() => abortRef.current?.abort()}
              glow={false}
              placeholder={`询问${scopeText}，例如：统计最近 30 天趋势`}
              showRetrievalControls
            />
          </div>
        </section>

        {!rightCollapsed && (
          <div
            role="separator"
            aria-orientation="vertical"
            title="拖动调整右侧宽度"
            onPointerDown={(event) => startResize('right', event)}
            className="group relative z-10 w-1 cursor-col-resize bg-transparent"
          >
            <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-[var(--cy-brand-400)]" />
          </div>
        )}
        <aside className={cn(
          'flex flex-shrink-0 flex-col border-l border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] transition-all',
          rightCollapsed && 'w-12',
        )}
        style={rightCollapsed ? undefined : { width: rightWidth }}
        >
          <div className={cn(
            'flex h-11 items-center gap-2 border-b border-[var(--cy-border-subtle)] px-2',
            rightCollapsed ? 'justify-end' : 'justify-between',
          )}>
            {!rightCollapsed && (
              <>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-[var(--cy-text-primary)]">{active?.name ?? '—'}</p>
                  {active?.comment && <p className="truncate text-[10px] text-[var(--cy-text-tertiary)]">{active.comment}</p>}
                </div>
                <div className="ml-auto flex shrink-0 items-center justify-end gap-1">
                  <RightTabBtn icon={<Type className="h-3.5 w-3.5" />} active={rightTab === 'fields'} onClick={() => setRightTab('fields')}>字段</RightTabBtn>
                  <RightTabBtn icon={<Table2 className="h-3.5 w-3.5" />} active={rightTab === 'rows'} onClick={() => setRightTab('rows')}>数据</RightTabBtn>
                </div>
              </>
            )}
            <button
              type="button"
              onClick={() => setRightCollapsed((v) => !v)}
              className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
              title={rightCollapsed ? '展开字段和数据' : '折叠字段和数据'}
            >
              {rightCollapsed ? <PanelRightOpen className="h-4 w-4" /> : <PanelRightClose className="h-4 w-4" />}
            </button>
          </div>
          {rightCollapsed ? (
            <button
              type="button"
              onClick={() => setRightCollapsed(false)}
              className="ml-auto mr-2 mt-2 flex h-[calc(100%-1rem)] w-8 items-start justify-center rounded-xl pt-3 text-[11px] text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
              title="展开字段和数据"
            >
              <Table2 className="h-4 w-4" />
            </button>
          ) : rightTab === 'fields' ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <FieldsTable fields={cols} />
            </div>
          ) : active ? (
            <RowsPane kuId={detail.ku_id} table={active.name} schema={active.schema} subKind={detail.sub_kind} />
          ) : null}
        </aside>
      </div>
    </div>
  );
};

const RightTabBtn: React.FC<{
  active: boolean;
  onClick(): void;
  icon: React.ReactNode;
  children: React.ReactNode;
}> = ({ active, onClick, icon, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors',
      active
        ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
        : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
    )}
  >
    {icon}
    {children}
  </button>
);

function scopeUnitLabel(kind: KuStructuredDetail['sub_kind']): string {
  if (kind === 'mongo') return '集合';
  if (kind === 'es') return '索引';
  return '表';
}

const StructuredMessageBubble: React.FC<{
  message: StructuredChatMessage;
  detail: KuStructuredDetail;
  onRetry(): void;
}> = ({ message, detail, onRetry }) => {
  const isUser = message.role === 'user';
  const scopeLabel = message.scopeNames.length
    ? `${message.scopeNames.length} 个${scopeUnitLabel(detail.sub_kind)}`
    : `全部${scopeUnitLabel(detail.sub_kind)}`;
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn(
        'max-w-[92%] rounded-2xl px-3 py-2 text-sm shadow-sm',
        isUser
          ? 'bg-[var(--cy-ink-700)] text-white'
          : 'border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] text-[var(--cy-text-primary)]',
      )}>
        <div className={cn('mb-1 text-[10px]', isUser ? 'text-white/65' : 'text-[var(--cy-text-tertiary)]')}>
          {isUser ? `提问 · ${scopeLabel}` : message.status === 'running' ? '正在查询结构化数据...' : `结构化结果 · ${scopeLabel}`}
        </div>
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.query}</p>
        ) : message.status === 'running' ? (
          <div className="inline-flex items-center gap-2 text-xs text-[var(--cy-text-secondary)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            正在生成 SQL 并读取数据...
          </div>
        ) : message.status === 'error' ? (
          <div className="space-y-2 text-xs">
            <p className="text-red-600">{message.error || message.block?.error || '查询失败'}</p>
            <button type="button" onClick={onRetry} className="text-[var(--cy-brand-700)] hover:underline">
              重试
            </button>
          </div>
        ) : (
          <KbResultBlock block={message.block ?? null} query={message.query} onRetry={onRetry} />
        )}
      </div>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// FieldsTable
// ──────────────────────────────────────────────────────────────

const FieldsTable: React.FC<{ fields: StructuredField[] }> = ({ fields }) => {
  if (fields.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
        没有字段信息
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-[1] bg-[var(--cy-surface-1)] text-xs text-[var(--cy-text-tertiary)]">
          <tr>
            <th className="w-8 px-3 py-2"></th>
            <th className="px-2 py-2 text-left font-medium">字段</th>
            <th className="px-2 py-2 text-left font-medium">类型</th>
            <th className="hidden px-2 py-2 text-left font-medium md:table-cell">可空</th>
            <th className="px-2 py-2 text-left font-medium">注释</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f.name} className="border-b border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]">
              <td className="px-3 py-1.5">
                <Hash
                  className={cn('h-3.5 w-3.5', f.primary_key ? 'text-amber-500' : 'text-[var(--cy-text-tertiary)]')}
                />
              </td>
              <td className="px-2 py-1.5 font-medium">
                {f.name}
                {f.primary_key && <span className="ml-1 rounded bg-amber-100 px-1 text-[9px] font-bold text-amber-700">PK</span>}
              </td>
              <td className="px-2 py-1.5 text-xs text-[var(--cy-text-secondary)]">
                <span className="inline-flex items-center gap-1 rounded bg-[var(--cy-surface-2)] px-1.5 py-0.5 font-mono">
                  <Type className="h-3 w-3 opacity-60" />
                  {f.type}
                </span>
              </td>
              <td className="hidden px-2 py-1.5 text-xs text-[var(--cy-text-tertiary)] md:table-cell">
                {f.nullable === false ? 'NOT NULL' : f.nullable === true ? 'NULL' : '—'}
              </td>
              <td className="px-2 py-1.5 text-xs text-[var(--cy-text-tertiary)]">{f.comment || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// RowsPane:分页拉真实行数据
// ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 20;

const RowsPane: React.FC<{ kuId: string; table: string; schema?: string; subKind: 'sql' | 'mongo' | 'es' }> = ({
  kuId, table, schema, subKind,
}) => {
  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<StructuredRowsResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<Error | null>(null);
  const acRef = React.useRef<AbortController | null>(null);

  // 切表/翻页 → 重新拉
  React.useEffect(() => {
    setPage(1);
    setData(null);
    setErr(null);
  }, [table, schema, kuId]);

  const load = React.useCallback(async (p: number) => {
    if (subKind !== 'sql') {
      setErr(new Error(`暂不支持 ${subKind} 类型的表预览(仅 SQL)`));
      return;
    }
    acRef.current?.abort();
    const ac = new AbortController();
    acRef.current = ac;
    setLoading(true); setErr(null);
    try {
      const r = await knowledgeUniverse.fetchStructuredRows({
        ku_id: kuId,
        table,
        schema,
        page: p,
        page_size: PAGE_SIZE,
      });
      if (ac.signal.aborted) return;
      setData(r);
      setPage(r.page);
    } catch (e) {
      if (ac.signal.aborted) return;
      const err = e as Error;
      setErr(err);
      reportError(err, '拉取数据失败');
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, [kuId, table, schema, subKind]);

  // 初次载入
  React.useEffect(() => {
    void load(1);
    return () => acRef.current?.abort();
  }, [load]);

  const total = data?.total ?? null;
  const totalPages = total != null ? Math.max(1, Math.ceil(total / PAGE_SIZE)) : null;
  const canPrev = page > 1;
  const canNext = totalPages != null ? page < totalPages : (data?.rows.length ?? 0) === PAGE_SIZE;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/60 px-4 py-1.5 text-[11px] text-[var(--cy-text-tertiary)]">
        <span>
          {total != null ? `共 ${total.toLocaleString()} 行` : '行数未知'} · 第 {page} {totalPages ? `/ ${totalPages}` : ''} 页 · 每页 {PAGE_SIZE}
        </span>
        <div className="flex items-center gap-1">
          <Button size="icon" variant="ghost" onClick={() => void load(page)} disabled={loading} aria-label="刷新" className="h-7 w-7">
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </Button>
          <Button size="icon" variant="ghost" disabled={!canPrev || loading} onClick={() => void load(page - 1)} aria-label="上一页" className="h-7 w-7">
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="ghost" disabled={!canNext || loading} onClick={() => void load(page + 1)} aria-label="下一页" className="h-7 w-7">
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {loading && !data ? (
          <div className="flex h-full items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> 加载中…
          </div>
        ) : err ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-[var(--cy-text-secondary)]">
            <p className="text-[var(--cy-text-primary)] font-medium">拉取失败</p>
            <p className="max-w-md break-words">{err.message}</p>
            <Button size="sm" variant="outline" onClick={() => void load(page)}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> 重试
            </Button>
          </div>
        ) : !data || data.rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-[var(--cy-text-tertiary)]">空表</div>
        ) : (
          <table className="w-full border-separate border-spacing-0 text-xs">
            <thead className="sticky top-0 z-[1]">
              <tr>
                <th className="border-b border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-1.5 text-left font-semibold text-[var(--cy-text-tertiary)]">#</th>
                {data.columns.map((c) => (
                  <th key={c} className="border-b border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-1.5 text-left font-semibold text-[var(--cy-text-primary)]">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-[var(--cy-surface-1)]">
                  <td className="border-b border-r border-[var(--cy-border-subtle)] px-3 py-1 text-[var(--cy-text-tertiary)]">
                    {(page - 1) * PAGE_SIZE + ri + 1}
                  </td>
                  {row.map((cell, ci) => (
                    <td key={ci} className="border-b border-r border-[var(--cy-border-subtle)] px-3 py-1 align-top text-[var(--cy-text-secondary)]">
                      <span className="block max-w-[28em] truncate" title={String(cell ?? '')}>
                        {fmtCell(cell)}
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

function fmtCell(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
