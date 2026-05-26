/**
 * MountDetailDrawer —— 点击行后右侧抽屉,展示 mount 详情 + 预览样本 + artifact。
 */
import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, X } from 'lucide-react';
import { Button } from '@chayuan/ui';
import { dataMountsApi } from '@chayuan/api';

export const MountDetailDrawer: React.FC<{
  mountId: string;
  onClose(): void;
}> = ({ mountId, onClose }) => {
  const previewQuery = useQuery({
    queryKey: ['dataMounts.preview', mountId],
    queryFn: () => dataMountsApi.preview(mountId),
    staleTime: 0,
  });
  const detail = previewQuery.data;
  const mount = detail?.mount;

  return (
    <div className="fixed inset-y-0 right-0 z-40 flex w-[min(640px,96vw)] flex-col border-l border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] shadow-xl">
      <div className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] px-4 py-3">
        <div>
          <div className="text-sm font-semibold">{mount?.name ?? mountId}</div>
          <div className="text-[11px] text-[var(--cy-text-tertiary)]">{mountId}</div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs">
        {previewQuery.isLoading && (
          <div className="flex items-center gap-2 text-[var(--cy-text-tertiary)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 物化预览中...
          </div>
        )}
        {detail?.error && (
          <div className="mb-2 rounded bg-rose-50 p-2 text-rose-700">{detail.error}</div>
        )}
        {mount && (
          <section className="mb-3 space-y-1">
            <div><span className="text-[var(--cy-text-tertiary)]">状态: </span>{mount.status}</div>
            <div><span className="text-[var(--cy-text-tertiary)]">范围: </span>{mount.scope_type}{mount.scope_id ? `/${mount.scope_id}` : ''}</div>
            <div><span className="text-[var(--cy-text-tertiary)]">模式: </span>{mount.mount_modes.join(' · ')}</div>
            <div><span className="text-[var(--cy-text-tertiary)]">条数上限: </span>{mount.max_items} · token 上限 {mount.max_tokens}</div>
          </section>
        )}

        {detail?.fields && detail.fields.length > 0 && (
          <section className="mb-3">
            <h4 className="mb-1 font-semibold">字段 schema</h4>
            <div className="space-y-1">
              {detail.fields.map((f) => (
                <div key={f.name} className="rounded border border-[var(--cy-border-subtle)] p-2">
                  <div className="font-mono text-[var(--cy-text-primary)]">{f.name}</div>
                  <div className="text-[var(--cy-text-tertiary)]">{f.type} · 非空率 {(f.fill_rate * 100).toFixed(0)}% · {f.unique_count} unique</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {detail?.preview_records && detail.preview_records.length > 0 && (
          <section className="mb-3">
            <h4 className="mb-1 font-semibold">样本预览(前 {Math.min(detail.preview_records.length, 10)} 条)</h4>
            <div className="space-y-2">
              {detail.preview_records.slice(0, 10).map((it, i) => (
                <div key={i} className="rounded border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2">
                  <div className="line-clamp-3">{it.text}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {detail?.artifacts && detail.artifacts.length > 0 && (
          <section className="mb-3">
            <h4 className="mb-1 font-semibold">物化产物 (artifact)</h4>
            <div className="space-y-2">
              {detail.artifacts.map((a, i) => (
                <div key={i} className="rounded border border-[var(--cy-border-subtle)] p-2">
                  <div className="font-mono text-[var(--cy-text-primary)]">{a.artifact_type}</div>
                  <div className="mt-1 text-[var(--cy-text-tertiary)]">
                    stats: {JSON.stringify(a.stats ?? {})}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
};
