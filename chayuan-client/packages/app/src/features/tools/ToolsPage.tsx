/**
 * 工具市场（/tools）。
 *
 * - 列出全部工具（含未启用）；
 * - 搜索 + 分组（按名称前缀简单分组）；
 * - 点击 → 在 composer 选中（只读，不直接启用：实际启用需后端配置中心）。
 *
 * 性能：
 * - useQuery 缓存 60s；search 在内存里 O(n)，N 一般 < 100。
 * - virtualization 在 N > 100 时再加。
 */

import * as React from 'react';
import { Search, Wrench, CheckCircle2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { tools as toolsApi, type ToolInfo } from '@chayuan/api';
import { Button, Input, cn } from '@chayuan/ui';
import { useComposerStore } from '../../store/composer';

const QK = ['tools', 'all'] as const;

export const ToolsPage: React.FC = () => {
  const q = useQuery({
    queryKey: QK,
    queryFn: () => toolsApi.listAll(),
    staleTime: 60_000,
    retry: 0,
  });
  const [filter, setFilter] = React.useState('');
  const selected = useComposerStore((s) => s.selectedTools);
  const setTools = useComposerStore((s) => s.setTools);

  const items = React.useMemo(() => {
    const arr = q.data ?? [];
    if (!filter.trim()) return arr;
    const lower = filter.toLowerCase();
    return arr.filter(
      (t) => t.name.toLowerCase().includes(lower) || (t.title ?? '').toLowerCase().includes(lower) || (t.description ?? '').toLowerCase().includes(lower),
    );
  }, [q.data, filter]);

  const toggle = (name: string) => {
    setTools(selected.includes(name) ? selected.filter((x) => x !== name) : [...selected, name]);
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b p-3">
        <div>
          <h2 className="text-base font-semibold">工具市场</h2>
          <p className="text-xs text-muted-foreground">
            选中后将在下次对话生效；可在 Composer 顶部能力卡确认
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索工具"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-8 pl-7 text-xs"
            />
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-3">
        {q.isLoading && <div className="text-sm text-muted-foreground">加载中…</div>}
        {!q.isLoading && items.length === 0 && (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            没有匹配工具
          </div>
        )}
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((t) => (
            <ToolCard key={t.name} tool={t} selected={selected.includes(t.name)} onToggle={() => toggle(t.name)} />
          ))}
        </div>
      </div>

      {selected.length > 0 && (
        <footer className="flex items-center justify-between border-t bg-muted/30 px-4 py-2 text-xs">
          <span>已选 {selected.length} 个工具</span>
          <Button size="sm" variant="ghost" onClick={() => setTools([])}>
            清空
          </Button>
        </footer>
      )}
    </div>
  );
};

const ToolCard: React.FC<{ tool: ToolInfo; selected: boolean; onToggle: () => void }> = ({
  tool,
  selected,
  onToggle,
}) => (
  <button
    type="button"
    onClick={onToggle}
    className={cn(
      'group relative flex flex-col items-start gap-1 rounded-lg border bg-card p-3 text-left transition-all',
      'hover:border-primary/50 hover:shadow-sm',
      selected && 'border-primary bg-primary/5',
    )}
  >
    <div className="flex w-full items-center gap-2">
      <Wrench className="h-4 w-4 text-muted-foreground" />
      <span className="truncate text-sm font-medium">{tool.title || tool.name}</span>
      {selected && <CheckCircle2 className="ml-auto h-4 w-4 text-primary" />}
    </div>
    <p className="line-clamp-2 text-xs text-muted-foreground">{tool.description || '—'}</p>
    <code className="mt-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">{tool.name}</code>
  </button>
);
