/**
 * MCP 市场页(/mcp)—— 卡片版式,跟 KbBoard 范式对齐。
 *
 * 顶栏:统计 / 搜索 / + 新建
 * 中部:卡片网格(McpCard);空态有引导卡
 * 行为:卡片角落 toggle 启用/禁用;卡片主体点击 → 打开编辑 dialog
 */

import * as React from 'react';
import { Loader2, Plug, Plus, Search } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Input } from '@chayuan/ui';
import { mcp, type McpConnection, type McpConnectionInput } from '@chayuan/api';
import { McpCard } from './McpCard';
import { McpEditDialog } from './McpEditDialog';
import { reportError, notifySuccess } from '../../store/errorDialog';

const QK = ['mcp', 'list-all'] as const;

export const McpBoard: React.FC = () => {
  const qc = useQueryClient();
  const [keyword, setKeyword] = React.useState('');
  const [editing, setEditing] = React.useState<{
    mode: 'create' | 'edit';
    seed?: McpConnection;
  } | null>(null);

  const list = useQuery({
    queryKey: QK,
    queryFn: () => mcp.list({ limit: 200 }),
    staleTime: 30_000,
    retry: 0,
  });

  const enableM = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? mcp.enable(id) : mcp.disable(id),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: QK });
      const prev = qc.getQueryData<McpConnection[]>(QK);
      qc.setQueryData<McpConnection[]>(QK, (cur) =>
        cur?.map((c) => (c.id === id ? { ...c, enabled } : c)) ?? [],
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      ctx?.prev && qc.setQueryData(QK, ctx.prev);
      reportError(e, '切换启用失败');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });

  const filtered = React.useMemo(() => {
    const arr = list.data ?? [];
    if (!keyword.trim()) return arr;
    const q = keyword.toLowerCase();
    return arr.filter(
      (c) =>
        c.server_name.toLowerCase().includes(q) ||
        (c.description ?? '').toLowerCase().includes(q) ||
        c.transport.toLowerCase().includes(q) ||
        c.args.some((a) => a.toLowerCase().includes(q)),
    );
  }, [list.data, keyword]);

  return (
    <div className="mx-auto flex h-full w-full max-w-7xl flex-col px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-[var(--cy-text-primary)]">
            <Plug className="h-5 w-5 text-violet-600" /> MCP 连接
          </h1>
          <p className="mt-0.5 text-xs text-[var(--cy-text-tertiary)]">
            通过 Model Context Protocol 把外部进程、SSE 服务暴露成 LLM 可调用的工具集
            · 共 {list.data?.length ?? 0} 个 · 已启用 {(list.data ?? []).filter((c) => c.enabled).length}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索 server / args / transport"
              className="h-8 w-56 rounded-full pl-7 text-xs"
            />
          </div>
          <Button
            size="sm"
            onClick={() =>
              setEditing({
                mode: 'create',
              })
            }
          >
            <Plus className="mr-1 h-3.5 w-3.5" /> 新建
          </Button>
        </div>
      </header>

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
        {list.isLoading ? (
          <div className="flex h-32 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : list.isError ? (
          <ErrorState error={list.error as Error} onRetry={() => list.refetch()} />
        ) : filtered.length === 0 ? (
          <EmptyState
            filtered={!!keyword}
            onCreate={() => setEditing({ mode: 'create' })}
          />
        ) : (
          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}
          >
            {filtered.map((c, i) => (
              <McpCard
                key={c.id}
                conn={c}
                index={i}
                onOpen={() => setEditing({ mode: 'edit', seed: c })}
                onToggleEnabled={(enabled) => enableM.mutate({ id: c.id, enabled })}
              />
            ))}
          </div>
        )}
      </div>

      <McpEditDialog
        mode={editing?.mode}
        seed={editing?.seed}
        open={editing != null}
        onOpenChange={(o) => !o && setEditing(null)}
        onSaved={(saved, action) => {
          notifySuccess(action === 'create' ? '已新建 MCP 连接' : '已保存修改');
          void qc.invalidateQueries({ queryKey: QK });
          if (saved) setEditing(null);
        }}
      />

      <style>{
        '@keyframes mpCardIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}'
      }</style>
    </div>
  );
};

const EmptyState: React.FC<{ filtered: boolean; onCreate(): void }> = ({ filtered, onCreate }) => (
  <div className="flex h-full min-h-[260px] flex-col items-center justify-center gap-3 text-center">
    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
      <Plug className="h-7 w-7" />
    </div>
    <p className="text-sm font-medium text-[var(--cy-text-primary)]">
      {filtered ? '没有匹配的 MCP 连接' : '还没有 MCP 连接'}
    </p>
    <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">
      MCP(Model Context Protocol)是把本地命令 / 远端 SSE 服务封装为 LLM 工具集的开放协议。
      新建后会在对话时作为可调用工具组出现。
    </p>
    {!filtered && (
      <Button size="sm" onClick={onCreate}>
        <Plus className="mr-1 h-3.5 w-3.5" /> 新建第一个 MCP 连接
      </Button>
    )}
  </div>
);

const ErrorState: React.FC<{ error: Error; onRetry(): void }> = ({ error, onRetry }) => (
  <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 text-center">
    <p className="text-sm font-medium text-[var(--cy-text-primary)]">加载失败</p>
    <p className="max-w-md break-words text-xs text-[var(--cy-text-secondary)]">{error.message}</p>
    <Button size="sm" variant="outline" onClick={onRetry}>
      重试
    </Button>
  </div>
);
