/**
 * 远端同步对话框 — 三步式 wizard,把 MinIO / FastDFS 上的文件夹拽进 KB。
 *
 * UX 设计:
 *  Step 1 [连接]    选 source kind → 填配置 → 试连;成功后顶部 badge 闪一下变绿。
 *  Step 2 [选择]    左边目录树(可勾选目录) + 右边筛选表单(扩展名/大小);
 *                  preflight 一键告诉你"≈42 个文件,12.3MB"。
 *  Step 3 [同步]    进度环 + 实时文件流瀑布(SSE 推);per-file 状态图标会在
 *                  收到 progress 帧时短暂亮一下,瀑布最多保留 80 行。
 *
 * 重用约束:本 dialog 与具体 source kind 无关 — 全部 source 配置走 Json 表单
 * (按 schema 渲染),新增 kind 只要扩 SOURCE_SCHEMAS 即可,组件本体不动。
 */

import * as React from 'react';
import {
  ArrowLeft, ArrowRight, ChevronRight, CloudDownload, FileSearch,
  Filter, Folder, FolderOpen, PlugZap, RefreshCw, Sparkles,
  StopCircle, X,
} from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogHeader, DialogTitle,
  Input, SegmentedTabs, Switch, cn,
} from '@chayuan/ui';
import {
  remoteSync,
  type BrowseResult,
  type DownloadEvent,
  type JobEvent,
  type JobSnapshot,
  type ProgressEvent,
  type RemoteFile,
  type SourceKind,
  type SourceKindInfo,
  type SourceOptions,
  type StatusEvent,
  type SyncFilterInput,
} from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';
import { AssistantBrandLogo } from '../../../components/AssistantBrandLogo';

// ── source 配置 schema(声明式;新 kind 加在这里) ───────────────────

interface FieldSpec {
  key: string;
  label: string;
  type: 'text' | 'password' | 'switch' | 'textarea';
  placeholder?: string;
  hint?: string;
  required?: boolean;
}
interface SourceSchema {
  kind: SourceKind;
  label: string;
  fields: FieldSpec[];
  defaultOptions: () => SourceOptions;
}

