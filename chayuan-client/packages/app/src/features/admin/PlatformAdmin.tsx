/**
 * Admin · 模型平台
 *
 * 列出 server 全部 MODEL_PLATFORMS(包括禁用的),允许 admin 在线翻转 enabled
 * + 维护 disabled_models 黑名单。改动持久化到 server 的 platform_overrides.json。
 *
 * 后端契约:
 *   GET   /admin/model_platforms                → { code, msg, data: PlatformRow[] }
 *   PATCH /admin/model_platforms/{platform_name} { enabled?, disabled_models? }
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Plug, Power, Trash2, X } from 'lucide-react';
import { Button, cn, Switch } from '@chayuan/ui';
import { request } from '@chayuan/api';
import { reportError, notifySuccess } from '../../store/errorDialog';

interface PlatformRow {
  platform_name: string;
  platform_type: string;
  api_base_url: string;
  enabled: boolean;
  auto_detect_model: boolean;
  llm_models: string[];
  embed_models: string[];
  disabled_models: string[];
  _overridden_fields: string[];
}

const KEY = ['admin', 'model_platforms'] as const;

async function listPlatforms(): Promise<PlatformRow[]> {
  const r = await request<{ data: PlatformRow[] }>('/admin/model_platforms');
  return r.data?.data ?? [];
}

async function patchPlatform(
  name: string,
  patch: { enabled?: boolean; disabled_models?: string[] },
): Promise<void> {
  await request(`/admin/model_platforms/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: patch,
  });
}

export const PlatformAdmin: React.FC = () => {
  const qc = useQueryClient();
  const list = useQuery<PlatformRow[]>({
    queryKey: KEY,
    queryFn: listPlatforms,
    staleTime: 30 * 1000,
  });

  const patchMut = useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: Parameters<typeof patchPlatform>[1] }) =>
      patchPlatform(name, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      // /v1/models 也跟着变,顺手 invalidate
      void qc.invalidateQueries({ queryKey: ['v1.models'] });
    },
    onError: (e: unknown) => reportError(e, '更新平台失败'),
  });

  const onToggleEnabled = (row: PlatformRow, next: boolean) => {
    patchMut.mutate(
      { name: row.platform_name, patch: { enabled: next } },
      {
        onSuccess: () =>
          notifySuccess(`${row.platform_name} 已${next ? '启用' : '禁用'}`),
      },
    );
  };

  const onToggleModelBlacklist = (row: PlatformRow, model: string) => {
    const cur = new Set(row.disabled_models);
    if (cur.has(model)) cur.delete(model);
    else cur.add(model);
    patchMut.mutate({
      name: row.platform_name,
      patch: { disabled_models: [...cur] },
    });
  };

  if (list.isLoading) return <div className="p-6 text-xs text-muted-foreground">加载中…</div>;
  if (list.isError)
    return (
      <div className="p-6">
        <p className="text-sm text-red-600">加载平台列表失败</p>
        <Button size="sm" variant="outline" className="mt-2" onClick={() => list.refetch()}>
          重试
        </Button>
      </div>
    );

  const data = list.data ?? [];
  if (data.length === 0)
    return <div className="p-6 text-xs text-muted-foreground">没有配置任何平台</div>;

  return (
    <div className="space-y-3 p-6">
      <p className="text-xs text-muted-foreground">
        ⚠️ 改动会立即生效并持久化到 <code>platform_overrides.json</code>。禁用的厂商
        所有模型会从 <code>/v1/models</code> 消失,前端模型选择器同步刷新。
      </p>
      {data.map((row) => (
        <PlatformCard
          key={row.platform_name}
          row={row}
          busy={patchMut.isPending}
          onToggleEnabled={onToggleEnabled}
          onToggleModel={onToggleModelBlacklist}
        />
      ))}
    </div>
  );
};

const PlatformCard: React.FC<{
  row: PlatformRow;
  busy: boolean;
  onToggleEnabled(row: PlatformRow, next: boolean): void;
  onToggleModel(row: PlatformRow, model: string): void;
}> = ({ row, busy, onToggleEnabled, onToggleModel }) => {
  const allModels = React.useMemo(() => {
    // 合并展示 llm + embed,标颜色区分
    const out: Array<{ name: string; kind: 'llm' | 'embed' }> = [];
    for (const n of row.llm_models) out.push({ name: n, kind: 'llm' });
    for (const n of row.embed_models) out.push({ name: n, kind: 'embed' });
    return out;
  }, [row.llm_models, row.embed_models]);
  const blacklist = React.useMemo(() => new Set(row.disabled_models), [row.disabled_models]);

  return (
    <div
      className={cn(
        'rounded-xl border bg-card p-4',
        row.enabled ? 'border-border' : 'border-dashed border-muted-foreground/30 opacity-70',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">{row.platform_name}</h3>
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase">
              {row.platform_type}
            </span>
            {row.auto_detect_model && (
              <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] text-blue-600">
                auto-detect
              </span>
            )}
            {row._overridden_fields.length > 0 && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">
                overridden
              </span>
            )}
          </div>
          <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
            {row.api_base_url}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs">
          <Power className="h-3.5 w-3.5 text-muted-foreground" />
          <Switch
            checked={row.enabled}
            disabled={busy}
            onCheckedChange={(v) => onToggleEnabled(row, v)}
          />
        </label>
      </div>

      {allModels.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {allModels.map(({ name, kind }) => {
            const banned = blacklist.has(name);
            return (
              <button
                key={`${kind}::${name}`}
                type="button"
                disabled={busy}
                onClick={() => onToggleModel(row, name)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                  banned
                    ? 'border-red-200 bg-red-50 text-red-600 line-through'
                    : kind === 'embed'
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                      : 'border-border bg-background text-foreground hover:bg-muted',
                  busy && 'cursor-not-allowed opacity-60',
                )}
                title={banned ? '点击恢复显示' : '点击拉黑(disabled_models)'}
              >
                {banned ? <Trash2 className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                {name}
              </button>
            );
          })}
        </div>
      )}

      {row.disabled_models.length > 0 && (
        <p className="mt-2 text-[10px] text-muted-foreground">
          黑名单:{row.disabled_models.length} 个 — 点击恢复
        </p>
      )}
    </div>
  );
};
