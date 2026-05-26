/**
 * 向量型 KB 详情。
 *
 * 布局对齐结构化知识库:
 *   - 左侧:collection 清单，可全库/多选/单选
 *   - 中间:按当前 collection 范围检索对话
 *   - 右侧:当前 collection 的字段说明与检索样例数据
 */

import * as React from 'react';
import {
  Boxes,
  CheckSquare,
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
  type KuVectorDetail,
  type VectorCollection,
  type VectorRowsResponse,
} from '@chayuan/api';
import type { DetailPanelProps } from './types';
import { ChatComposer } from '../../composer/ChatComposer';
import { KbResultBlock } from '../shared/KbResultBlock';
import { reportError } from '../../../store/errorDialog';
import { useComposerStore } from '../../../store/composer';

type RightTab = 'fields' | 'rows';

interface VectorChatMessage {
  id: string;
  role: 'user' | 'assistant';
  query: string;
  block?: Extract<AskBlock, { kind: 'vector' }> | null;
  error?: string | null;
  status: 'running' | 'done' | 'error';
  scopeNames: string[];
}

export const VectorKbDetail: React.FC<DetailPanelProps<KuVectorDetail>> = ({ detail }) => {
  const collections = detail.collections ?? [];
  const [activeName, setActiveName] = React.useState<string | null>(collections[0]?.name ?? null);
  const [keyword, setKeyword] = React.useState('');
  const [rightTab, setRightTab] = React.useState<RightTab>('fields');
  const [leftCollapsed, setLeftCollapsed] = React.useState(false);
  const [rightCollapsed, setRightCollapsed] = React.useState(false);
  const [selectedNames, setSelectedNames] = React.useState<Set<string>>(() => new Set(collections.map((c) => c.name)));
  const [messages, setMessages] = React.useState<VectorChatMessage[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const modelId = useComposerStore((s) => s.modelId);
  const modelIdRef = React.useRef(modelId);
  modelIdRef.current = modelId;

  const filtered = React.useMemo(() => {
    if (!keyword.trim()) return collections;
    const q = keyword.toLowerCase();
    return collections.filter((c) => c.name.toLowerCase().includes(q) || (c.comment ?? '').toLowerCase().includes(q));
  }, [collections, keyword]);

  const active = React.useMemo(
    () => collections.find((c) => c.name === activeName) ?? null,
    [activeName, collections],
  );

  React.useEffect(() => {
    setSelectedNames((prev) => {
      const valid = new Set(collections.map((c) => c.name));
      const next = new Set([...prev].filter((name) => valid.has(name)));
      if (next.size === 0 && collections.length > 0) collections.forEach((c) => next.add(c.name));
      return next;
    });
    if (!activeName && collections[0]) setActiveName(collections[0].name);
  }, [activeName, collections]);

  React.useEffect(() => () => abortRef.current?.abort(), []);

  const allSelected = collections.length > 0 && collections.every((c) => selectedNames.has(c.name));
  const visibleAllSelected = filtered.length > 0 && filtered.every((c) => selectedNames.has(c.name));
  const selectedScopeNames = React.useMemo(
    () => allSelected ? [] : collections.map((c) => c.name).filter((name) => selectedNames.has(name)),
    [allSelected, collections, selectedNames],
  );
  const scopeText = allSelected
    ? '全部集合'
    : selectedScopeNames.length === 1
      ? `集合「${selectedScopeNames[0]}」`
      : `已选 ${selectedScopeNames.length} 个集合`;

  const selectOnly = (name: string) => {
    setActiveName(name);
    setRightTab('rows');
    setSelectedNames(new Set([name]));
  };
  const toggleOne = (name: string) => {
    setActiveName(name);
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name) && next.size > 1) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const toggleVisibleAll = () => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (visibleAllSelected) filtered.forEach((c) => next.delete(c.name));
      else filtered.forEach((c) => next.add(c.name));
      if (next.size === 0 && activeName) next.add(activeName);
      return next;
    });
  };
  const selectAll = () => setSelectedNames(new Set(collections.map((c) => c.name)));

  const submit = async (query: string) => {
    const q = query.trim();
    if (!q || streaming) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const scopeNames = allSelected ? [] : selectedScopeNames;
    const userId = `vector-user-${Date.now()}`;
    const assistantId = `vector-assistant-${Date.now()}`;
    setMessages((items) => [
      ...items,
      { id: userId, role: 'user', query: q, status: 'done', scopeNames },
      { id: assistantId, role: 'assistant', query: q, status: 'running', scopeNames },
    ]);
    setStreaming(true);
    try {
      const data = await Promise.race([
        knowledgeUniverse.ask(q, [detail.ku_id], 8, {
          model: modelIdRef.current,
          vectorScopes: scopeNames.length ? { [detail.ku_id]: scopeNames } : undefined,
        }),
        new Promise<never>((_, reject) => {
          ctrl.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
        }),
      ]);
      if (ctrl.signal.aborted) return;
      const block = (data.results || []).find((b) => b.ku_id === detail.ku_id && b.kind === 'vector') as Extract<AskBlock, { kind: 'vector' }> | undefined;
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
      reportError(e, '向量库对话失败');
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setStreaming(false);
    }
  };

  if (collections.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-[var(--cy-text-tertiary)]">
        <Boxes className="h-8 w-8" />
        <p>没有可读集合</p>
        {detail.degraded ? (
          <p className="max-w-md break-words text-center text-[11px] text-amber-700">内省失败:{detail.degraded}</p>
        ) : (
          <p className="text-xs">检查后端连接、权限或 collection 配置</p>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      {detail.degraded && (
        <div className="flex-shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-[11px] text-amber-800">
          部分内省失败，数据可能不全:{detail.degraded}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <aside className={cn(
          'flex flex-shrink-0 flex-col border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] transition-all',
          leftCollapsed ? 'w-12' : 'w-72',
        )}>
          <div className="flex h-11 items-center gap-2 border-b border-[var(--cy-border-subtle)] px-2">
            <button
              type="button"
              onClick={() => setLeftCollapsed((v) => !v)}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
              title={leftCollapsed ? '展开集合清单' : '折叠集合清单'}
            >
              {leftCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
            {!leftCollapsed && (
              <div className="min-w-0 flex-1 text-xs font-medium text-[var(--cy-text-primary)]">
                集合清单 · 已选 {selectedNames.size}
              </div>
            )}
          </div>
          {leftCollapsed ? (
            <button
              type="button"
              onClick={() => setLeftCollapsed(false)}
              className="m-2 flex flex-1 items-center justify-center rounded-xl text-[11px] text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
              title="展开集合清单"
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
                    placeholder="搜索集合"
                    className="h-8 w-full rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-7 pr-3 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
                  />
                </div>
                <div className="flex items-center gap-1">
                  <button type="button" onClick={toggleVisibleAll} className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]">
                    <CheckSquare className="h-3 w-3" />
                    {visibleAllSelected ? '取消当前全选' : '全选当前'}
                  </button>
                  <button type="button" onClick={selectAll} className="rounded-full px-2 py-1 text-[11px] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]">
                    全库
                  </button>
                </div>
              </div>
              <ul className="flex-1 overflow-y-auto py-1">
                {filtered.map((c) => {
                  const checked = selectedNames.has(c.name);
                  return (
                    <li key={c.name}>
                      <div className={cn(
                        'group flex w-full items-center gap-2 px-2 py-1.5 text-xs transition-colors',
                        activeName === c.name
                          ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                          : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
                      )}>
                        <button
                          type="button"
                          onClick={() => toggleOne(c.name)}
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
                          onClick={() => selectOnly(c.name)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left"
                          title="点击查看数据并仅检索此集合"
                        >
                          <Database className="h-3 w-3 flex-shrink-0" />
                          <span className="min-w-0 flex-1 truncate font-medium">{c.name}</span>
                          {c.size != null && <span className="text-[10px] opacity-60">{compact(c.size)}</span>}
                        </button>
                      </div>
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

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--cy-surface-base)]">
          <header className="flex h-11 flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">向量库对话</p>
              <p className="truncate text-[11px] text-[var(--cy-text-tertiary)]">范围：{scopeText}</p>
            </div>
            <button type="button" onClick={() => setMessages([])} className="text-[11px] text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]">
              清空消息
            </button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full w-full flex-col items-center justify-center text-center text-sm text-[var(--cy-text-tertiary)]">
                <MessageSquare className="mb-3 h-8 w-8" />
                <p className="font-medium text-[var(--cy-text-secondary)]">在下方输入问题，直接对向量集合检索</p>
                <p className="mt-1 text-xs">左侧点击集合可联动右侧数据预览，并把对话范围限制到该集合。</p>
              </div>
            ) : (
              <div className="flex w-full flex-col gap-4">
                {messages.map((m) => (
                  <VectorMessageBubble key={m.id} message={m} onRetry={() => void submit(m.query)} />
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
              placeholder={`检索${scopeText}，例如：查找合同风险相关内容`}
              showRetrievalControls
            />
          </div>
        </section>

        <aside className={cn(
          'flex flex-shrink-0 flex-col border-l border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] transition-all',
          rightCollapsed ? 'w-12' : 'w-[420px]',
        )}>
          <div className={cn(
            'flex h-11 items-center gap-2 border-b border-[var(--cy-border-subtle)] px-2',
            rightCollapsed ? 'justify-end' : 'justify-between',
          )}>
            {!rightCollapsed && (
              <>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-[var(--cy-text-primary)]">{active?.name ?? '—'}</p>
                  <p className="truncate text-[10px] text-[var(--cy-text-tertiary)]">字段与检索样例</p>
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
            <VectorFields collection={active} />
          ) : active ? (
            <VectorRowsPane kuId={detail.ku_id} collection={active.name} />
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

const VectorMessageBubble: React.FC<{ message: VectorChatMessage; onRetry(): void }> = ({ message, onRetry }) => {
  const isUser = message.role === 'user';
  const scopeLabel = message.scopeNames.length ? `${message.scopeNames.length} 个集合` : '全部集合';
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn(
        'max-w-[92%] rounded-2xl px-3 py-2 text-sm shadow-sm',
        isUser
          ? 'bg-[var(--cy-ink-700)] text-white'
          : 'border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] text-[var(--cy-text-primary)]',
      )}>
        <div className={cn('mb-1 text-[10px]', isUser ? 'text-white/65' : 'text-[var(--cy-text-tertiary)]')}>
          {isUser ? `提问 · ${scopeLabel}` : message.status === 'running' ? '正在检索向量库...' : `向量结果 · ${scopeLabel}`}
        </div>
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.query}</p>
        ) : message.status === 'running' ? (
          <div className="inline-flex items-center gap-2 text-xs text-[var(--cy-text-secondary)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            正在按集合范围召回相似内容...
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

const VectorFields: React.FC<{ collection: VectorCollection | null }> = ({ collection }) => {
  const fields = [
    ['score', 'number', '相似度分数，越大越相关'],
    ['content', 'text', '向量库中可读文本片段'],
    ['title', 'text', '来源标题或文件名'],
    ['collection', 'text', '当前命中的集合名称'],
    ['meta.*', 'metadata', '原始向量库元数据字段'],
  ];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="border-b border-[var(--cy-border-subtle)] px-4 py-2 text-[11px] text-[var(--cy-text-tertiary)]">
        {collection?.dim != null ? `维度 ${collection.dim}` : '维度未知'}
        {collection?.metric ? ` · ${collection.metric}` : ''}
        {collection?.size != null ? ` · ${compact(collection.size)} 条` : ''}
      </div>
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-[1] bg-[var(--cy-surface-1)] text-xs text-[var(--cy-text-tertiary)]">
          <tr>
            <th className="w-8 px-3 py-2"></th>
            <th className="px-2 py-2 text-left font-medium">字段</th>
            <th className="px-2 py-2 text-left font-medium">类型</th>
            <th className="px-2 py-2 text-left font-medium">说明</th>
          </tr>
        </thead>
        <tbody>
          {fields.map(([name, type, comment]) => (
            <tr key={name} className="border-b border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]">
              <td className="px-3 py-1.5"><Hash className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" /></td>
              <td className="px-2 py-1.5 font-medium">{name}</td>
              <td className="px-2 py-1.5 text-xs text-[var(--cy-text-secondary)]">
                <span className="inline-flex items-center gap-1 rounded bg-[var(--cy-surface-2)] px-1.5 py-0.5 font-mono">
                  <Type className="h-3 w-3 opacity-60" />
                  {type}
                </span>
              </td>
              <td className="px-2 py-1.5 text-xs text-[var(--cy-text-tertiary)]">{comment}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const PAGE_SIZE = 10;

const VectorRowsPane: React.FC<{ kuId: string; collection: string }> = ({ kuId, collection }) => {
  const [query, setQuery] = React.useState(collection);
  const [data, setData] = React.useState<VectorRowsResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<Error | null>(null);
  const acRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    setQuery(collection);
    setData(null);
    setErr(null);
  }, [collection]);

  const load = React.useCallback(async (nextQuery?: string) => {
    acRef.current?.abort();
    const ac = new AbortController();
    acRef.current = ac;
    setLoading(true);
    setErr(null);
    try {
      const r = await knowledgeUniverse.fetchVectorRows({
        ku_id: kuId,
        collection,
        query: (nextQuery ?? query).trim() || collection,
        page: 1,
        page_size: PAGE_SIZE,
      });
      if (ac.signal.aborted) return;
      setData(r);
    } catch (e) {
      if (ac.signal.aborted) return;
      const error = e as Error;
      setErr(error);
      reportError(error, '拉取向量集合样例失败');
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, [collection, kuId, query]);

  React.useEffect(() => {
    void load(collection);
    return () => acRef.current?.abort();
  }, [collection, kuId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/60 px-4 py-2">
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void load(); }}
            placeholder="输入预览检索词"
            className="h-8 min-w-0 flex-1 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
          />
          <Button size="icon" variant="ghost" onClick={() => void load()} disabled={loading} aria-label="刷新" className="h-8 w-8">
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </Button>
        </div>
        <p className="text-[11px] text-[var(--cy-text-tertiary)]">
          向量库通常不支持直接分页扫描，这里展示按检索词召回的 Top {PAGE_SIZE} 样例。
        </p>
      </div>
      {loading && !data ? (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          正在检索样例...
        </div>
      ) : err ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-red-600">{err.message}</div>
      ) : data && data.rows.length > 0 ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 z-[1] bg-[var(--cy-surface-1)] text-[var(--cy-text-tertiary)]">
              <tr>
                {data.columns.map((c) => <th key={c} className="whitespace-nowrap px-2 py-2 text-left font-medium">{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, idx) => (
                <tr key={`${collection}-${idx}`} className="border-b border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]">
                  {row.map((v, i) => (
                    <td key={`${collection}-${idx}-${i}`} className="max-w-[220px] truncate px-2 py-1.5" title={String(v ?? '')}>
                      {formatCell(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--cy-text-tertiary)]">没有检索到样例数据</div>
      )}
    </div>
  );
};

function formatCell(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function compact(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}
