/**
 * 95-5:文件夹定时同步 — 任务列表页。
 *
 * 顶部 [新建任务] / [刷新];列表显示每个 job 的关键状态;
 * 每行操作:[立即同步] [干跑] [编辑] [启用/暂停] [删除]
 */
import * as React from 'react';
import {
  Clock, FolderSync, MoreHorizontal, Pause, Play, Plus,
  RefreshCw, Trash2, Zap,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { folderSync, type FolderSyncJob } from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';
import { FolderSyncJobDialog } from './FolderSyncJobDialog';
import { getPlatform } from '@chayuan/platform-shared';

export function FolderSyncPage(): React.JSX.Element {
  const [jobs, setJobs] = React.useState<FolderSyncJob[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [editing, setEditing] = React.useState<FolderSyncJob | null>(null);
  const [showCreate, setShowCreate] = React.useState(false);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    try {
      setJobs(await folderSync.list());
    } catch (e) {
      reportError(e as Error, '加载同步任务失败');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { void refresh(); }, [refresh]);

  const handleTrigger = async (j: FolderSyncJob) => {
    try {
      const r = await folderSync.trigger(j.id);
      const s = r.summary as Record<string, number>;
      notifySuccess(
        `已同步 ${j.name}: +${s.added_indexed ?? 0} / ~${s.modified_reindexed ?? 0} / -${s.removed_purged ?? 0}, 错误 ${s.errors ?? 0}`,
      );
      void refresh();
    } catch (e) {
      reportError(e as Error, '立即同步失败');
    }
  };

  const handleDryRun = async (j: FolderSyncJob) => {
    try {
      const r = await folderSync.dryRun(j.id);
      const p = r.diff_preview!;
      alert(
        `干跑结果(${j.name}):\n` +
        `新增:${p.added.length} 个\n` +
        `修改:${p.modified.length} 个\n` +
        `删除:${p.removed.length} 个\n` +
        `不变:${p.unchanged}`,
      );
    } catch (e) {
      reportError(e as Error, '干跑失败');
    }
  };

  const handleToggleEnabled = async (j: FolderSyncJob) => {
    try {
      await folderSync.update(j.id, { enabled: !j.enabled } as Partial<FolderSyncJob>);
      void refresh();
    } catch (e) {
      reportError(e as Error, '更新失败');
    }
  };

  const handleDelete = async (j: FolderSyncJob) => {
    const ok = await getPlatform().dialog.confirm(
      `删除任务「${j.name}」?\n(不影响已同步的文件,只停止后续同步)`,
      { title: '删除同步任务' },
    );
    if (!ok) return;
    try {
      await folderSync.remove(j.id);
      notifySuccess('任务已删除');
      void refresh();
    } catch (e) {
      reportError(e as Error, '删除失败');
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <FolderSync className="h-5 w-5 text-primary" />
            文件夹定时同步
          </h2>
          <p className="text-sm text-muted-foreground">
            选本地文件夹定期扫描,新增 / 修改 / 删除自动入索引(支持文档+图像)
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void refresh()}
                  disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            刷新
          </Button>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            新建任务
          </Button>
        </div>
      </header>

      {!loading && jobs.length === 0 && (
        <div className="rounded-md border border-dashed p-8 text-center text-muted-foreground">
          <FolderSync className="mx-auto mb-2 h-8 w-8 opacity-50" />
          <p>还没有同步任务。新建一个让本地文件夹自动同步到知识库。</p>
        </div>
      )}

      <div className="space-y-2">
        {jobs.map((j) => (
          <JobRow
            key={j.id} job={j}
            onTrigger={() => void handleTrigger(j)}
            onDryRun={() => void handleDryRun(j)}
            onToggle={() => void handleToggleEnabled(j)}
            onEdit={() => setEditing(j)}
            onDelete={() => void handleDelete(j)}
          />
        ))}
      </div>

      <FolderSyncJobDialog
        open={showCreate || editing !== null}
        onOpenChange={(o) => {
          if (!o) { setShowCreate(false); setEditing(null); }
        }}
        editing={editing}
        onSaved={() => {
          setShowCreate(false);
          setEditing(null);
          void refresh();
        }}
      />
    </div>
  );
}

function JobRow({
  job, onTrigger, onDryRun, onToggle, onEdit, onDelete,
}: {
  job: FolderSyncJob;
  onTrigger: () => void;
  onDryRun: () => void;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}): React.JSX.Element {
  const summary = job.last_sync_summary as Record<string, unknown>;
  const intervalLabel = formatInterval(job.interval_seconds);
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn(
              'h-2 w-2 rounded-full',
              job.enabled ? 'bg-green-500' : 'bg-gray-300',
            )} />
            <h3 className="truncate font-medium">{job.name}</h3>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs">
              {intervalLabel}
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {job.folder_path} → {job.target}
          </p>
          {job.last_sync_at && (
            <p className="mt-1 text-xs text-muted-foreground">
              <Clock className="mr-1 inline h-3 w-3" />
              上次:{formatRelativeTime(job.last_sync_at)}
              {summary && Object.keys(summary).length > 0 && (
                <span className="ml-2">
                  +{(summary.added_indexed as number) ?? 0}
                  /~{(summary.modified_reindexed as number) ?? 0}
                  /-{(summary.removed_purged as number) ?? 0}
                  {(summary.errors as number) > 0 && (
                    <span className="text-destructive">
                      , 错误 {(summary.errors as number)}
                    </span>
                  )}
                </span>
              )}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={onTrigger}
                  title="立即同步">
            <Zap className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={onDryRun}
                  title="干跑(看 diff 不真同步)">
            干跑
          </Button>
          <Button variant="ghost" size="sm" onClick={onToggle}
                  title={job.enabled ? '暂停' : '启用'}>
            {job.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEdit}>编辑</Button>
          <Button variant="ghost" size="sm" onClick={onDelete}
                  className="text-destructive">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `每 ${seconds} 秒`;
  if (seconds < 3600) return `每 ${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `每 ${(seconds / 3600).toFixed(1)} 小时`;
  return `每 ${(seconds / 86400).toFixed(1)} 天`;
}

function formatRelativeTime(iso: string): string {
  try {
    const t = new Date(iso).getTime();
    const diff = Date.now() - t;
    if (diff < 60_000) return '刚才';
    if (diff < 3_600_000) return `${Math.round(diff / 60_000)} 分钟前`;
    if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} 小时前`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
