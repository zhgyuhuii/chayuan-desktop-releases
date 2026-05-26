/**
 * 「模型框架」+「默认模型」面板。
 *
 * 顶部一行：``RuntimeFrameworkCards`` —— 11 个 adapter 卡片，每张显示
 *   * 图标 / 名称（Ollama / vLLM / Infinity / ComfyUI / FunASR / ...）
 *   * 健康状态徽章（绿/橙/灰）
 *   * 它能服务的 capability tag（"对话 / 文本嵌入 / 图像嵌入"）
 *   * 已经在它上面注册的本地模型数量
 *   * URL（绿色：可用；灰色：未配置）
 *   * 操作按钮：
 *       - "一键安装"（``install_kind ∈ {one-click, pip}`` 才显示，否则灰）
 *       - "查看安装命令"（永远可点 → 弹 ``RuntimeInstallWizard``）
 *
 * 下面一行：``DefaultModelsRow`` —— 9 类 capability 的"默认模型"卡片。
 *   每张卡片：
 *     * capability 中文名 + 已选 model（运行时 / 格式 / 大小）
 *     * 下拉选其它候选；候选为空 → "去推荐"按钮直接跳到 CapabilityCenter
 */
import * as React from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  cn,
} from '@chayuan/ui';
import {
  aiPlatform,
  type DefaultsCandidate,
  type DefaultsResponse,
  type RuntimeCardDto,
  type RuntimeInstallTask,
  type RuntimesResponse,
} from '@chayuan/api';
import { notifyInfo, notifySuccess, reportError } from '../../store/errorDialog';

const CAPABILITY_ZH: Record<string, string> = {
  chat: '对话',
  embedding: '文本嵌入',
  clip: '图像嵌入',
  rerank: '重排',
  t2i: '文生图',
  t2v: '文生视频',
  tts: '语音合成',
  asr: '语音识别',
  ocr: '图像识别文字',
};

// 与设置面板里 CapabilityCenter 用到的 9 个 tab 对齐的反向 map
const CAP_TO_PANEL_TAB: Record<string, string> = {
  chat: 'chat',
  embedding: 'text-embedding',
  clip: 'image-embedding',
  rerank: 'rerank',
  t2i: 'text-to-image',
  t2v: 'text-to-video',
  tts: 'text-to-speech',
  asr: 'asr',
  ocr: 'ocr',
};

function fmtSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** 候选 model id 的短显:本地 scanner 路径(models/bundled/chat/Qwen3-...-GGUF)
 *  只取最后一段 + 去 -GGUF 后缀;云厂商裸 id (deepseek-v4-flash) 原样。 */
function shortLabel(id: string): string {
  if (!id.includes('/')) return id;
  const tail = id.split('/').filter(Boolean).pop() ?? id;
  return tail.replace(/-(gguf|GGUF)$/, '');
}

/** 平台显示名 + 排序:本地 sidecar(local-chat / local-embedding / ...)归一到「本地模型」
 *  排首位;云厂商按名称字母序。platform_name 缺失或异常 → 「其它」分组。 */
function platformGroupKey(p: string | null | undefined): { key: string; label: string } {
  if (!p) return { key: '__other__', label: '其它' };
  if (p === 'local' || p.startsWith('local-')) {
    return { key: '__local__', label: '本地模型' };
  }
  return { key: p, label: p };
}

function groupCandidates(
  candidates: DefaultsCandidate[],
): Array<{ label: string; items: DefaultsCandidate[] }> {
  const map = new Map<string, { label: string; items: DefaultsCandidate[] }>();
  for (const c of candidates) {
    const { key, label } = platformGroupKey(c.platform_name);
    const entry = map.get(key) ?? { label, items: [] };
    entry.items.push(c);
    map.set(key, entry);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => {
      if (a === '__local__') return -1;
      if (b === '__local__') return 1;
      if (a === '__other__') return 1;
      if (b === '__other__') return -1;
      return a.localeCompare(b);
    })
    .map(([, v]) => ({
      label: v.label,
      items: v.items.slice().sort((x, y) => x.id.localeCompare(y.id)),
    }));
}

// ===========================================================================
// 顶部：模型框架卡片
// ===========================================================================

interface RuntimeCenterProps {
  /** 父组件 (`AiPlatformPanel`) 注入：用户点 "去推荐" 时切换 capability tab。 */
  onJumpToCapability?: (panelTab: string) => void;
}

