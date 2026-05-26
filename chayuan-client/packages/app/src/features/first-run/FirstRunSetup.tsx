/**
 * 首启动数据目录向导(Phase 1)。
 *
 * 仅在桌面运行时挂载。流程:
 *
 * 1. mount → 调 ``dataDirApi.state()``
 *    - ``configured=true`` → 直接放行(隐身)
 *    - ``configured=false`` → 全屏模态,要求用户选数据目录
 *
 * 2. 用户两条路径:
 *    - 「使用默认目录」:直接保存平台标准路径
 *    - 「选择其他目录」:用 ``pickDataDirectory`` 弹原生 folder picker
 *
 * 3. 选定后实时探测该路径是否已有察元数据(``checkExisting``):
 *    - 有:展示绿色提示「检测到旧数据,将自动沿用」
 *    - 无:展示中性提示「将在该目录创建新的数据库与索引」
 *
 * 4. 「确认」→ ``dataDirApi.set(path)``;后端会自动 ``mkdir -p`` + 写 marker。
 *    成功后回调 ``onConfigured``,Shell 据此切到主路由。
 *
 * 单机版重构后,这是用户启动应用唯一会被打扰的步骤。
 */

import { Button } from '@chayuan/ui';
import { CheckCircle2, FolderOpen, Loader2 } from 'lucide-react';
import * as React from 'react';
import { reportError } from '../../store/errorDialog';
import {
  type DataDirState,
  getDataDirApi,
  isDataDirSupported,
} from './dataDirApi';

export interface FirstRunSetupProps {
  /** 用户已经选好(或检测到老配置)时回调 */
  onConfigured(state: DataDirState): void;
}

type Phase = 'probing' | 'choosing' | 'saving';

