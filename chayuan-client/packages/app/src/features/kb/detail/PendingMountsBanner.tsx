/**
 * PendingMountsBanner —— KB 详情页顶部:有待 ingest 的 corpus_pending 任务时显示。
 *
 * 数据流:
 *   /knowledge_base/{kbName}/pending_mounts → list of {artifact + mount + item_count}
 *   - 「确认入库」按钮  → POST .../{aid}/confirm   → 真 ingest 到 KB,artifact 标 disabled
 *   - 「拒绝」按钮      → POST .../{aid}/reject    → 仅标 disabled
 *
 * UE:
 *   * 折叠态:只显示总数;展开后显示 mount 名/源/条数 + 双按钮
 *   * 列表为空时不渲染(零侵入)
 *   * 任何 API 失败都静默 toast,不阻塞主体 KB 浏览
 */
import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Database, Loader2, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { kbPendingMountsApi, type PendingMount } from '@chayuan/api';
import { reportError } from '../../../store/errorDialog';

export const PendingMountsBanner: React.FC<{ kbName: string }> = ({ kbName }) => {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);

  const listQ = useQuery({
    queryKey: ['kb.pending_mounts', kbName],
    queryFn: () => kbPendingMountsApi.list(kbName),
    enabled: !!kbName,
    staleTime: 30_000,
    retry: false,
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['kb.pending_mounts', kbName] });
    void qc.invalidateQueries({ queryKey: ['ku.detail'] });
  };

  const confirmMut = useMutation({
    mutationFn: (artifactId: string) => kbPendingMountsApi.confirm(kbName, artifactId),
    onSuccess: invalidate,
    onError: (e) => reportError(e, '确认入库失败'),
  });

  const rejectMut = useMutation({
    mutationFn: (artifactId: string) => kbPendingMountsApi.reject(kbName, artifactId, ''),
    onSuccess: invalidate,
    onError: (e) => reportError(e, '拒绝失败'),
  });

  const items = listQ.data?.items ?? [];
  if (!items.length) return null;

  return (
    <div className="border-b border-[var(--cy-warning-500)] bg-[var(--cy-warning-50)] px-4 py-2 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
      >
        <Database className="h-3.5 w-3.5 text-[var(--cy-warning-700)]" />
        <span className="font-medium text-[var(--cy-warning-700)]">
          {items.length} 个数据挂载等待 ingest 到 {kbName}
        </span>
        <span className="ml-auto text-[var(--cy-text-tertiary)]">
          {expanded ? '收起 ▴' : '展开 ▾'}
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2">
          {items.map((it) => (
            <PendingMountRow
              key={it.id}
              item={it}
              busy={confirmMut.isPending || rejectMut.isPending}
              onConfirm={() => confirmMut.mutate(it.id)}
              onReject={() => rejectMut.mutate(it.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const PendingMountRow: React.FC<{
  item: PendingMount;
  busy: boolean;
  onConfirm(): void;
  onReject(): void;
}> = ({ item, busy, onConfirm, onReject }) => {
  const sourceType =
    ((item.payload as { source_type?: string } | null)?.source_type) ?? 'unknown';
  return (
    <div className="flex items-center gap-3 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2">
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-[var(--cy-text-primary)]">
          {item.mount?.name ?? item.mount_id}
        </div>
        <div className="mt-0.5 truncate text-[10px] text-[var(--cy-text-tertiary)]">
          source: {sourceType} · {item.item_count} 条 · v{item.version}
        </div>
      </div>
      <Button
        size="sm"
        onClick={onConfirm}
        disabled={busy}
        className="h-7"
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
        确认入库
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={onReject}
        disabled={busy}
        className={cn('h-7')}
      >
        <X className="h-3 w-3" /> 拒绝
      </Button>
    </div>
  );
};
