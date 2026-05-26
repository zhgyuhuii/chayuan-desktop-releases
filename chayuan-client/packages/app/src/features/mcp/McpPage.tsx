/**
 * MCP 连接管理页（/mcp）。
 *
 * - 列表：当前所有连接 + enable / disable / delete；
 * - 新建对话框：server_name + args + transport + env；
 * - 详情用 dialog 而非独立页（连接数一般 ≤ 20）。
 *
 * 性能：单 query；CRUD 走 useMutation 乐观更新；invalidate 仅刷自己。
 */

import * as React from 'react';
import { Plus, Trash2, Power, PowerOff } from 'lucide-react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Button,
  cn,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from '@chayuan/ui';
import { mcp, type McpConnection, type McpConnectionInput } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';

const QK = ['mcp', 'list-all'] as const;

export const McpPage: React.FC = () => {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: QK,
    queryFn: () => mcp.list({ limit: 100 }),
    staleTime: 30_000,
    retry: 0,
  });
  const enableM = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? mcp.enable(id) : mcp.disable(id),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: QK });
      const prev = qc.getQueryData<McpConnection[]>(QK);
      qc.setQueryData<McpConnection[]>(QK, (cur) => cur?.map((c) => (c.id === id ? { ...c, enabled } : c)) ?? []);
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(QK, ctx.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: QK }),
  });
  const deleteM = useMutation({
    mutationFn: (id: string) => mcp.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK }),
  });
  const [editing, setEditing] = React.useState<McpConnectionInput | null>(null);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b p-3">
        <div>
          <h2 className="text-base font-semibold">MCP 连接</h2>
          <p className="text-xs text-muted-foreground">{list.data?.length ?? 0} 个</p>
        </div>
        <Button
          size="sm"
          onClick={() =>
            setEditing({ server_name: '', args: [], env: {}, transport: 'stdio', timeout: 30, enabled: true })
          }
        >
          <Plus className="h-3.5 w-3.5" />
          新建
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto p-3">
        {list.isLoading && <div className="text-sm text-muted-foreground">加载中…</div>}
        {!list.isLoading && (list.data?.length ?? 0) === 0 && (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            还没有 MCP 连接。点右上角「新建」添加一个。
          </div>
        )}
        <div className="space-y-2">
          {list.data?.map((c) => (
            <div
              key={c.id}
              className={cn(
                'flex items-start justify-between gap-3 rounded-lg border p-3',
                c.enabled ? 'border-emerald-300/60 bg-emerald-50/30 dark:bg-emerald-950/20' : '',
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{c.server_name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">{c.transport}</span>
                </div>
                <p className="line-clamp-1 text-xs text-muted-foreground">
                  {c.description || c.args.join(' ') || '—'}
                </p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">超时 {c.timeout}s · {c.id.slice(0, 8)}</p>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => enableM.mutate({ id: c.id, enabled: !c.enabled })}
                  title={c.enabled ? '禁用' : '启用'}
                >
                  {c.enabled ? <PowerOff className="h-3.5 w-3.5" /> : <Power className="h-3.5 w-3.5" />}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-destructive"
                  onClick={async () => {
                    const ok = await getPlatform().dialog.confirm(
                      `删除连接「${c.server_name}」?`,
                      { title: '删除 MCP 连接' },
                    );
                    if (ok) deleteM.mutate(c.id);
                  }}
                  title="删除"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <McpEditor open={!!editing} initial={editing} onClose={() => setEditing(null)} onSaved={() => qc.invalidateQueries({ queryKey: QK })} />
    </div>
  );
};

const McpEditor: React.FC<{
  open: boolean;
  initial: McpConnectionInput | null;
  onClose(): void;
  onSaved(): void;
}> = ({ open, initial, onClose, onSaved }) => {
  const [draft, setDraft] = React.useState<McpConnectionInput>(
    initial ?? { server_name: '', args: [], env: {}, transport: 'stdio', timeout: 30, enabled: true },
  );
  React.useEffect(() => {
    if (initial) setDraft(initial);
  }, [initial]);

  const save = useMutation({
    mutationFn: (body: McpConnectionInput) => mcp.create(body),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : null)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>新建 MCP 连接</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="服务名">
            <Input value={draft.server_name} onChange={(e) => setDraft({ ...draft, server_name: e.target.value })} className="h-8 text-xs" />
          </Field>
          <Field label="启动参数（空格分隔）">
            <Input
              value={(draft.args ?? []).join(' ')}
              onChange={(e) => setDraft({ ...draft, args: e.target.value.split(/\s+/).filter(Boolean) })}
              placeholder="npx mcp-everything"
              className="h-8 text-xs"
            />
          </Field>
          <Field label="Transport">
            <select
              value={draft.transport ?? 'stdio'}
              onChange={(e) => setDraft({ ...draft, transport: e.target.value as 'stdio' | 'sse' })}
              className="h-8 rounded-md border bg-background px-2 text-xs"
            >
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
            </select>
          </Field>
          <Field label="超时（秒）">
            <Input
              type="number"
              value={draft.timeout ?? 30}
              onChange={(e) => setDraft({ ...draft, timeout: Number.parseInt(e.target.value, 10) || 30 })}
              className="h-8 text-xs"
              min={1}
              max={300}
            />
          </Field>
          <Field label="描述">
            <Input
              value={draft.description ?? ''}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              className="h-8 text-xs"
            />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button onClick={() => save.mutate(draft)} disabled={!draft.server_name.trim() || save.isPending}>
            {save.isPending ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label className="block space-y-1 text-xs">
    <span className="font-medium text-muted-foreground">{label}</span>
    {children}
  </label>
);