const SOURCE_SCHEMAS: SourceSchema[] = [
  {
    kind: 'minio',
    label: 'MinIO / S3',
    fields: [
      { key: 'endpoint', label: 'Endpoint', type: 'text', placeholder: 'minio:9000 或 https://s3.example.com', required: true },
      { key: 'bucket', label: 'Bucket', type: 'text', placeholder: 'my-bucket', required: true },
      { key: 'access_key', label: 'Access Key', type: 'text', required: true },
      { key: 'secret_key', label: 'Secret Key', type: 'password', required: true },
      { key: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
      { key: 'secure', label: 'HTTPS', type: 'switch', hint: '勾上则 TLS;endpoint 含 https:// 自动识别' },
    ],
    defaultOptions: () => ({ endpoint: '', bucket: '', access_key: '', secret_key: '', region: 'us-east-1', secure: false }),
  },
  {
    kind: 'fastdfs',
    label: 'FastDFS',
    fields: [
      { key: 'trackers', label: 'Trackers', type: 'text', placeholder: '10.0.0.1:22122,10.0.0.2:22122', required: true, hint: '逗号分隔,可填多个' },
      { key: 'manifest_file_id', label: 'Manifest file_id', type: 'text', placeholder: 'group1/M00/00/00/abcd.txt', hint: '推荐:每行一个 file_id 的清单文件' },
      { key: 'local_root', label: '共享挂载根目录', type: 'text', placeholder: '/var/fdfs/storage/data', hint: '老部署 NFS 共享时填' },
    ],
    defaultOptions: () => ({ trackers: '', manifest_file_id: '', local_root: '' }),
  },
];

// ── 主组件 ──────────────────────────────────────────────────────────

export interface RemoteSyncDialogProps {
  open: boolean;
  kbName: string;
  onClose(): void;
  /** 同步完成后调用,用于刷新 KB 详情 */
  onCompleted?(snapshot: JobSnapshot): void;
}

type Step = 'connect' | 'select' | 'run';

export const RemoteSyncDialog: React.FC<RemoteSyncDialogProps> = ({
  open, kbName, onClose, onCompleted,
}) => {
  const [step, setStep] = React.useState<Step>('connect');
  const [kind, setKind] = React.useState<SourceKind>('minio');
  const [options, setOptions] = React.useState<SourceOptions>(() => SOURCE_SCHEMAS[0]!.defaultOptions());
  const [kindInfos, setKindInfos] = React.useState<SourceKindInfo[]>([]);

  // 切换 kind 时重置默认 options
  const onChangeKind = (k: SourceKind) => {
    setKind(k);
    const sch = SOURCE_SCHEMAS.find((s) => s.kind === k);
    if (sch) setOptions(sch.defaultOptions());
  };

  // 拉一次后端 kind 可用性
  React.useEffect(() => {
    if (!open) return;
    remoteSync.listKinds().then(setKindInfos).catch(() => setKindInfos([]));
  }, [open]);

  // 关闭时复位(避免下次打开看到上次状态)
  React.useEffect(() => {
    if (!open) {
      setStep('connect');
      setSelectedDirs([]);
      setFilter({ extensions: ['pdf', 'md', 'txt', 'docx'], max_size_bytes: 50 * 1024 * 1024 });
      setActiveJob(null);
    }
  }, [open]);

  // selection / filter 状态在外层维持,跨 step 不丢
  const [selectedDirs, setSelectedDirs] = React.useState<string[]>(['']);
  const [filter, setFilter] = React.useState<SyncFilterInput>({
    extensions: ['pdf', 'md', 'txt', 'docx'],
    max_size_bytes: 50 * 1024 * 1024,
  });
  const [activeJob, setActiveJob] = React.useState<JobSnapshot | null>(null);

  const schema = (SOURCE_SCHEMAS.find((s) => s.kind === kind) || SOURCE_SCHEMAS[0])!;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="!max-w-3xl !p-0 overflow-hidden">
        <DialogHeader className="border-b border-[var(--cy-border-subtle)] bg-gradient-to-r from-[var(--cy-brand-50)] to-transparent px-5 py-3">
          <DialogTitle className="flex items-center gap-2 text-base">
            <CloudDownload className="h-4 w-4 text-[var(--cy-brand-600)]" />
            从远端同步到「{kbName}」
          </DialogTitle>
          <Stepper step={step} />
        </DialogHeader>

        <div className="max-h-[70vh] min-h-[360px] overflow-y-auto px-5 py-4">
          {step === 'connect' && (
            <ConnectStep
              kind={kind}
              kindInfos={kindInfos}
              schema={schema}
              options={options}
              onChangeKind={onChangeKind}
              onChangeOptions={setOptions}
              onNext={() => setStep('select')}
            />
          )}
          {step === 'select' && (
            <SelectStep
              kind={kind}
              options={options}
              selectedDirs={selectedDirs}
              setSelectedDirs={setSelectedDirs}
              filter={filter}
              setFilter={setFilter}
              onBack={() => setStep('connect')}
              onStart={async () => {
                try {
                  const snap = await remoteSync.start({
                    kb_name: kbName, kind, options,
                    paths: selectedDirs, filter,
                  });
                  setActiveJob(snap);
                  setStep('run');
                } catch (e) {
                  reportError(e, '启动同步失败');
                }
              }}
            />
          )}
          {step === 'run' && activeJob && (
            <RunStep
              jobId={activeJob.id}
              onBack={() => setStep('select')}
              onClose={onClose}
              onCompleted={(snap) => {
                if (snap.status === 'done' && snap.succeeded > 0) {
                  notifySuccess(`已同步 ${snap.succeeded} 个文件`);
                }
                onCompleted?.(snap);
              }}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

// ── 子组件:Stepper ─────────────────────────────────────────────────

const STEPS: { v: Step; label: string }[] = [
  { v: 'connect', label: '1. 连接' },
  { v: 'select', label: '2. 选择' },
  { v: 'run', label: '3. 同步' },
];

const Stepper: React.FC<{ step: Step }> = ({ step }) => {
  const idx = STEPS.findIndex((s) => s.v === step);
  return (
    <div className="mt-2 flex items-center gap-1.5 text-xs">
      {STEPS.map((s, i) => (
        <React.Fragment key={s.v}>
          <span className={cn(
            'rounded-full px-2 py-0.5 transition-colors',
            i === idx ? 'bg-[var(--cy-brand-600)] text-white' :
            i < idx ? 'bg-emerald-100 text-emerald-700' :
                       'bg-[var(--cy-surface-2)] text-[var(--cy-text-tertiary)]',
          )}>{s.label}</span>
          {i < STEPS.length - 1 && <ChevronRight className="h-3 w-3 text-[var(--cy-text-tertiary)]" />}
        </React.Fragment>
      ))}
    </div>
  );
};

// ── Step 1:连接 ────────────────────────────────────────────────────

const ConnectStep: React.FC<{
  kind: SourceKind;
  kindInfos: SourceKindInfo[];
  schema: SourceSchema;
  options: SourceOptions;
  onChangeKind(k: SourceKind): void;
  onChangeOptions(o: SourceOptions): void;
  onNext(): void;
}> = ({ kind, kindInfos, schema, options, onChangeKind, onChangeOptions, onNext }) => {
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<{ ok: boolean; msg: string } | null>(null);

  const items = SOURCE_SCHEMAS.map((s) => {
    const info = kindInfos.find((k) => k.kind === s.kind);
    return {
      value: s.kind,
      label: (
        <span className="inline-flex items-center gap-1">
          {s.label}
          {info && !info.available && <span className="text-[10px] text-amber-600">(缺依赖)</span>}
        </span>
      ),
      disabled: info ? !info.available : false,
    };
  });

  const onTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await remoteSync.testConnection(kind, options);
      setTestResult({ ok: !!r.ok, msg: r.msg });
    } catch (e) {
      setTestResult({ ok: false, msg: (e as Error).message || '连接失败' });
    } finally {
      setTesting(false);
    }
  };

  const setField = (k: string, v: unknown) =>
    onChangeOptions({ ...(options as Record<string, unknown>), [k]: v });

  return (
    <div className="space-y-4">
      <SegmentedTabs items={items} value={kind} onChange={(v) => onChangeKind(v as SourceKind)} />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {schema.fields.map((f) => (
          <div key={f.key} className={cn('space-y-1', f.type === 'switch' && 'md:col-span-2 flex items-center justify-between')}>
            {f.type === 'switch' ? (
              <>
                <div>
                  <div className="text-xs font-medium">{f.label}</div>
                  {f.hint && <div className="text-[10px] text-[var(--cy-text-tertiary)]">{f.hint}</div>}
                </div>
                <Switch
                  checked={Boolean((options as Record<string, unknown>)[f.key])}
                  onCheckedChange={(v) => setField(f.key, v)}
                />
              </>
            ) : (
              <>
                <label className="text-xs font-medium">
                  {f.label} {f.required && <span className="text-red-500">*</span>}
                </label>
                <Input
                  type={f.type === 'password' ? 'password' : 'text'}
                  value={String((options as Record<string, unknown>)[f.key] ?? '')}
                  placeholder={f.placeholder}
                  onChange={(e) => setField(f.key, e.target.value)}
                  className="h-8 text-xs"
                />
                {f.hint && <div className="text-[10px] text-[var(--cy-text-tertiary)]">{f.hint}</div>}
              </>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between rounded-lg border border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-xs">
          {testing ? (
            <><AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" /><span>连接测试中…</span></>
          ) : testResult ? (
            <span className={cn(
              'inline-flex items-center gap-1 rounded-full px-2 py-0.5',
              testResult.ok ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700',
            )}>
              {testResult.ok ? <Sparkles className="h-3 w-3" /> : <X className="h-3 w-3" />}
              <span className="truncate max-w-[260px]">{testResult.msg}</span>
            </span>
          ) : (
            <><PlugZap className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" /><span className="text-[var(--cy-text-tertiary)]">填好配置,先来一次连接测试</span></>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onTest} disabled={testing}>
            <RefreshCw className={cn('h-3.5 w-3.5', testing && 'animate-spin')} /> 试连
          </Button>
          <Button size="sm" onClick={onNext} disabled={!testResult?.ok}>
            下一步 <ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
};

// ── Step 2:选择 ────────────────────────────────────────────────────

const SelectStep: React.FC<{
  kind: SourceKind;
  options: SourceOptions;
  selectedDirs: string[];
  setSelectedDirs(p: string[]): void;
  filter: SyncFilterInput;
  setFilter(f: SyncFilterInput): void;
  onBack(): void;
  onStart(): void;
}> = ({ kind, options, selectedDirs, setSelectedDirs, filter, setFilter, onBack, onStart }) => {
  const [cwd, setCwd] = React.useState('');
  const [page, setPage] = React.useState<BrowseResult | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [pre, setPre] = React.useState<{ total: number; bytes: number; sample: RemoteFile[] } | null>(null);
  const [previewing, setPreviewing] = React.useState(false);

  const loadPath = React.useCallback(async (path: string) => {
    setLoading(true);
    try {
      const p = await remoteSync.browse(kind, options, path);
      setPage(p);
      setCwd(p.cwd);
    } catch (e) {
      reportError(e, '列目录失败');
    } finally {
      setLoading(false);
    }
  }, [kind, options]);

  React.useEffect(() => { loadPath(''); }, [loadPath]);

  const toggleDir = (key: string) => {
    setSelectedDirs(
      selectedDirs.includes(key)
        ? selectedDirs.filter((k) => k !== key)
        : [...selectedDirs, key],
    );
  };

  const onPreflight = async () => {
    setPreviewing(true);
    try {
      const r = await remoteSync.preflight(kind, options, selectedDirs.length ? selectedDirs : [''], filter);
      setPre({ total: r.total, bytes: r.bytes_total, sample: r.sample });
    } catch (e) {
      reportError(e, '预扫描失败');
    } finally {
      setPreviewing(false);
    }
  };

  const breadcrumbs = (cwd ? cwd.split('/').filter(Boolean) : []);
  const extInputValue = (filter.extensions || []).join(',');
  const sizeMb = filter.max_size_bytes ? Math.round(filter.max_size_bytes / 1024 / 1024) : 0;

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_320px]">
      {/* 左:目录浏览 */}
      <div className="space-y-2">
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            className="rounded px-1.5 py-0.5 hover:bg-[var(--cy-surface-2)]"
            onClick={() => loadPath('')}
          >根</button>
          {breadcrumbs.map((seg, i) => {
            const path = breadcrumbs.slice(0, i + 1).join('/') + '/';
            return (
              <React.Fragment key={path}>
                <ChevronRight className="h-3 w-3 text-[var(--cy-text-tertiary)]" />
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 hover:bg-[var(--cy-surface-2)]"
                  onClick={() => loadPath(path)}
                >{seg}</button>
              </React.Fragment>
            );
          })}
          <Button size="icon" variant="ghost" className="ml-auto h-6 w-6"
            onClick={() => loadPath(cwd)}>
            <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
          </Button>
        </div>

        <div className="max-h-[360px] overflow-y-auto rounded-lg border border-[var(--cy-border-subtle)]">
          <table className="w-full text-xs">
            <tbody>
              {(page?.entries || []).map((e) => (
                <tr key={e.key} className={cn(
                  'border-b border-[var(--cy-border-subtle)]/40 hover:bg-[var(--cy-surface-1)]',
                )}>
                  <td className="w-7 px-2 py-1.5">
                    {e.is_dir ? (
                      <input
                        type="checkbox"
                        checked={selectedDirs.includes(e.key)}
                        onChange={() => toggleDir(e.key)}
                      />
                    ) : null}
                  </td>
                  <td className="w-7 px-1 py-1.5">
                    {e.is_dir
                      ? <Folder className="h-3.5 w-3.5 text-[var(--cy-brand-500)]" />
                      : <FileSearch className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />}
                  </td>
                  <td
                    className={cn('truncate px-1 py-1.5', e.is_dir && 'cursor-pointer text-[var(--cy-brand-700)]')}
                    onClick={() => e.is_dir && loadPath(e.key)}
                  >
                    {e.name || '/'}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right text-[var(--cy-text-tertiary)]">
                    {e.is_dir ? '' : formatSize(e.size)}
                  </td>
                </tr>
              ))}
              {(!loading && (page?.entries.length ?? 0) === 0) && (
                <tr><td colSpan={4} className="py-6 text-center text-[var(--cy-text-tertiary)]">空目录</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center gap-2 text-[11px] text-[var(--cy-text-tertiary)]">
          <FolderOpen className="h-3 w-3" />
          已选 {selectedDirs.length} 个起点{selectedDirs.includes('') && '(含根)'}
          {selectedDirs.length > 0 && (
            <button type="button" className="ml-1 underline hover:text-[var(--cy-text-primary)]"
              onClick={() => setSelectedDirs([])}>清空</button>
          )}
        </div>
      </div>

      {/* 右:筛选 + preflight */}
      <div className="space-y-3">
        <div className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
            <Filter className="h-3.5 w-3.5" /> 筛选规则
          </div>
          <div className="space-y-2">
            <div>
              <label className="text-[10px] text-[var(--cy-text-tertiary)]">扩展名(逗号分隔,空=不限)</label>
              <Input
                value={extInputValue}
                onChange={(e) => setFilter({ ...filter, extensions: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
                className="h-7 text-xs"
                placeholder="pdf,md,docx"
              />
            </div>
            <div>
              <label className="text-[10px] text-[var(--cy-text-tertiary)]">单文件大小上限(MB)</label>
              <Input
                type="number"
                value={String(sizeMb)}
                onChange={(e) => {
                  const mb = parseInt(e.target.value || '0', 10);
                  setFilter({ ...filter, max_size_bytes: mb > 0 ? mb * 1024 * 1024 : undefined });
                }}
                className="h-7 text-xs"
              />
            </div>
          </div>
        </div>

        <Button variant="ghost" size="sm" className="w-full" onClick={onPreflight} disabled={previewing || !selectedDirs.length}>
          {previewing ? <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" /> : <FileSearch className="h-3.5 w-3.5" />}
          预扫描
        </Button>

        {pre && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
            <div className="text-base font-semibold">≈{pre.total.toLocaleString()} 个文件</div>
            <div className="text-[11px] text-emerald-700">合计 {formatSize(pre.bytes)}</div>
            {pre.sample.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-[10px]">预览样本 {pre.sample.length} 条</summary>
                <ul className="mt-1 max-h-28 overflow-y-auto pl-3 text-[10px]">
                  {pre.sample.map((f) => <li key={f.key} className="truncate">{f.name}</li>)}
                </ul>
              </details>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="h-3.5 w-3.5" /> 上一步
          </Button>
          <Button size="sm" onClick={onStart} disabled={!selectedDirs.length}>
            开始同步 <ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
};

// ── Step 3:同步进度 ────────────────────────────────────────────────

interface FeedRow {
  id: number;
  file: string;
  status: ProgressEvent['status'];
  size: number;
  error?: string | null;
}

const RunStep: React.FC<{
  jobId: string;
  onBack(): void;
  onClose(): void;
  onCompleted(snap: JobSnapshot): void;
}> = ({ jobId, onBack, onClose, onCompleted }) => {
  const [snap, setSnap] = React.useState<JobSnapshot | null>(null);
  const [feed, setFeed] = React.useState<FeedRow[]>([]);
  const [throughput, setThroughput] = React.useState({ bps: 0, fps: 0 }); // bytes/s, files/s
  // 当前正在下载的文件细粒度进度;后端 download 帧驱动
  const [downloading, setDownloading] = React.useState<DownloadEvent | null>(null);
  const startRef = React.useRef<{ t: number; bytes: number; processed: number } | null>(null);
  const idRef = React.useRef(0);

  React.useEffect(() => {
    let stopped = false;
    // 拉一次初始 snapshot,SSE 上来再覆盖
    remoteSync.get(jobId).then((s) => !stopped && setSnap(s)).catch(() => {});

    const off = remoteSync.subscribeJob(jobId, {
      onEvent: (ev: JobEvent) => {
        if (stopped) return;
        if (ev.type === 'download') {
          const d = ev.data as DownloadEvent;
          setDownloading(d);
          // 100% 收尾帧后,等待对应 progress 事件清掉显示;
          // 这里不立刻清,避免闪烁
          return;
        }
        if (ev.type === 'progress') {
          const p = ev.data as ProgressEvent;
          // 当 progress 帧到达,表示这个文件已结束(ok / failed / skipped),
          // 清掉细粒度下载进度以让位给下一个文件
          setDownloading((cur) => (cur && cur.file === p.file ? null : cur));
          setFeed((prev) => {
            const next = [{ id: ++idRef.current, file: p.file, status: p.status, size: p.size, error: p.error }, ...prev];
            return next.slice(0, 80);
          });
          // 顺便用 progress 帧自己推算 snap(避免 SSE 完成前 UI 卡住)
          setSnap((cur) => cur ? {
            ...cur,
            processed: p.processed,
            total: p.total || cur.total,
            succeeded: cur.succeeded + (p.status === 'ok' ? 1 : 0),
            failed: cur.failed + (p.status === 'failed' ? 1 : 0),
            skipped: cur.skipped + (p.status === 'skipped' ? 1 : 0),
            bytes_done: cur.bytes_done + (p.status === 'ok' ? p.size : 0),
            last_file: p.file,
          } : cur);
        } else if (ev.type === 'status') {
          const s = ev.data as StatusEvent;
          setSnap((cur) => cur ? { ...cur, status: s.status, error: s.error || cur.error } : cur);
          if (['done', 'failed', 'cancelled'].includes(s.status)) {
            remoteSync.get(jobId).then((fresh) => {
              setSnap(fresh);
              onCompleted(fresh);
            }).catch(() => {});
          }
        }
      },
      onError: (e) => reportError(e, '同步流断开'),
    });
    return () => { stopped = true; off(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // 简单的吞吐计算:每 1.5s 算一次
  React.useEffect(() => {
    const t = setInterval(() => {
      if (!snap) return;
      const now = Date.now();
      const cur = { t: now, bytes: snap.bytes_done, processed: snap.processed };
      const prev = startRef.current;
      startRef.current = cur;
      if (!prev) return;
      const dt = (now - prev.t) / 1000;
      if (dt <= 0) return;
      setThroughput({
        bps: Math.max(0, (cur.bytes - prev.bytes) / dt),
        fps: Math.max(0, (cur.processed - prev.processed) / dt),
      });
    }, 1500);
    return () => clearInterval(t);
  }, [snap]);

  if (!snap) {
    return <div className="flex items-center gap-2 text-xs text-[var(--cy-text-tertiary)]"><AssistantBrandLogo running className="h-3 w-3 rounded-full" /> 准备中…</div>;
  }
  const pct = snap.total > 0 ? Math.min(100, Math.round((snap.processed / snap.total) * 100)) : 0;
  const isTerminal = ['done', 'failed', 'cancelled'].includes(snap.status);
  const eta = throughput.fps > 0 && snap.total > snap.processed
    ? Math.ceil((snap.total - snap.processed) / throughput.fps)
    : null;

  return (
    <div className="space-y-4">
      {/* 顶部:进度环 + 数字 */}
      <div className="flex items-center gap-5 rounded-xl border border-[var(--cy-border-subtle)] bg-gradient-to-br from-[var(--cy-brand-50)] to-transparent p-4">
        <ProgressRing pct={pct} status={snap.status} />
        <div className="min-w-0 flex-1">
          <div className="text-xs text-[var(--cy-text-tertiary)]">
            状态 <StatusBadge status={snap.status} />
          </div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            {snap.processed.toLocaleString()} <span className="text-base text-[var(--cy-text-tertiary)]">/ {snap.total.toLocaleString()}</span>
          </div>
          <div className="mt-1 grid grid-cols-3 gap-1 text-[11px] text-[var(--cy-text-tertiary)]">
            <span><span className="text-emerald-600 font-medium">✓ {snap.succeeded}</span> 入库</span>
            <span><span className="text-amber-600 font-medium">↷ {snap.skipped}</span> 跳过</span>
            <span><span className="text-red-600 font-medium">✗ {snap.failed}</span> 失败</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-[var(--cy-text-tertiary)]">
            <span>↓ {formatSize(throughput.bps)}/s</span>
            <span>{throughput.fps.toFixed(1)} 文件/s</span>
            {eta !== null && <span>预计 {formatDuration(eta)}</span>}
            <span>已下载 {formatSize(snap.bytes_done)}</span>
          </div>
        </div>
      </div>

      {/* 当前文件细粒度下载条:仅在 download 帧活跃时显示 */}
      {downloading && (
        <div className="rounded-lg border border-[var(--cy-brand-200)] bg-[var(--cy-brand-50,#eff6ff)] px-3 py-2">
          <div className="mb-1 flex items-center justify-between text-[11px]">
            <span className="min-w-0 flex-1 truncate text-[var(--cy-text-secondary)]">
              ↓ {downloading.file}
            </span>
            <span className="ml-2 whitespace-nowrap font-mono text-[var(--cy-text-tertiary)]">
              {formatSize(downloading.downloaded)}
              {downloading.total > 0 && ` / ${formatSize(downloading.total)}`}
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-white/70">
            <div
              className="h-full bg-[var(--cy-brand-500)] transition-[width] duration-200"
              style={{
                width: downloading.total > 0
                  ? `${Math.min(100, (downloading.downloaded / downloading.total) * 100)}%`
                  : '50%',
              }}
            />
          </div>
        </div>
      )}

      {/* 文件流瀑布 */}
      <div className="rounded-lg border border-[var(--cy-border-subtle)]">
        <div className="border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-1.5 text-[11px] text-[var(--cy-text-tertiary)]">
          实时文件流 · 最近 {feed.length} 条
        </div>
        <ul className="max-h-[260px] overflow-y-auto">
          {feed.map((row) => (
            <li key={row.id}
              className={cn(
                'flex items-center gap-2 border-b border-[var(--cy-border-subtle)]/40 px-3 py-1 text-xs',
                'animate-in fade-in slide-in-from-top-1 duration-300',
              )}>
              <span className={cn(
                'inline-block h-1.5 w-1.5 rounded-full',
                row.status === 'ok' && 'bg-emerald-500',
                row.status === 'skipped' && 'bg-amber-500',
                row.status === 'failed' && 'bg-red-500',
              )} />
              <span className="min-w-0 flex-1 truncate">{row.file}</span>
              <span className="whitespace-nowrap text-[10px] text-[var(--cy-text-tertiary)]">{formatSize(row.size)}</span>
            </li>
          ))}
          {feed.length === 0 && (
            <li className="px-3 py-6 text-center text-[11px] text-[var(--cy-text-tertiary)]">等待文件…</li>
          )}
        </ul>
      </div>

      {/* 底部按钮 */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={onBack} disabled={!isTerminal}>
          <ArrowLeft className="h-3.5 w-3.5" /> 调整设置
        </Button>
        <div className="flex items-center gap-2">
          {!isTerminal && (
            <Button variant="ghost" size="sm" className="text-red-600"
              onClick={() => remoteSync.cancel(jobId).catch(() => {})}>
              <StopCircle className="h-3.5 w-3.5" /> 取消
            </Button>
          )}
          <Button size="sm" onClick={onClose}>
            {isTerminal ? '完成' : '后台运行'}
          </Button>
        </div>
      </div>
    </div>
  );
};

// ── 小组件 ─────────────────────────────────────────────────────────

const ProgressRing: React.FC<{ pct: number; status: JobSnapshot['status'] }> = ({ pct, status }) => {
  const r = 28;
  const C = 2 * Math.PI * r;
  const off = C * (1 - pct / 100);
  const color = status === 'failed' ? 'stroke-red-500' :
                status === 'cancelled' ? 'stroke-amber-500' :
                status === 'done' ? 'stroke-emerald-500' : 'stroke-[var(--cy-brand-500)]';
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" className="shrink-0">
      <circle cx="36" cy="36" r={r} className="stroke-[var(--cy-surface-2)] fill-none" strokeWidth="6" />
      <circle cx="36" cy="36" r={r} className={cn('fill-none transition-[stroke-dashoffset] duration-500', color)}
        strokeWidth="6" strokeLinecap="round" strokeDasharray={C} strokeDashoffset={off}
        transform="rotate(-90 36 36)" />
      <text x="36" y="40" textAnchor="middle" className="fill-[var(--cy-text-primary)] text-[14px] font-semibold tabular-nums">
        {pct}%
      </text>
    </svg>
  );
};

const StatusBadge: React.FC<{ status: JobSnapshot['status'] }> = ({ status }) => {
  const map: Record<JobSnapshot['status'], { c: string; t: string }> = {
    queued:    { c: 'bg-slate-100 text-slate-700', t: '等待' },
    running:   { c: 'bg-[var(--cy-brand-100)] text-[var(--cy-brand-700)]', t: '运行中' },
    done:      { c: 'bg-emerald-100 text-emerald-700', t: '完成' },
    failed:    { c: 'bg-red-100 text-red-700', t: '失败' },
    cancelled: { c: 'bg-amber-100 text-amber-700', t: '已取消' },
  };
  const m = map[status];
  return <span className={cn('ml-1 rounded-full px-1.5 py-0.5 text-[10px]', m.c)}>{m.t}</span>;
};

function formatSize(bytes: number): string {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}
