/**
 * 设置面板里的「数据目录」段(Phase 7)。
 *
 * 提供:
 *   - 显示当前数据目录(只读)
 *   - 「切换数据目录」按钮 → 弹模态:警告 + 选新目录 + 已备份勾选 → 确认
 *   - 确认流程:``sidecar.kill()`` → ``dataDir.set(newPath)`` → ``window.location.reload()``
 *     重启时 Shell 走 FirstRunSetup → SidecarGate,自动用新目录起 sidecar
 *
 * 警示:目前**不自动迁移旧数据**(不复制 / 不移动);用户在切换前自己决定是否
 * 备份。后续 Phase 7.x 可加"复制旧数据 → 校验 → 切换"向导,但单文件 sqlite +
 * 目录结构整体复制就是 cp -r,实际场景下让用户自己用文件管理器复制更可靠。
 */

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@chayuan/ui';
import { CheckCircle2, FolderOpen, Loader2 } from 'lucide-react';
import * as React from 'react';
import { reportError } from '../../store/errorDialog';
import { getDataDirApi, isDataDirSupported } from './dataDirApi';
import { getPlatform } from '@chayuan/platform-shared';

export const DataDirSwitchSection: React.FC = () => {
  const supported = isDataDirSupported();
  const [currentPath, setCurrentPath] = React.useState<string>('');
  const [loadError, setLoadError] = React.useState<string>('');
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    if (!supported) return;
    void getDataDirApi()
      .state()
      .then((s) => setCurrentPath(s.path))
      .catch((e: unknown) => {
        // 拿不到状态(命令未注册等)— 不再静默,显式提示
        setLoadError(String((e as { message?: string })?.message ?? e));
      });
  }, [supported]);

  // 不再静默 return null —— 用户在设置里点开"数据目录"必须看到东西。
  // 三种降级态各给明确文案:
  if (!supported) {
    return (
      <p className="text-xs text-zinc-500">
        当前运行形态不支持本地数据目录(仅桌面端可用)。
      </p>
    );
  }
  if (loadError) {
    return (
      <p className="text-xs text-amber-600 dark:text-amber-400">
        读取数据目录失败:{loadError}
      </p>
    );
  }
  if (!currentPath) {
    return <p className="text-xs text-zinc-500">正在读取数据目录…</p>;
  }

  return (
    <>
      <div className="rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">当前数据目录</p>
            <p
              className="mt-1 break-all font-mono text-xs text-zinc-600 dark:text-zinc-400"
              title={currentPath}
            >
              {currentPath}
            </p>
            <p className="mt-2 text-xs text-zinc-500">
              数据库、向量索引、上传的文件、模型缓存全部保存在这里。
              切换前请先用文件管理器手动备份(应用不会自动迁移)。
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
            切换目录
          </Button>
        </div>
      </div>
      <DataDirSwitchDialog
        open={open}
        currentPath={currentPath}
        onOpenChange={setOpen}
      />
    </>
  );
};

interface DialogProps {
  open: boolean;
  currentPath: string;
  onOpenChange(open: boolean): void;
}

const DataDirSwitchDialog: React.FC<DialogProps> = ({
  open,
  currentPath,
  onOpenChange,
}) => {
  const [picked, setPicked] = React.useState<string>('');
  const [hasExisting, setHasExisting] = React.useState(false);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  // 重置 state 在每次打开时
  React.useEffect(() => {
    if (open) {
      setPicked('');
      setHasExisting(false);
      setAcknowledged(false);
      setBusy(false);
    }
  }, [open]);

  // 探测候选路径下是否已经有察元数据
  React.useEffect(() => {
    if (!picked.trim()) return;
    const handle = setTimeout(() => {
      void getDataDirApi()
        .checkExisting(picked.trim())
        .then(setHasExisting)
        .catch(() => setHasExisting(false));
    }, 300);
    return () => clearTimeout(handle);
  }, [picked]);

  const onPick = React.useCallback(async () => {
    try {
      const result = await getDataDirApi().pickDirectory({
        title: '选择新的察元 AI 数据目录',
        defaultPath: currentPath || undefined,
      });
      if (result) setPicked(result);
    } catch (e) {
      reportError(e, '选择目录失败');
    }
  }, [currentPath]);

  const onConfirm = React.useCallback(async () => {
    if (!picked.trim() || busy) return;
    setBusy(true);
    try {
      // 1. 停 sidecar(避免老 sidecar 还在写老数据目录)
      const sidecar = getPlatform().sidecar;
      if (sidecar) {
        try {
          await sidecar.kill();
        } catch {
          /* 已 stopped 也没事 */
        }
      }
      // 2. 写新数据目录(后端会 mkdir -p + 写 marker + 写 desktop.json)
      await getDataDirApi().set(picked.trim());
      // 3. 重新加载 webview;Shell 重新走 FirstRunSetup → SidecarGate
      window.location.reload();
    } catch (e) {
      setBusy(false);
      reportError(e, '切换数据目录失败');
    }
  }, [picked, busy]);

  const samePath = picked.trim() === currentPath;
  const canConfirm =
    picked.trim().length > 0 && !samePath && acknowledged && !busy;

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>切换数据目录</DialogTitle>
          <DialogDescription>
            应用会重启并使用新目录。已有的对话、知识库、本地模型缓存
            <strong> 不会自动迁移</strong>;请在切换前自行备份。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-zinc-500">新目录</label>
            <div className="mt-1 flex gap-2">
              <input
                type="text"
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="off"
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
                placeholder="点击右侧浏览按钮选择"
                className="h-9 flex-1 rounded-lg border border-zinc-300 bg-white px-3 text-sm outline-none focus:border-[var(--cy-brand-400,#818cf8)] dark:border-zinc-700 dark:bg-zinc-900"
                disabled={busy}
              />
              <button
                type="button"
                onClick={onPick}
                disabled={busy}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-zinc-300 bg-white px-3 text-sm hover:border-[var(--cy-brand-300,#a5b4fc)] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <FolderOpen className="h-4 w-4" />
                浏览
              </button>
            </div>
          </div>

          {samePath && picked && (
            <p className="text-xs text-amber-600">
              新目录与当前目录相同,无需切换。
            </p>
          )}

          {hasExisting && !samePath && (
            <div className="flex gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-900/20 dark:text-emerald-200">
              <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <p>检测到该目录已有察元数据,切换后会沿用现有数据,不会覆盖。</p>
            </div>
          )}

          <label className="flex items-start gap-2 text-xs text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              disabled={busy}
              className="mt-0.5"
            />
            <span>
              我已了解切换后旧目录的数据**不会自动迁移**,且应用会立即重启。
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            取消
          </Button>
          <Button
            size="sm"
            onClick={() => void onConfirm()}
            disabled={!canConfirm}
          >
            {busy ? (
              <>
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                切换中...
              </>
            ) : (
              '确认切换'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
