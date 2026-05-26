/**
 * 文档型 KB 详情面板。
 *
 * 设计:
 *   - 顶部 toolbar:文件数 / 上传(owner-only) / 多选删除(owner-only) / 搜索文件名
 *   - 表格列:checkbox / 图标 / 文件名 / 大小 / 入库状态 / chunks / 操作(预览 / 下载)
 *   - 预览 → 新窗口打开 /knowledge_base/preview_doc?... + token query
 *     下载 → /knowledge_base/download_doc?... + token query
 *   - owner 才能删除 / 上传(后端 require_owner_or_admin 已校验,UI 这层先做权限隐藏)
 */

import * as React from 'react';
import {
  CloudDownload,
  Download,
  Eye,
  FileText,
  FolderUp,
  Layers,
  PanelLeftClose,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { kb as kbApi, getAccessToken, getClientConfig, type DocumentFile, type KbFileChunk, type KuDocumentDetail } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { reportError, notifySuccess } from '../../../store/errorDialog';
import { useAuthStore } from '../../../store/auth';
import { usePreviewStore } from '../../preview';
import { RemoteSyncDialog } from '../sync/RemoteSyncDialog';
import { confirmDuplicateUpload } from '../upload/duplicateCheck';
import type { DetailPanelProps } from './types';

interface ReindexTaskState {
  taskId: string;
  mode?: 'upload' | 'reindex';
  queue?: string;
  state?: string;
  fileCount?: number;
  fileNames?: string[];
  processed?: number;
  okCount?: number;
  currentFile?: string;
  failedFiles?: Record<string, string>;
  error?: string;
}

export const DocumentKbDetail: React.FC<DetailPanelProps<KuDocumentDetail> & {
  onToggleCollapse?: () => void;
}> = ({
  detail,
  mine,
  onMutate,
  onToggleCollapse,
}) => {
  const [keyword, setKeyword] = React.useState('');
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [chunkFile, setChunkFile] = React.useState<string | null>(null);
  const [reindexTask, setReindexTask] = React.useState<ReindexTaskState | null>(null);

  const filtered = React.useMemo<DocumentFile[]>(() => {
    const arr = detail.files ?? [];
    if (!keyword.trim()) return arr;
    const q = keyword.toLowerCase();
    return arr.filter((f) => f.file_name.toLowerCase().includes(q));
  }, [detail.files, keyword]);

  const toggle = (name: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  const allSelected = filtered.length > 0 && filtered.every((f) => selected.has(f.file_name));
  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(filtered.map((f) => f.file_name)));
  };

  React.useEffect(() => {
    if (!reindexTask?.taskId) return;
    if (['success', 'partial_success', 'failed'].includes(reindexTask.state || '')) return;
    let stopped = false;
    const poll = async () => {
      try {
        const st = await kbApi.taskStatus(reindexTask.taskId);
        if (stopped) return;
        setReindexTask((cur) => cur && cur.taskId === reindexTask.taskId ? {
          ...cur,
          state: st.state || cur.state,
          fileCount: st.file_count ?? st.payload_summary?.file_count ?? cur.fileCount,
          processed: st.processed ?? (st.state === 'success' ? (st.file_count ?? cur.fileCount) : cur.processed),
          okCount: st.ok_count ?? cur.okCount,
          currentFile: st.current_file ?? cur.currentFile,
          failedFiles: st.failed_files ?? cur.failedFiles,
          error: st.error ?? cur.error,
        } : cur);
        if (['success', 'partial_success', 'failed'].includes(st.state || '')) {
          onMutate();
          return;
        }
      } catch {
        // 任务状态可能尚未写入;继续轮询几次,避免刚提交就闪错误。
      }
      if (!stopped) window.setTimeout(poll, 1000);
    };
    const timer = window.setTimeout(poll, 400);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [onMutate, reindexTask?.state, reindexTask?.taskId]);

  // 远端同步弹窗开关 — 仅 owner 可见
  const [syncOpen, setSyncOpen] = React.useState(false);

  // 上传进度 0..1;null 表示当前没有进行中的上传
  const [uploadRatio, setUploadRatio] = React.useState<number | null>(null);
  const uploadTaskActive = reindexTask?.mode === 'upload'
    && !['success', 'partial_success', 'failed'].includes(reindexTask.state || '');
  const uploadLocked = busy || uploadRatio !== null || uploadTaskActive;

  // 共用上传执行;source = 'files' / 'folder' 仅决定 picker 与文案
  const runUpload = async (source: 'files' | 'folder') => {
    if (!mine || uploadLocked) return;
    setBusy(true);
    try {
      const fs = getPlatform().fs;
      let picked: File[] | undefined;
      if (source === 'folder') {
        if (!fs.pickDirectory) {
          reportError(new Error('当前平台不支持文件夹选择'), '上传失败');
          return;
        }
        picked = (await fs.pickDirectory()) as unknown as File[];
      } else {
        picked = (await fs.pickFiles({ multiple: true })) as unknown as File[];
      }
      if (!picked || !picked.length) return;
      const duplicateOk = await confirmDuplicateUpload(detail.meta.name, picked);
      if (!duplicateOk) return;
      setUploadRatio(0);
      const resp = await kbApi.uploadDocs({
        knowledge_base_name: detail.meta.name,
        files: picked,
        override: false,
        to_vector_store: true,
        onProgress: (ev) => setUploadRatio(ev.ratio),
      });
      setUploadRatio(1);
      if (resp.data?.task_id) {
        const savedFiles = resp.data.saved_files?.length ? resp.data.saved_files : picked.map((f) => {
          const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
          return rel && rel.length ? rel : f.name;
        });
        setReindexTask({
          taskId: resp.data.task_id,
          mode: 'upload',
          queue: resp.data.queue,
          state: resp.data.queue === 'sync' ? 'success' : 'queued',
          fileCount: savedFiles.length,
          fileNames: savedFiles,
          processed: resp.data.queue === 'sync' ? savedFiles.length : 0,
          okCount: 0,
          failedFiles: {},
        });
      }
      const msg = source === 'folder'
        ? `已上传 ${picked.length} 个文件(含子目录),正在向量化…`
        : `已上传 ${picked.length} 个文件,正在向量化…`;
      notifySuccess(msg);
      onMutate();
    } catch (e) {
      reportError(e, '上传失败');
    } finally {
      setBusy(false);
      window.setTimeout(() => setUploadRatio(null), 800);
    }
  };
  const onUpload = () => runUpload('files');
  const onUploadFolder = () => runUpload('folder');

  const onDeleteSelected = async () => {
    if (!mine || selected.size === 0) return;
    const ok0 = await getPlatform().dialog.confirm(
      `删除选中的 ${selected.size} 个文件?该操作不可恢复`,
      { title: '删除文件' },
    );
    if (!ok0) return;
    setBusy(true);
    try {
      await kbApi.deleteDocs({
        knowledge_base_name: detail.meta.name,
        file_names: [...selected],
        delete_content: true,
      });
      notifySuccess(`已删除 ${selected.size} 个文件`);
      setSelected(new Set());
      onMutate();
    } catch (e) {
      reportError(e, '删除失败');
    } finally {
      setBusy(false);
    }
  };

  const onDeleteOne = React.useCallback(async (fileName: string) => {
    if (!mine) return;
    const ok0 = await getPlatform().dialog.confirm(
      `删除「${fileName}」?该操作不可恢复`,
      { title: '删除文件' },
    );
    if (!ok0) return;
    setBusy(true);
    try {
      await kbApi.deleteDocs({
        knowledge_base_name: detail.meta.name,
        file_names: [fileName],
        delete_content: true,
      });
      notifySuccess(`已删除「${fileName}」`);
      setSelected((s) => {
        if (!s.has(fileName)) return s;
        const next = new Set(s);
        next.delete(fileName);
        return next;
      });
      onMutate();
    } catch (e) {
      reportError(e, '删除失败');
    } finally {
      setBusy(false);
    }
  }, [mine, detail.meta.name, onMutate]);

  const runReindex = async (fileNames: string[]) => {
    if (!mine || fileNames.length === 0) return;
    const label = fileNames.length === 1 ? `「${fileNames[0]}」` : `${fileNames.length} 个文件`;
    const ok0 = await getPlatform().dialog.confirm(
      `重新生成 ${label} 的向量数据?\n旧向量会先删除,然后基于源文件重新切片入库。`,
      { title: '重建向量' },
    );
    if (!ok0) return;
    setBusy(true);
    try {
      const resp = await kbApi.reindexDocs({
        knowledge_base_name: detail.meta.name,
        file_names: fileNames,
        async_task: true,
      });
      const data = resp.data;
      if (data?.task_id) {
        setReindexTask({
          taskId: data.task_id,
          mode: 'reindex',
          queue: data.queue,
          state: data.queue === 'sync' ? 'success' : 'queued',
          fileCount: data.file_count ?? fileNames.length,
          fileNames,
          processed: data.queue === 'sync' ? fileNames.length : 0,
          okCount: 0,
          failedFiles: {},
        });
        notifySuccess(`已提交重新向量化任务:${data.task_id.slice(0, 8)}`, data.queue === 'sync' ? '已同步执行完成' : '点击进度条可查看当前任务');
      } else {
        notifySuccess(`已提交 ${label} 的重新向量化`);
      }
      if (fileNames.length > 1) setSelected(new Set());
      window.setTimeout(onMutate, 1200);
    } catch (e) {
      reportError(e, '重新生成向量数据失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <Toolbar
        total={filtered.length}
        selectedCount={selected.size}
        keyword={keyword}
        onKeyword={setKeyword}
        mine={mine}
        busy={uploadLocked}
        onUpload={onUpload}
        onUploadFolder={onUploadFolder}
        onSync={() => setSyncOpen(true)}
        onDeleteSelected={onDeleteSelected}
        onReindexSelected={() => runReindex([...selected])}
        onRefresh={onMutate}
        onToggleCollapse={onToggleCollapse}
      />

      <RemoteSyncDialog
        open={syncOpen}
        kbName={detail.meta.name}
        onClose={() => setSyncOpen(false)}
        onCompleted={() => onMutate()}
      />
      <FileChunksDialog
        open={chunkFile != null}
        kbName={detail.meta.name}
        fileName={chunkFile ?? ''}
        editable={isAdmin}
        onClose={() => setChunkFile(null)}
        onSaved={() => onMutate()}
      />

      {uploadRatio !== null && (
        <div className="border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[var(--cy-text-secondary)]">
              {uploadRatio < 1 ? '上传中…' : '上传完毕,正在向量化…'}
            </span>
            <span className="font-mono text-[var(--cy-text-tertiary)]">
              {Math.round(uploadRatio * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
            <div
              className={
                uploadRatio < 1
                  ? 'h-full bg-[var(--cy-brand-500)] transition-[width] duration-200'
                  : 'h-full bg-emerald-500 transition-[width] duration-200 animate-pulse'
              }
              style={{ width: `${Math.max(2, uploadRatio * 100)}%` }}
            />
          </div>
        </div>
      )}

      {reindexTask && (
        <ReindexProgressStrip task={reindexTask} onClose={() => setReindexTask(null)} />
      )}

      {filtered.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
          {keyword ? '没有匹配的文件' : '还没有文件,点上传开始'}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-[1] bg-[var(--cy-surface-1)] text-xs text-[var(--cy-text-tertiary)]">
              <tr>
                <th className="w-8 px-3 py-2 text-left">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    aria-label="全选"
                  />
                </th>
                <th className="w-8 px-2 py-2"></th>
                <th className="px-2 py-2 text-left font-medium">文件名</th>
                <th className="hidden px-2 py-2 text-left font-medium md:table-cell">入库状态</th>
                <th className="hidden px-2 py-2 text-left font-medium md:table-cell">大小</th>
                <th className="w-24 px-2 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <FileRow
                  key={f.file_name}
                  file={f}
                  kbName={detail.meta.name}
                  ingestTask={reindexTask}
                  mine={mine}
                  checked={selected.has(f.file_name)}
                  onToggle={() => toggle(f.file_name)}
                  onChunks={() => setChunkFile(f.file_name)}
                  onReindex={() => runReindex([f.file_name])}
                  onDelete={() => void onDeleteOne(f.file_name)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const Toolbar: React.FC<{
  total: number;
  selectedCount: number;
  keyword: string;
  onKeyword(s: string): void;
  mine: boolean;
  busy: boolean;
  onUpload(): void;
  onUploadFolder(): void;
  onSync(): void;
  onDeleteSelected(): void;
  onReindexSelected(): void;
  onRefresh(): void;
  onToggleCollapse?: () => void;
}> = ({ total, selectedCount, keyword, onKeyword, mine, busy, onUpload, onUploadFolder, onSync, onDeleteSelected, onReindexSelected, onRefresh, onToggleCollapse }) => (
  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-2">
    <div className="flex items-center gap-3 text-xs text-[var(--cy-text-tertiary)]">
      {onToggleCollapse && (
        <Button
          size="icon"
          variant="ghost"
          onClick={onToggleCollapse}
          aria-label="折叠文件列表"
          title="折叠文件列表"
          className="h-7 w-7"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </Button>
      )}
      <span>{total} 个文件</span>
      {selectedCount > 0 && <span className="text-[var(--cy-text-secondary)]">已选 {selectedCount}</span>}
      {mine && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-[var(--cy-brand-50,#eff6ff)] px-1.5 py-0.5 text-[10px] text-[var(--cy-brand-700)]"
          title="可以把文件或文件夹拖到本面板的任意位置上传"
        >
          <FolderUp className="h-2.5 w-2.5" />
          也可拖拽
        </span>
      )}
    </div>
    <div className="flex items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
        <input
          value={keyword}
          onChange={(e) => onKeyword(e.target.value)}
          placeholder="搜索文件"
          className="h-8 w-44 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-7 pr-3 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
        />
      </div>
      <Button size="icon" variant="ghost" onClick={onRefresh} aria-label="刷新" className="h-8 w-8">
        <RefreshCw className="h-3.5 w-3.5" />
      </Button>
      {mine && selectedCount > 0 && (
        <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={onReindexSelected} disabled={busy}>
          <RefreshCw className="h-3.5 w-3.5" /> 重新生成向量
        </Button>
      )}
      {mine && selectedCount > 0 && (
        <Button size="sm" variant="ghost" className="h-8 text-xs text-destructive" onClick={onDeleteSelected} disabled={busy}>
          <Trash2 className="h-3.5 w-3.5" /> 删除
        </Button>
      )}
      {mine && (
        <Button size="sm" variant="ghost" onClick={onSync} disabled={busy} className="h-8 text-xs">
          <CloudDownload className="h-3.5 w-3.5" /> 远端同步
        </Button>
      )}
      {mine && (
        <Button size="sm" variant="ghost" onClick={onUploadFolder} disabled={busy} className="h-8 text-xs"
          title="选择整个文件夹上传(保留子目录结构)">
          <FolderUp className="h-3.5 w-3.5" /> 文件夹
        </Button>
      )}
      {mine && (
        <Button size="sm" onClick={onUpload} disabled={busy} className="h-8 text-xs">
          <Upload className="h-3.5 w-3.5" /> 上传
        </Button>
      )}
    </div>
  </div>
);

const ReindexProgressStrip: React.FC<{ task: ReindexTaskState; onClose(): void }> = ({ task, onClose }) => {
  const total = Math.max(1, task.fileCount ?? 1);
  const processed = Math.min(total, task.processed ?? 0);
  const pct = task.state === 'success' ? 100 : Math.round((processed / total) * 100);
  const done = ['success', 'partial_success', 'failed'].includes(task.state || '');
  const failedCount = Object.keys(task.failedFiles || {}).length;
  const verb = task.mode === 'upload' ? '入库' : '重新生成向量';
  const title = task.state === 'failed'
    ? `${verb}失败`
    : done
      ? `${verb}完成：成功 ${task.okCount ?? Math.max(0, processed - failedCount)} / ${total}`
      : `正在${verb}：${processed} / ${total}`;
  return (
    <button
      type="button"
      onClick={() => {
        const detail = [
          `任务: ${task.taskId}`,
          `状态: ${task.state || 'queued'}`,
          task.currentFile ? `当前文件: ${task.currentFile}` : '',
          task.error ? `错误: ${task.error}` : '',
          failedCount ? `失败文件: ${Object.entries(task.failedFiles || {}).map(([k, v]) => `${k}: ${v}`).join('\n')}` : '',
        ].filter(Boolean).join('\n');
        window.alert(detail);
      }}
      className="border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-2 text-left"
      title="点击查看任务详情"
    >
      <div className="flex items-center justify-between gap-3 text-[11px]">
        <span className={cn(
          'inline-flex items-center gap-1.5',
          task.state === 'failed' ? 'text-red-600' : done ? 'text-emerald-600' : 'text-[var(--cy-text-secondary)]',
        )}>
          <RefreshCw className={cn('h-3.5 w-3.5', !done && 'animate-spin')} />
          {title}
          {task.queue === 'local' && <span className="text-[var(--cy-text-tertiary)]">本地后台任务</span>}
        </span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-[var(--cy-text-tertiary)]">{pct}%</span>
          {done && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => { e.stopPropagation(); onClose(); }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.stopPropagation();
                  onClose();
                }
              }}
              className="rounded px-1.5 py-0.5 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
            >
              关闭
            </span>
          )}
        </span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
        <div
          className={cn(
            'h-full transition-[width] duration-300',
            task.state === 'failed' ? 'bg-red-500' : done ? 'bg-emerald-500' : 'bg-[var(--cy-brand-500)]',
          )}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>
      {!done && task.currentFile && (
        <div className="mt-1 truncate text-[10px] text-[var(--cy-text-tertiary)]">
          当前文件：{task.currentFile}
        </div>
      )}
    </button>
  );
};

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

const FileRow: React.FC<{
  file: DocumentFile;
  kbName: string;
  ingestTask: ReindexTaskState | null;
  mine: boolean;
  checked: boolean;
  onToggle(): void;
  onChunks(): void;
  onReindex(): void;
  onDelete(): void;
}> = ({ file, kbName, ingestTask, mine, checked, onToggle, onChunks, onReindex, onDelete }) => {
  const openPreview = usePreviewStore((s) => s.open);
  const progress = fileIngestProgress(file.file_name, ingestTask);
  const onPreview = () => {
    openPreview({
      source: 'kb-doc',
      kbName,
      fileName: file.file_name,
      fileSize: file.file_size ?? undefined,
    });
  };
  const onDownload = async () => {
    const url = await buildDocUrl(kbName, file.file_name, 'download_doc');
    if (!url) return;
    if (typeof window !== 'undefined') {
      const a = document.createElement('a');
      a.href = url;
      a.download = file.file_name;
      a.click();
    }
  };
  return (
    <tr className="cursor-pointer border-b border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]" onClick={onPreview}>
      <td className="px-3 py-1.5" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={checked} onChange={onToggle} />
      </td>
      <td className="px-2 py-1.5">
        <FileText className="h-4 w-4 text-[var(--cy-text-tertiary)]" />
      </td>
      <td className="min-w-0 truncate px-2 py-1.5 font-medium text-[var(--cy-text-primary)]">{file.file_name}</td>
      <td className="hidden px-2 py-1.5 text-xs text-[var(--cy-text-tertiary)] md:table-cell">
        {progress ? (
          <FileIngestProgress progress={progress} />
        ) : (
          file.in_db ? `已索引 · ${file.doc_count ?? 0} chunks` : '未索引'
        )}
      </td>
      <td className="hidden whitespace-nowrap px-2 py-1.5 text-xs text-[var(--cy-text-tertiary)] md:table-cell">
        {file.file_size ? `${(file.file_size / 1024).toFixed(1)} KB` : '—'}
      </td>
      <td className="whitespace-nowrap px-2 py-1.5 text-right" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={onPreview}
          aria-label="预览"
          className={cn('mx-0.5 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]')}
          title="预览"
        >
          <Eye className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onDownload}
          aria-label="下载"
          className={cn('mx-0.5 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]')}
          title="下载"
        >
          <Download className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onChunks}
          aria-label="查看切片"
          className={cn('mx-0.5 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]')}
          title="查看切片"
        >
          <Layers className="h-3.5 w-3.5" />
        </button>
        {mine && (
          <button
            type="button"
            onClick={onReindex}
            aria-label="重新生成向量"
            className={cn('mx-0.5 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]')}
            title="删除旧向量并重新生成"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        )}
        {mine && (
          <button
            type="button"
            onClick={onDelete}
            aria-label="删除"
            title="删除这个文件"
            className={cn('mx-0.5 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-red-500/10 hover:text-red-600')}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </td>
    </tr>
  );
};

interface FileProgressState {
  label: string;
  pct: number;
  tone: 'running' | 'done' | 'error';
}

function fileIngestProgress(fileName: string, task: ReindexTaskState | null): FileProgressState | null {
  if (!task || !task.fileNames?.includes(fileName)) return null;
  const terminal = ['success', 'partial_success', 'failed'].includes(task.state || '');
  const failed = Boolean(task.failedFiles?.[fileName]);
  if (failed) return { label: '入库失败', pct: 100, tone: 'error' };
  const idx = task.fileNames.indexOf(fileName);
  const processed = task.processed ?? 0;
  if (terminal || processed > idx) {
    return { label: task.mode === 'upload' ? '入库完成' : '重建完成', pct: 100, tone: 'done' };
  }
  if (task.currentFile === fileName) {
    const total = Math.max(1, task.fileCount ?? task.fileNames.length);
    const pct = Math.max(6, Math.min(98, Math.round(((idx + 0.5) / total) * 100)));
    return { label: task.mode === 'upload' ? '正在入库…' : '正在重建…', pct, tone: 'running' };
  }
  return { label: '等待入库…', pct: 3, tone: 'running' };
}

const FileIngestProgress: React.FC<{ progress: FileProgressState }> = ({ progress }) => (
  <div className="min-w-[150px] max-w-[240px]">
    <div className="mb-1 flex items-center justify-between gap-2">
      <span className={cn(
        progress.tone === 'error' ? 'text-red-600' : progress.tone === 'done' ? 'text-emerald-600' : 'text-[var(--cy-text-secondary)]',
      )}>
        {progress.label}
      </span>
      <span className="font-mono text-[10px] text-[var(--cy-text-tertiary)]">{progress.pct}%</span>
    </div>
    <div className="h-1 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
      <div
        className={cn(
          'h-full transition-[width] duration-300',
          progress.tone === 'error' ? 'bg-red-500' : progress.tone === 'done' ? 'bg-emerald-500' : 'bg-[var(--cy-brand-500)]',
        )}
        style={{ width: `${Math.max(2, progress.pct)}%` }}
      />
    </div>
  </div>
);

const FileChunksDialog: React.FC<{
  open: boolean;
  kbName: string;
  fileName: string;
  editable: boolean;
  onClose(): void;
  onSaved(): void;
}> = ({ open, kbName, fileName, editable, onClose, onSaved }) => {
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [chunks, setChunks] = React.useState<KbFileChunk[]>([]);

  React.useEffect(() => {
    if (!open || !fileName) return;
    let cancelled = false;
    setLoading(true);
    kbApi.listFileChunks({ knowledge_base_name: kbName, file_name: fileName })
      .then((data) => {
        if (!cancelled) setChunks(data.chunks ?? []);
      })
      .catch((e) => reportError(e, '加载切片失败'))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, kbName, fileName]);

  const updateText = (idx: number, text: string) => {
    setChunks((prev) => prev.map((c, i) => (i === idx ? { ...c, text } : c)));
  };

  const save = async () => {
    if (!editable || !fileName) return;
    setSaving(true);
    try {
      await kbApi.saveFileChunks({
        knowledge_base_name: kbName,
        file_name: fileName,
        chunks: chunks.map((c) => ({
          chunk_id: c.chunk_id,
          text: c.text,
          metadata: c.metadata,
        })),
      });
      notifySuccess('切片已保存', '已重新向量化该文件');
      onSaved();
      onClose();
    } catch (e) {
      reportError(e, '保存切片失败');
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" role="dialog" aria-modal="true">
      <div className="flex max-h-[82vh] w-[min(980px,92vw)] flex-col overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] shadow-xl">
        <header className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-[var(--cy-text-primary)]">文件切片</h3>
            <p className="mt-0.5 text-xs text-[var(--cy-text-tertiary)]">{fileName}</p>
          </div>
          <button type="button" className="text-sm text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]" onClick={onClose}>
            关闭
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="py-10 text-center text-sm text-[var(--cy-text-tertiary)]">正在加载切片…</div>
          ) : chunks.length === 0 ? (
            <div className="py-10 text-center text-sm text-[var(--cy-text-tertiary)]">暂无切片</div>
          ) : (
            <div className="space-y-3">
              {chunks.map((chunk, idx) => (
                <section key={`${chunk.chunk_id || idx}`} className="rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-3">
                  <div className="mb-2 flex items-center justify-between text-xs text-[var(--cy-text-tertiary)]">
                    <span>#{idx + 1} {chunk.chunk_id ? `· ${chunk.chunk_id}` : ''}</span>
                    <span>{chunk.text.length} 字</span>
                  </div>
                  {editable ? (
                    <textarea
                      value={chunk.text}
                      onChange={(e) => updateText(idx, e.target.value)}
                      className="min-h-28 w-full resize-y rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2 text-sm text-[var(--cy-text-primary)] focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none"
                    />
                  ) : (
                    <pre className="whitespace-pre-wrap rounded-lg bg-[var(--cy-surface-base)] p-2 text-sm text-[var(--cy-text-primary)]">{chunk.text}</pre>
                  )}
                  {Object.keys(chunk.metadata ?? {}).length > 0 && (
                    <pre className="mt-2 max-h-28 overflow-auto rounded-lg bg-[var(--cy-surface-base)] p-2 text-[11px] text-[var(--cy-text-tertiary)]">
                      {JSON.stringify(chunk.metadata, null, 2)}
                    </pre>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>
        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] px-4 py-3 text-xs text-[var(--cy-text-tertiary)]">
          <span>{editable ? '保存后会重新向量化该文件' : '当前账号可查看切片，只有管理员可修改'}</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>取消</Button>
            {editable && (
              <Button size="sm" onClick={save} disabled={saving || loading}>
                {saving ? '保存中…' : '保存并重新向量化'}
              </Button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
};

/**
 * 拼出 /knowledge_base/{preview_doc|download_doc} 的完整 URL。
 * 这两个端点是中间件 token 白名单(see middleware._QUERY_TOKEN_ALLOWED_PATHS),
 * 浏览器直接 GET 不能塞 Authorization header,所以把 token 放 query。
 */
async function buildDocUrl(kbName: string, fileName: string, action: 'preview_doc' | 'download_doc'): Promise<string | null> {
  const cfg = getClientConfig();
  const base = (cfg.baseURL || '').replace(/\/+$/, '');
  const tok = await getAccessToken();
  const qs = new URLSearchParams({
    knowledge_base_name: kbName,
    file_name: fileName,
  });
  if (tok) qs.set('token', tok);
  return `${base}/knowledge_base/${action}?${qs.toString()}`;
}

