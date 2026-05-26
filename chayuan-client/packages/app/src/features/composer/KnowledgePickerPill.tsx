import { knowledgeUniverse, type KuItem } from '@chayuan/api';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger, Pill, cn } from '@chayuan/ui';
import { useQuery } from '@tanstack/react-query';
import { Check, Library, Search, X } from 'lucide-react';
import * as React from 'react';
import { useComposerStore, useComposerS } from '../../store/composer';

export interface KnowledgePickerPillProps {
  iconOnly?: boolean;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
  contentClassName?: string;
  includeKinds?: Array<KuItem['kind']>;
}

export const KnowledgePickerPill: React.FC<KnowledgePickerPillProps> = ({
  iconOnly = false,
  side = 'bottom',
  align = 'start',
  contentClassName,
  includeKinds,
}) => {
  const selectedKuIds = useComposerS((s) => s.selectedKuIds);
  const setKuIds = useComposerS((s) => s.setKuIds);
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const { data = [], isLoading } = useQuery<KuItem[]>({
    queryKey: ['ku.list', 'composer-picker'],
    queryFn: () => knowledgeUniverse.list(),
    staleTime: 60 * 1000,
  });

  const allowed = React.useMemo(() => new Set(includeKinds ?? []), [includeKinds]);
  const available = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return data
      .filter((item) => !includeKinds?.length || allowed.has(item.kind))
      .filter((item) => {
        if (!q) return true;
        return `${item.display_name} ${item.name} ${item.description ?? ''} ${item.kind}`.toLowerCase().includes(q);
      });
  }, [allowed, data, includeKinds?.length, query]);

  const selected = React.useMemo(() => new Set(selectedKuIds), [selectedKuIds]);
  const visibleAllSelected = available.length > 0 && available.every((item) => selected.has(item.ku_id));
  const visibleSelectedCount = React.useMemo(
    () => available.reduce((n, item) => n + (selected.has(item.ku_id) ? 1 : 0), 0),
    [available, selected],
  );
  const visibleSomeSelected = visibleSelectedCount > 0 && !visibleAllSelected;
  const byId = React.useMemo(() => new Map(data.map((item) => [item.ku_id, item])), [data]);
  const selectedNames = React.useMemo(
    () => selectedKuIds
      .map((id) => byId.get(id)?.display_name || id.replace(/^doc:/, '').replace(/^src:/, '源 '))
      .slice(0, 4)
      .join('、'),
    [byId, selectedKuIds],
  );

  const toggle = (id: string) => {
    setKuIds(selected.has(id) ? selectedKuIds.filter((x) => x !== id) : [...selectedKuIds, id]);
  };
  const selectVisibleAll = () => {
    const visibleIds = available.map((item) => item.ku_id);
    const keepHidden = selectedKuIds.filter((id) => !visibleIds.includes(id));
    setKuIds(visibleAllSelected ? keepHidden : Array.from(new Set([...keepHidden, ...visibleIds])));
  };

  const title = selectedKuIds.length
    ? `已选 ${selectedKuIds.length} 个知识库${selectedNames ? `：${selectedNames}` : ''}`
    : '选择知识库';

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Pill
          tone={selectedKuIds.length ? 'ink' : 'outline'}
          size="sm"
          aria-label="选择知识库"
          title={title}
          className={cn(
            'shrink-0',
            iconOnly
              ? 'h-8 w-8 justify-center px-0'
              : 'max-[560px]:h-8 max-[560px]:w-8 max-[560px]:justify-center max-[560px]:px-0',
          )}
        >
          <Library className="h-3.5 w-3.5" />
          {!iconOnly && (
            <span className="max-w-[120px] truncate max-[560px]:hidden">
              {selectedKuIds.length ? `知识库 ${selectedKuIds.length}` : '知识库'}
            </span>
          )}
        </Pill>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={align}
        side={side}
        sideOffset={8}
        collisionPadding={12}
        className={cn('w-80 overflow-hidden p-0', contentClassName)}
      >
        <div className="border-b border-[var(--cy-border-subtle)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-[var(--cy-text-primary)]">选择知识库</div>
              <div className="text-[11px] text-[var(--cy-text-tertiary)]">
                可多选、全选；未选择时按普通对话处理
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={selectVisibleAll}
                disabled={available.length === 0}
                className="inline-flex h-7 items-center gap-1.5 rounded-full px-2 text-[11px] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] disabled:opacity-50"
                aria-checked={visibleAllSelected ? true : visibleSomeSelected ? 'mixed' : false}
                role="checkbox"
                title={visibleAllSelected ? '取消当前结果全选' : '全选当前结果'}
              >
                <span className={cn(
                  'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border',
                  visibleAllSelected || visibleSomeSelected
                    ? 'border-cyan-500 bg-cyan-500 text-white'
                    : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]',
                )}>
                  {visibleAllSelected ? <Check className="h-2.5 w-2.5" /> : visibleSomeSelected ? <span className="h-0.5 w-2 rounded bg-white" /> : null}
                </span>
                <span className="w-8 text-left">全选</span>
              </button>
              {selectedKuIds.length > 0 && (
                <button
                  type="button"
                  onClick={() => setKuIds([])}
                  className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
                >
                  <X className="h-3 w-3" />
                  清空
                </button>
              )}
            </div>
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索知识库名称、描述"
              className="h-8 w-full rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-8 pr-3 text-xs text-[var(--cy-text-primary)] outline-none focus:border-cyan-400"
            />
          </div>
          {selectedKuIds.length > 0 && (
            <div className="mt-2 truncate text-[11px] text-cyan-700" title={selectedNames}>
              已选：{selectedNames || `${selectedKuIds.length} 个知识库`}
            </div>
          )}
        </div>
        <div className="max-h-[280px] overflow-y-auto p-2">
          {isLoading ? (
            <div className="px-3 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">正在加载知识库...</div>
          ) : available.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">没有匹配的知识库</div>
          ) : (
            available.map((item) => {
              const checked = selected.has(item.ku_id);
              return (
                <button
                  key={item.ku_id}
                  type="button"
                  onClick={() => toggle(item.ku_id)}
                  className={cn(
                    'flex w-full items-start gap-2 rounded-xl px-2 py-2 text-left transition-colors',
                    checked ? 'bg-cyan-50 text-cyan-900' : 'hover:bg-[var(--cy-surface-2)]',
                  )}
                >
                  <span className={cn(
                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                    checked ? 'border-cyan-500 bg-cyan-500 text-white' : 'border-[var(--cy-border-subtle)]',
                  )}>
                    {checked && <Check className="h-3 w-3" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-[var(--cy-text-primary)]">{item.display_name}</span>
                    <span className="block truncate text-[11px] text-[var(--cy-text-tertiary)]">
                      {knowledgeKindLabel(item.kind)} · {item.count ?? 0} 条 · {item.description || '无描述'}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

function knowledgeKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    document: '文档库',
    structured: '结构化库',
    image: '图像库',
    vector: '向量库',
  };
  return labels[kind] ?? kind;
}