export const FirstRunSetup: React.FC<FirstRunSetupProps> = ({ onConfigured }) => {
  const [phase, setPhase] = React.useState<Phase>('probing');
  const [defaultPath, setDefaultPath] = React.useState('');
  const [path, setPath] = React.useState('');
  const [hasExisting, setHasExisting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // 便携模式候选目录(``<exe_dir>/data/``);后端探测可写 + 不在 Program Files 时填,
  // 否则 null。null = 隐藏便携按钮(装机版默认行为)。
  const [portableCandidate, setPortableCandidate] = React.useState<string | null>(null);

  // 1. 启动探测
  React.useEffect(() => {
    if (!isDataDirSupported()) {
      // Web 构建 / 平台未注入 dataDir 能力 → 直接放行
      onConfigured({
        configured: true,
        path: '',
        defaultPath: '',
        configFile: '',
        hasExistingData: false,
        portableCandidate: null,
      });
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const api = getDataDirApi();
        const s = await api.state();
        if (cancelled) return;
        if (s.configured) {
          onConfigured(s);
          return;
        }
        setDefaultPath(s.defaultPath);
        setPath(s.defaultPath);
        setHasExisting(s.hasExistingData);
        setPortableCandidate(s.portableCandidate);
        setPhase('choosing');
      } catch {
        if (cancelled) return;
        // 拿不到状态意味着 Tauri 命令未注册(开发态/老版本)。直接放行,避免卡住。
        onConfigured({
          configured: true,
          path: '',
          defaultPath: '',
          configFile: '',
          hasExistingData: false,
          portableCandidate: null,
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onConfigured]);

  // 用户输入路径变化 → 重新探测「是否已有数据」(节流 300ms 避免每键探测)
  React.useEffect(() => {
    if (phase !== 'choosing' || !path.trim()) return;
    const handle = setTimeout(() => {
      void getDataDirApi()
        .checkExisting(path.trim())
        .then((b) => setHasExisting(b))
        .catch(() => setHasExisting(false));
    }, 300);
    return () => clearTimeout(handle);
  }, [path, phase]);

  const onPick = React.useCallback(async () => {
    try {
      const picked = await getDataDirApi().pickDirectory({
        defaultPath: path || defaultPath || undefined,
      });
      if (picked) setPath(picked);
    } catch (e) {
      reportError(e, '选择目录失败');
    }
  }, [defaultPath, path]);

  const onConfirm = React.useCallback(async () => {
    if (!path.trim()) {
      setError('数据目录路径不能为空');
      return;
    }
    setError(null);
    setPhase('saving');
    try {
      const next = await getDataDirApi().set(path.trim());
      onConfigured(next);
    } catch (e) {
      setPhase('choosing');
      setError(typeof e === 'string' ? e : (e as Error)?.message ?? '保存失败');
    }
  }, [path, onConfigured]);

  const useDefault = React.useCallback(() => {
    setPath(defaultPath);
  }, [defaultPath]);

  if (phase === 'probing') {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[var(--cy-surface-base,#fff)] text-sm text-[var(--cy-text-tertiary,#64748b)]">
        正在初始化察元 AI...
      </div>
    );
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[var(--cy-surface-2,#f8fafc)]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_25%,rgba(99,102,241,0.10),transparent_32%),radial-gradient(circle_at_75%_75%,rgba(14,165,233,0.10),transparent_30%)]" />
      {/* 居中容器可滚动:内容比视口高时(Linux WebKitGTK 字体偏大 / 小窗 / 已有数据提示增高)
          卡片用 my-auto —— 放得下垂直居中,放不下则滚动,绝不裁掉底部「开始使用」按钮。 */}
      <div className="absolute inset-0 flex justify-center overflow-y-auto px-6 py-8">
        <div className="my-auto w-full max-w-xl rounded-3xl border border-[var(--cy-border-subtle,#e2e8f0)] bg-[var(--cy-surface-base,#fff)]/95 p-8 shadow-[var(--cy-shadow-lg,0_24px_80px_rgba(15,23,42,0.12))] backdrop-blur-xl">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--cy-brand-500,#6366f1)] text-base font-bold text-white">
              察
            </div>
            <div>
              <h1 className="text-lg font-semibold text-[var(--cy-text-primary,#0f172a)]">
                欢迎使用察元 AI
              </h1>
              <p className="text-xs text-[var(--cy-text-tertiary,#94a3b8)]">
                只需选择一个数据目录,即可开始使用。其它配置开箱即用。
              </p>
            </div>
          </div>

          <p className="mb-2 text-sm font-medium text-[var(--cy-text-primary,#0f172a)]">
            数据目录
          </p>
          <p className="mb-3 text-xs leading-relaxed text-[var(--cy-text-secondary,#475569)]">
            察元 AI 会把数据库、向量索引、上传的文件、本地模型缓存全部保存在这里。
            选择一个有充足空间的目录(建议 ≥ 5 GB)。
          </p>

          <div className="flex gap-2">
            <input
              type="text"
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder={defaultPath}
              className="h-10 flex-1 rounded-xl border border-[var(--cy-border-subtle,#e2e8f0)] bg-[var(--cy-surface-1,#f1f5f9)] px-3 text-sm text-[var(--cy-text-primary,#0f172a)] outline-none transition-colors focus:border-[var(--cy-brand-400,#818cf8)]"
            />
            <button
              type="button"
              onClick={onPick}
              className="inline-flex h-10 items-center gap-1.5 rounded-xl border border-[var(--cy-border-subtle,#e2e8f0)] bg-[var(--cy-surface-1,#f1f5f9)] px-3 text-sm font-medium text-[var(--cy-text-secondary,#475569)] transition-colors hover:border-[var(--cy-brand-300,#a5b4fc)] hover:text-[var(--cy-text-primary,#0f172a)]"
              disabled={phase === 'saving'}
            >
              <FolderOpen className="h-4 w-4" />
              浏览
            </button>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
            <button
              type="button"
              onClick={useDefault}
              className="text-xs text-[var(--cy-brand-600,#4f46e5)] hover:underline"
              disabled={phase === 'saving' || path === defaultPath}
            >
              使用平台默认 ({truncatePath(defaultPath)})
            </button>
            {/* 便携模式:Tauri 后端探测 exe 同级 data/ 可写 + 不在 Program Files 时才出现。
                MSI 装到 Program Files 用户态写不了,不出现;解压版到 D:\Chayuan\ 才出现。
                选这条 → 整目录拷到 U 盘/别的电脑就能直接用,不带用户名。 */}
            {portableCandidate && (
              <button
                type="button"
                onClick={() => setPath(portableCandidate)}
                className="text-xs text-[var(--cy-brand-600,#4f46e5)] hover:underline"
                disabled={phase === 'saving' || path === portableCandidate}
                title="便携模式:数据存在安装目录旁,整目录拷到 U 盘 / 别的电脑可直接用"
              >
                使用便携模式 ({truncatePath(portableCandidate)})
              </button>
            )}
          </div>

          {/* 旧数据提示:绿色 = 检测到老数据,将沿用 */}
          {hasExisting ? (
            <div className="mt-4 flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-900/20 dark:text-emerald-200">
              <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>
                <p className="font-medium">检测到该目录已有察元数据</p>
                <p className="mt-1 text-emerald-800/80 dark:text-emerald-200/70">
                  应用会自动沿用现有数据(知识库 / 历史会话 / 本地模型缓存),不会覆盖。
                </p>
              </div>
            </div>
          ) : (
            <div className="mt-4 rounded-xl border border-[var(--cy-border-subtle,#e2e8f0)] bg-[var(--cy-surface-1,#f1f5f9)] p-3 text-xs text-[var(--cy-text-secondary,#475569)]">
              该目录为空,应用首次启动时会在此创建数据库与索引。
            </div>
          )}

          {error && (
            <p className="mt-3 text-xs text-red-600">{error}</p>
          )}

          <div className="mt-6 flex justify-end gap-2">
            <Button
              size="sm"
              onClick={() => void onConfirm()}
              disabled={phase === 'saving' || !path.trim()}
            >
              {phase === 'saving' ? (
                <>
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  保存中...
                </>
              ) : (
                <>开始使用</>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

/** 太长的路径在按钮里显示不下,中段省略。 */
function truncatePath(p: string, max = 48): string {
  if (!p || p.length <= max) return p;
  const head = p.slice(0, Math.floor(max / 2) - 2);
  const tail = p.slice(p.length - Math.ceil(max / 2) + 2);
  return `${head}...${tail}`;
}
