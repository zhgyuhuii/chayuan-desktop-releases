/**
 * 95-5:新建 / 编辑同步任务对话框。
 *
 * target 形式:
 *   coll:<id>     — 集合
 *   doc:<kb_name> — 文档 KB
 *   src:<id>      — 图像 source
 */
import * as React from 'react';
import { FolderSync } from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input, Switch, Textarea,
} from '@chayuan/ui';
import {
  folderSync, kbCollections, knowledgeUniverse,
  type FolderSyncJob, type KbCollection, type KuItem,
} from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: FolderSyncJob | null;   // null = 新建
  onSaved: () => void;
}

const INTERVAL_PRESETS: Array<{ label: string; sec: number }> = [
  { label: '每 5 分钟', sec: 300 },
  { label: '每 30 分钟', sec: 1800 },
  { label: '每 1 小时', sec: 3600 },
  { label: '每 6 小时', sec: 21600 },
  { label: '每天', sec: 86400 },
];

export function FolderSyncJobDialog({
  open, onOpenChange, editing, onSaved,
}: Props): React.JSX.Element {
  const [name, setName] = React.useState('');
  const [folderPath, setFolderPath] = React.useState('');
  const [targetType, setTargetType] = React.useState<'coll' | 'doc' | 'src'>('coll');
  const [targetValue, setTargetValue] = React.useState('');
  const [intervalSec, setIntervalSec] = React.useState(300);
  const [recursive, setRecursive] = React.useState(true);
  const [includeGlobs, setIncludeGlobs] = React.useState('*.pdf,*.docx,*.txt,*.md,*.jpg,*.png,*.webp');
  const [excludeGlobs, setExcludeGlobs] = React.useState('~$*,.DS_Store,*.tmp');
  const [collections, setCollections] = React.useState<KbCollection[]>([]);
  const [docKbs, setDocKbs] = React.useState<KuItem[]>([]);
  const [imgSrcs, setImgSrcs] = React.useState<KuItem[]>([]);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    // 拉所有可选 target
    void Promise.all([
      kbCollections.list().catch(() => []),
      knowledgeUniverse.list('document').catch(() => []),
      knowledgeUniverse.list('image').catch(() => []),
    ]).then(([colls, docs, imgs]) => {
      setCollections(colls);
      setDocKbs(docs);
      setImgSrcs(imgs);
    });
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    if (editing) {
      setName(editing.name);
      setFolderPath(editing.folder_path);
      setIntervalSec(editing.interval_seconds);
      setRecursive(editing.recursive);
      setIncludeGlobs((editing.include_globs || []).join(','));
      setExcludeGlobs((editing.exclude_globs || []).join(','));
      const t = editing.target;
      if (t.startsWith('coll:')) {
        setTargetType('coll'); setTargetValue(t.slice(5));
      } else if (t.startsWith('doc:')) {
        setTargetType('doc'); setTargetValue(t.slice(4));
      } else if (t.startsWith('src:')) {
        setTargetType('src'); setTargetValue(t.slice(4));
      }
    } else {
      setName(''); setFolderPath('');
      setTargetType('coll'); setTargetValue('');
      setIntervalSec(300); setRecursive(true);
      setIncludeGlobs('*.pdf,*.docx,*.txt,*.md,*.jpg,*.png,*.webp');
      setExcludeGlobs('~$*,.DS_Store,*.tmp');
    }
  }, [open, editing]);

  const submit = async () => {
    if (!name.trim() || !folderPath.trim() || !targetValue.trim()) return;
    const target = `${targetType}:${targetValue.trim()}`;
    const include = includeGlobs.split(',').map((s) => s.trim()).filter(Boolean);
    const exclude = excludeGlobs.split(',').map((s) => s.trim()).filter(Boolean);
    setBusy(true);
    try {
      if (editing) {
        await folderSync.update(editing.id, {
          name: name.trim(),
          folder_path: folderPath.trim(),
          target,
          interval_seconds: intervalSec,
          recursive,
          include_globs: include,
          exclude_globs: exclude,
        } as Partial<FolderSyncJob>);
        notifySuccess('已更新');
      } else {
        await folderSync.create({
          name: name.trim(), folder_path: folderPath.trim(), target,
          interval_seconds: intervalSec, recursive,
          include_globs: include, exclude_globs: exclude,
        });
        notifySuccess('已创建并启用');
      }
      onSaved();
    } catch (e) {
      reportError(e as Error, editing ? '更新失败' : '创建失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderSync className="h-5 w-5 text-primary" />
            {editing ? '编辑同步任务' : '新建同步任务'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">任务名 *</label>
            <Input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="项目素材自动同步" />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">本地文件夹路径 *</label>
            <Input value={folderPath} onChange={(e) => setFolderPath(e.target.value)}
                   placeholder="D:\work\project-x\materials"
                   className="font-mono text-sm" />
            <p className="mt-1 text-xs text-muted-foreground">
              任意绝对路径(用户决策 6)。chayuan 服务进程需要对此路径有读权限。
            </p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">同步到 *</label>
            <div className="flex gap-2">
              <select
                value={targetType}
                onChange={(e) => {
                  setTargetType(e.target.value as 'coll' | 'doc' | 'src');
                  setTargetValue('');
                }}
                className="rounded border bg-background px-2 py-1.5 text-sm"
              >
                <option value="coll">集合</option>
                <option value="doc">文档库</option>
                <option value="src">图像源</option>
              </select>
              <select
                value={targetValue}
                onChange={(e) => setTargetValue(e.target.value)}
                className="flex-1 rounded border bg-background px-2 py-1.5 text-sm"
              >
                <option value="">— 选一个 —</option>
                {targetType === 'coll' && collections.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.display_name || c.name} (id={c.id})
                  </option>
                ))}
                {targetType === 'doc' && docKbs.map((k) => (
                  <option key={k.ku_id} value={k.name}>
                    {k.display_name || k.name}
                  </option>
                ))}
                {targetType === 'src' && imgSrcs.map((k) => (
                  <option key={k.ku_id} value={k.ku_id.replace(/^src:/, '')}>
                    {k.display_name || k.name}
                  </option>
                ))}
              </select>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              集合会自动按文件类型分发(文档→子文档库,图像→子图像源)
            </p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">同步频率</label>
            <div className="flex flex-wrap gap-1">
              {INTERVAL_PRESETS.map((p) => (
                <Button
                  key={p.sec}
                  size="sm"
                  variant={intervalSec === p.sec ? 'default' : 'ghost'}
                  onClick={() => setIntervalSec(p.sec)}
                >
                  {p.label}
                </Button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={recursive} onCheckedChange={setRecursive} />
            <label className="text-sm">递归扫描子目录</label>
          </div>

          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              高级:文件名 glob(逗号分隔)
            </summary>
            <div className="mt-2 space-y-2">
              <div>
                <label className="mb-1 block text-xs">包含 globs</label>
                <Input value={includeGlobs}
                       onChange={(e) => setIncludeGlobs(e.target.value)}
                       className="font-mono text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs">排除 globs</label>
                <Input value={excludeGlobs}
                       onChange={(e) => setExcludeGlobs(e.target.value)}
                       className="font-mono text-xs" />
              </div>
            </div>
          </details>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
          <Button
            onClick={() => void submit()}
            disabled={!name.trim() || !folderPath.trim() || !targetValue.trim() || busy}
          >
            {busy ? '保存中…' : (editing ? '保存' : '创建并启用')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