export const RuntimeCenter: React.FC<RuntimeCenterProps> = ({ onJumpToCapability }) => {
  const [runtimes, setRuntimes] = React.useState<RuntimesResponse | null>(null);
  const [defaults, setDefaults] = React.useState<DefaultsResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [wizardFor, setWizardFor] = React.useState<RuntimeCardDto | null>(null);

  const reload = React.useCallback(async () => {
    setLoading(true);
    try {
      const [rt, df] = await Promise.all([
        aiPlatform.runtimes.list(),
        aiPlatform.defaults.list(),
      ]);
      setRuntimes(rt);
      setDefaults(df);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void reload();
    const t = window.setInterval(() => void reload(), 30_000);
    return () => window.clearInterval(t);
  }, [reload]);

  if (loading && !runtimes) {
    return <div className="text-xs text-zinc-500">加载中…</div>;
  }
  if (error) {
    return (
      <div className="text-xs text-amber-600">
        无法连上 /v1/admin/runtimes：{error}
      </div>
    );
  }
  if (!runtimes || !defaults) return null;

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-sm font-semibold">模型框架</h3>
        <p className="mb-2 text-[11px] text-zinc-500 leading-relaxed">
          每个框架对应一种 runtime 适配器。绿色徽章 = 服务正在运行；橙色 =
          已配置但 ping 不通；灰色 = 未安装。点卡片可一键安装或查看跨平台安装命令。
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {runtimes.runtimes.map((r) => (
            <RuntimeCard
              key={r.name}
              card={r}
              onClick={() => setWizardFor(r)}
            />
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold">默认模型</h3>
        <p className="mb-2 text-[11px] text-zinc-500 leading-relaxed">
          为每一类能力指定默认模型；后续聊天 / 嵌入 / 文生图 / 文转音 等请求未显式
          传 ``model`` 时，将走这里设置的模型。
        </p>
        <DefaultModelsRow
          defaults={defaults}
          onChanged={() => void reload()}
          onJumpToCapability={onJumpToCapability}
        />
      </section>

      {wizardFor && (
        <RuntimeInstallWizard
          card={wizardFor}
          hostOs={runtimes.host_os}
          onClose={(installed) => {
            setWizardFor(null);
            if (installed) void reload();
          }}
        />
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// 单张运行时卡片
// ---------------------------------------------------------------------------

const RuntimeCard: React.FC<{
  card: RuntimeCardDto;
  onClick: () => void;
}> = ({ card, onClick }) => {
  const tone = card.health === 'healthy'
    ? 'border-emerald-300 bg-emerald-50/40 dark:border-emerald-800 dark:bg-emerald-900/20'
    : card.health === 'configured'
      ? 'border-amber-300 bg-amber-50/40 dark:border-amber-800 dark:bg-amber-900/20'
      : 'border-zinc-200 bg-zinc-50/40 dark:border-zinc-700 dark:bg-zinc-900/40';
  const dot = card.health === 'healthy'
    ? 'bg-emerald-500'
    : card.health === 'configured'
      ? 'bg-amber-500'
      : 'bg-zinc-400';
  const installable = card.install_kind === 'one-click' || card.install_kind === 'pip';

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`runtime-card-${card.name}`}
      className={[
        'group flex flex-col gap-2 rounded-xl border p-3 text-left transition-all',
        'hover:-translate-y-0.5 hover:shadow-sm',
        tone,
      ].join(' ')}
    >
      <div className="flex items-center gap-2">
        <span className={['inline-block h-2 w-2 shrink-0 rounded-full', dot].join(' ')} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{card.label}</div>
          <div className="truncate font-mono text-[10px] text-zinc-500">{card.name}</div>
        </div>
        <span className="rounded bg-zinc-200/70 px-1.5 py-0.5 text-[10px] text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">
          {card.models_served} 模型
        </span>
      </div>

      <div className="flex flex-wrap gap-1">
        {card.category_labels.map((cl) => (
          <span
            key={cl}
            className="rounded bg-white px-1.5 py-0.5 text-[10px] text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
          >
            {cl}
          </span>
        ))}
        {card.needs_gpu && (
          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] text-purple-700 dark:bg-purple-900/40 dark:text-purple-200">
            需 GPU
          </span>
        )}
      </div>

      <div className="flex items-center justify-between text-[10px] text-zinc-500">
        <span className="truncate">{card.url ?? '未配置 URL'}</span>
        <span
          className={[
            'shrink-0 rounded px-1.5 py-0.5',
            installable
              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200'
              : 'bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-200',
          ].join(' ')}
        >
          {card.install_kind === 'one-click'
            ? '一键安装'
            : card.install_kind === 'pip'
              ? 'pip 安装'
              : card.install_kind === 'docker'
                ? '需 Docker'
                : '需手动'}
        </span>
      </div>
    </button>
  );
};

// ---------------------------------------------------------------------------
// 安装向导（Modal）
// ---------------------------------------------------------------------------

const OS_LABEL: Record<string, string> = {
  'linux-apt': 'Linux · apt',
  'linux-pacman': 'Linux · pacman',
  'mac-brew': 'macOS · brew',
  'win-winget': 'Windows · winget',
  docker: 'Docker',
};

const RuntimeInstallWizard: React.FC<{
  card: RuntimeCardDto;
  hostOs: 'linux' | 'mac' | 'win';
  onClose: (installed: boolean) => void;
}> = ({ card, hostOs, onClose }) => {
  const [task, setTask] = React.useState<RuntimeInstallTask | null>(null);
  const [autoBusy, setAutoBusy] = React.useState(false);
  const [installed, setInstalled] = React.useState(false);

  const startAuto = React.useCallback(async () => {
    setAutoBusy(true);
    try {
      const t = await aiPlatform.runtimes.install(card.name);
      setTask(t);
      notifyInfo('已开始安装', `${card.label}：后台运行，结束后会自动刷新框架列表。`);
      // 简单轮询；20s 兜底
      const start = Date.now();
      const tick = window.setInterval(async () => {
        try {
          const s = await aiPlatform.runtimes.status(t.task_id);
          setTask(s);
          if (s.state === 'done' || s.state === 'failed' || Date.now() - start > 600_000) {
            window.clearInterval(tick);
            if (s.state === 'done') {
              notifySuccess('安装完成', card.label);
              setInstalled(true);
            } else if (s.state === 'failed') {
              notifyInfo('安装失败', `${card.label} 安装未成功；可改用手动命令。`, 5000);
            }
          }
        } catch {
          // 忽略；下次轮询继续
        }
      }, 1500);
    } catch (e: unknown) {
      reportError(e, `启动安装失败：${card.label}`);
    } finally {
      setAutoBusy(false);
    }
  }, [card]);

  const close = () => onClose(installed);

  // 卡片支持的"原生命令"：按 host_os 优先
  const recipes = card.install_recipes || {};
  const preferredOsKey =
    hostOs === 'mac' ? 'mac-brew' : hostOs === 'win' ? 'win-winget' : 'linux-apt';
  const preferred = recipes[preferredOsKey] ?? [];

  return (
    <Dialog open onOpenChange={(o) => !o && close()}>
      <DialogContent
        className={cn(
          // 覆盖 max-w-lg 默认 + 适应安装日志(可能很长)的高度
          'max-w-2xl max-h-[88vh] overflow-y-auto p-4',
        )}
      >
        <div className="mb-2 flex items-baseline justify-between pr-8">
          <DialogTitle className="text-base font-semibold">
            {card.label} · 安装与配置
          </DialogTitle>
        </div>

        <p className="mb-3 text-[11px] text-zinc-600 dark:text-zinc-400">
          能力：
          {card.category_labels.join(' / ')} · 安装方式：
          <code className="font-mono">{card.install_kind}</code>
          {card.url ? (
            <>
              {' '}
              · URL <code className="font-mono">{card.url}</code>
            </>
          ) : null}
        </p>

        {/* —— 一键安装 —— */}
        {(card.install_kind === 'one-click' || card.install_kind === 'pip') && (
          <section className="mb-3 rounded border border-emerald-300 bg-emerald-50 p-3 dark:border-emerald-800 dark:bg-emerald-900/20">
            <div className="text-xs font-medium text-emerald-900 dark:text-emerald-200">
              {card.install_kind === 'one-click'
                ? '一键安装（推荐）'
                : `pip 自动安装：${card.default_pip_package ?? ''}`}
            </div>
            <p className="mt-1 text-[11px] text-zinc-700 dark:text-zinc-300">
              点击下方按钮，察元会在本地以子进程的方式直接执行安装；过程会推到事件总线，
              安装结束后框架卡片自动刷新。
            </p>
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                disabled={
                  autoBusy ||
                  !!task && task.state === 'running'
                }
                onClick={() => void startAuto()}
                data-testid={`auto-install-${card.name}`}
                className="rounded border border-emerald-500 bg-emerald-100 px-3 py-1 text-xs text-emerald-700 hover:bg-emerald-200 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200"
              >
                {task?.state === 'running' ? '安装中…' : '一键安装'}
              </button>
              {task?.state && (
                <span className="text-[11px] text-zinc-500">
                  状态：{task.state}
                  {task.return_code !== undefined ? ` · rc=${task.return_code}` : ''}
                </span>
              )}
            </div>

            {task?.log && task.log.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] text-zinc-500">
                  安装日志（最后 {Math.min(task.log.length, 20)} 行）
                </summary>
                <pre className="mt-1 max-h-40 overflow-auto rounded bg-zinc-900 p-2 font-mono text-[11px] text-zinc-100">
                  {task.log.slice(-20).join('\n')}
                </pre>
              </details>
            )}
          </section>
        )}

        {/* —— 手动命令 —— */}
        <section className="rounded border border-zinc-200 p-3 dark:border-zinc-700">
          <div className="mb-1 text-xs font-medium">
            或者手动安装（按本机 ``{hostOs}`` 已优先排序）
          </div>
          {preferred.length > 0 && (
            <details open className="mb-2 text-[11px]">
              <summary className="cursor-pointer">{OS_LABEL[preferredOsKey] ?? preferredOsKey}（推荐）</summary>
              <pre className="mt-1 overflow-x-auto rounded bg-zinc-900 px-2 py-1 font-mono text-[11px] text-zinc-100">
                {preferred.join('\n')}
              </pre>
            </details>
          )}
          {Object.entries(recipes)
            .filter(([os]) => os !== preferredOsKey)
            .map(([os, cmds]) => (
              <details key={os} className="mb-1 text-[11px]">
                <summary className="cursor-pointer">{OS_LABEL[os] ?? os}</summary>
                <pre className="mt-1 overflow-x-auto rounded bg-zinc-900 px-2 py-1 font-mono text-[11px] text-zinc-100">
                  {(cmds as string[]).join('\n')}
                </pre>
              </details>
            ))}
          {Object.keys(recipes).length === 0 && (
            <p className="text-[11px] text-zinc-500">
              暂无内建命令；请查阅官方文档。
            </p>
          )}
        </section>

        <p className="mt-3 text-[11px] text-zinc-500">
          安装完成后:察元会自动探测端口并写入 <code>runtime.json</code>;模型清单会
          通过 <code>discovery</code> 自动同步到设置面板的"已安装"列表。
        </p>
      </DialogContent>
    </Dialog>
  );
};

// ===========================================================================
// 9 类默认模型选择
// ===========================================================================

const DefaultModelsRow: React.FC<{
  defaults: DefaultsResponse;
  onChanged: () => void;
  onJumpToCapability?: (panelTab: string) => void;
}> = ({ defaults, onChanged, onJumpToCapability }) => {
  const onPick = React.useCallback(
    async (cap: string, modelId: string) => {
      try {
        await aiPlatform.defaults.setMany({ [cap]: modelId });
        notifySuccess('已设为默认', `${CAPABILITY_ZH[cap] ?? cap} → ${modelId}`);
        onChanged();
      } catch (e: unknown) {
        reportError(e, '保存默认模型失败');
      }
    },
    [onChanged],
  );

  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {defaults.capabilities.map((cap) => (
        <DefaultCapabilityCard
          key={cap}
          cap={cap}
          current={defaults.defaults[cap] ?? null}
          candidates={defaults.candidates[cap] ?? []}
          onPick={onPick}
          onJumpToCapability={onJumpToCapability}
        />
      ))}
    </div>
  );
};

const DefaultCapabilityCard: React.FC<{
  cap: string;
  current: string | null;
  candidates: DefaultsCandidate[];
  onPick: (cap: string, modelId: string) => Promise<void>;
  onJumpToCapability?: (panelTab: string) => void;
}> = ({ cap, current, candidates, onPick, onJumpToCapability }) => {
  const empty = candidates.length === 0;
  const currentItem = candidates.find((c) => c.id === current);
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900"
      data-testid={`default-card-${cap}`}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">{CAPABILITY_ZH[cap] ?? cap}</span>
        <span className="font-mono text-[10px] text-zinc-500">{cap}</span>
      </div>

      {empty ? (
        <div className="mt-2 text-[11px] text-zinc-500">
          未安装；
          <button
            type="button"
            onClick={() => onJumpToCapability?.(CAP_TO_PANEL_TAB[cap] ?? cap)}
            className="text-emerald-600 underline hover:text-emerald-700"
          >
            去推荐
          </button>
        </div>
      ) : (
        <>
          <select
            value={current ?? ''}
            onChange={(e) => void onPick(cap, e.target.value)}
            data-testid={`default-select-${cap}`}
            className="mt-2 h-7 w-full rounded border border-zinc-300 bg-transparent px-2 text-[11px] dark:border-zinc-700"
          >
            <option value="" disabled>
              请选择
            </option>
            {groupCandidates(candidates).map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.items.map((c) => (
                  <option key={c.id} value={c.id}>
                    {shortLabel(c.id)}
                    {c.size_bytes ? ` · ${fmtSize(c.size_bytes)}` : ''}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {currentItem && (
            <div className="mt-1 truncate text-[10px] text-zinc-500">
              当前：runtime={currentItem.runtime ?? '-'}
              {currentItem.format ? ` · format=${currentItem.format}` : ''}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default RuntimeCenter;
