/**
 * 「9 类模型库」中心：左 capability 导航 / 右 已装+推荐+自定义。
 *
 * 设计目标
 * --------
 *
 * 用户在 设置 → AI 平台 里：
 *
 * * 不再面对一坨 ``<details>`` 折叠列表；而是 ① 左边一列 9 个能力 tab、
 *   ② 右边一栏 "我的"（已安装） / "推荐" / "自定义" 三段。
 * * 每个 ``capability`` 都有"默认模型"概念；用户能在 "我的" 里"设为默认"。
 *   特别地，``image-embedding`` 的默认 = chat 上传图片时走的向量化模型。
 * * 推荐项一键安装；点击后下方出现进度条；后端自动 rescan + discovery，
 *   完成后 SSE 推 ``rescanned``，前端刷新模型库。
 * * 自定义：粘 hf_repo 名字 + 选 capability + 安装。
 *
 * 与 marketplace 的关系
 * ---------------------
 *
 * `MarketplacePage` 是"全网模型广场"——按厂商展开，列出云 / 内网模型；
 * 这里是"本地能跑的引擎栈"——是 chayuan-server / 适配器层认识的那批；
 * 二者公用同一份 ``aiPlatform`` API、共享 react-query 缓存键。
 */
import * as React from 'react';
import { Cpu } from 'lucide-react';
import {
  aiPlatform,
  aiPlatformRecommended,
  type AiPlatformModel,
  type Capability,
  type LocalRuntimeCapability,
  type ModelLocateResponse,
  type RecommendedListResponse,
  type RecommendedModelDto,
  type PullProgress,
} from '@chayuan/api';
import { Button } from '@chayuan/ui';
import { notifyInfo } from '../../store/errorDialog';
import { useLocalRuntimeStore } from '../../store/localRuntime';

const CAPABILITY_LABELS: Record<Capability, string> = {
  chat: '对话',
  'text-embedding': '文本嵌入',
  'image-embedding': '图像嵌入',
  rerank: '重排',
  'text-to-image': '文生图',
  'text-to-video': '文生视频',
  'text-to-speech': '语音合成',
  asr: '语音识别',
  ocr: '图像识别文字',
};

const CAPABILITY_ORDER: Capability[] = [
  'chat',
  'text-embedding',
  'image-embedding',
  'rerank',
  'text-to-image',
  'text-to-video',
  'text-to-speech',
  'asr',
  'ocr',
];

interface CapabilityCenterProps {
  /** 当前是否要求登录（auth_required）；未登录时安装按钮全 disable，渲染提示。 */
  authRequired?: boolean;
  /** 用户是否登录；与 authRequired 联合判定能否操作。 */
  isLoggedIn?: boolean;
}

function fmtSizeMB(mb: number): string {
  if (!mb || mb <= 0) return '-';
  if (mb < 1024) return `${mb} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

// ---------------------------------------------------------------------------
// 数据 hooks
// ---------------------------------------------------------------------------

function useCapabilityData() {
  const [recommended, setRecommended] = React.useState<RecommendedListResponse | null>(null);
  const [models, setModels] = React.useState<AiPlatformModel[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    setLoading(true);
    try {
      const [rec, list] = await Promise.all([
        aiPlatformRecommended.list(),
        aiPlatform.models.list(),
      ]);
      setRecommended(rec);
      setModels(list);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  // 监听后端"模型已新增 / 已删除"事件，自动刷新（懒注册：第一次有数据后再订阅）
  React.useEffect(() => {
    if (!recommended) return;
    let cancelled = false;
    const unsub = aiPlatform.models.subscribeEvents?.({
      onAdded: () => { if (!cancelled) void reload(); },
      onUpdated: () => { if (!cancelled) void reload(); },
      onRemoved: () => { if (!cancelled) void reload(); },
      onError: () => { /* SSE not available; UI 仍可手动刷新 */ },
    });
    return () => {
      cancelled = true;
      try { unsub?.(); } catch { /* noop */ }
    };
  }, [recommended, reload]);

  return { recommended, models, loading, error, reload };
}

// ---------------------------------------------------------------------------
// 安装进度（每个 capability 共享一个"当前正在装的列表"）
// ---------------------------------------------------------------------------

interface ActiveInstall {
  taskId: string;
  repo: string;
  state: PullProgress['state'];
  percent: number;
  bytesDone: number;
  bytesTotal: number;
  message?: string;
}

function useInstallTracker(onDone: () => void) {
  const [active, setActive] = React.useState<Record<string, ActiveInstall>>({});
  const cleanupsRef = React.useRef<Record<string, () => void>>({});

  const start = React.useCallback(
    (rec: RecommendedModelDto, capability: Capability) => {
      void (async () => {
        try {
          const task = await aiPlatformRecommended.install(rec.hf_repo, { capability });
          const key = task.task_id;
          setActive((prev) => ({
            ...prev,
            [key]: {
              taskId: key,
              repo: rec.id,
              state: 'queued',
              percent: 0,
              bytesDone: 0,
              bytesTotal: rec.size_mb * 1024 * 1024,
            },
          }));
          const cleanup = aiPlatformRecommended.streamProgress(
            key,
            (p) => {
              setActive((prev) => ({
                ...prev,
                [key]: {
                  taskId: key,
                  repo: rec.id,
                  state: p.state ?? 'running',
                  percent: typeof p.percent === 'number' ? p.percent : (
                    p.bytes_total ? (p.bytes_done ?? 0) / p.bytes_total * 100 : 0
                  ),
                  bytesDone: p.bytes_done ?? 0,
                  bytesTotal: p.bytes_total ?? rec.size_mb * 1024 * 1024,
                  message: p.message,
                },
              }));
              if (p.state === 'done' || p.state === 'rescanned') {
                // 后端 scan 完触发 onDone；之后清理 entry
                onDone();
                window.setTimeout(() => {
                  setActive((prev) => {
                    const next = { ...prev };
                    delete next[key];
                    return next;
                  });
                }, 2_000);
              }
            },
            () => {
              try { cleanupsRef.current[key]?.(); } catch { /* noop */ }
              delete cleanupsRef.current[key];
            },
          );
          cleanupsRef.current[key] = cleanup;
        } catch (e: unknown) {
          // 启动安装就失败：直接拿 repo 当 key 存一条 error 记录
          setActive((prev) => ({
            ...prev,
            [`err-${rec.id}`]: {
              taskId: `err-${rec.id}`,
              repo: rec.id,
              state: 'error',
              percent: 0,
              bytesDone: 0,
              bytesTotal: 0,
              message: e instanceof Error ? e.message : String(e),
            },
          }));
        }
      })();
    },
    [onDone],
  );

  React.useEffect(
    () => () => {
      for (const c of Object.values(cleanupsRef.current)) {
        try { c?.(); } catch { /* noop */ }
      }
    },
    [],
  );

  return { active, start };
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

export const CapabilityCenter: React.FC<CapabilityCenterProps> = ({
  authRequired,
  isLoggedIn,
}) => {
  const data = useCapabilityData();
  const [activeCap, setActiveCap] = React.useState<Capability>('chat');
  const localRuntime = useLocalRuntimeStore();

  // 把 panel cap 字符串映射到 LocalRuntimeCapability;embedding / rerank / asr / image-embedding 支持本地 runtime
  const localCap: LocalRuntimeCapability | null =
    activeCap === 'text-embedding' ? 'embedding' :
    activeCap === 'rerank' ? 'rerank' :
    activeCap === 'asr' ? 'asr' :
    activeCap === 'image-embedding' ? 'image-embedding' :
    null;
  const localStatus = localCap ? localRuntime.statuses[localCap] : null;
  const localPending = localCap ? localRuntime.pendingFor[localCap] : null;

  // 来自 RuntimeCenter "去推荐" 按钮的跳转 —— 切到对应 capability tab
  React.useEffect(() => {
    const onJump = (e: Event) => {
      const detail = (e as CustomEvent<{ capability?: string }>).detail;
      const target = detail?.capability;
      if (!target) return;
      // capability 字面量必须命中 9 个 tab 之一
      if ((CAPABILITY_ORDER as string[]).includes(target)) {
        setActiveCap(target as Capability);
      }
    };
    window.addEventListener('chayuan:capability-jump', onJump);
    return () => window.removeEventListener('chayuan:capability-jump', onJump);
  }, []);
  const [customRepo, setCustomRepo] = React.useState('');
  const installer = useInstallTracker(() => void data.reload());

  // capability 限制：未登录 + auth_required = 不能装
  const canInstall = !authRequired || !!isLoggedIn;

  const installedByCap = React.useMemo(() => {
    const map = new Map<Capability, AiPlatformModel[]>();
    for (const c of CAPABILITY_ORDER) map.set(c, []);
    for (const m of data.models) {
      const c = (m.capability ?? 'chat') as Capability;
      (map.get(c) ?? map.get('chat')!).push(m);
    }
    return map;
  }, [data.models]);

  const recommendedByCap = data.recommended?.recommended ?? {};

  const setDefault = React.useCallback(
    async (cap: Capability, modelId: string) => {
      try {
        await aiPlatform.models.setDefault?.(cap, modelId);
        await data.reload();
      } catch {
        // setDefault 可能未实现；这里静默——UI 体验不能因为它崩
      }
    },
    [data],
  );

  const installCustom = React.useCallback(() => {
    const repo = customRepo.trim();
    if (!repo) return;
    installer.start(
      {
        id: repo,
        capability: activeCap,
        runtime: 'auto',
        hf_repo: repo,
        size_mb: 0,
        intent: '自定义安装',
        offline_friendly: false,
        optional_for_lite: true,
        id_aliases: [],
      },
      activeCap,
    );
    setCustomRepo('');
  }, [activeCap, customRepo, installer]);

  const installedCount = (cap: Capability) => installedByCap.get(cap)?.length ?? 0;
  const activeInstalls = Object.values(installer.active).filter(
    (a) => a.state !== 'done' && a.state !== 'rescanned',
  );

  if (data.loading && !data.recommended) {
    return <div className="text-xs text-zinc-500">加载中…</div>;
  }
  if (data.error) {
    return (
      <div className="text-xs text-amber-600">
        无法连上 /v1/admin/recommended_models：{data.error}
      </div>
    );
  }

  return (
    <div className="flex h-[55vh] gap-4">
      {/* 左侧 9 个 capability tab */}
      <nav
        className="w-44 shrink-0 overflow-y-auto rounded-md border border-zinc-200 bg-zinc-50/40 dark:border-zinc-700 dark:bg-zinc-900/40"
        aria-label="模型能力"
      >
        <ul className="text-sm">
          {CAPABILITY_ORDER.map((cap) => {
            const active = activeCap === cap;
            return (
              <li key={cap}>
                <button
                  type="button"
                  data-testid={`cap-tab-${cap}`}
                  onClick={() => setActiveCap(cap)}
                  className={[
                    'flex w-full items-center justify-between border-l-2 px-3 py-2 text-left transition-colors',
                    active
                      ? 'border-l-emerald-500 bg-white dark:bg-zinc-800/80'
                      : 'border-l-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800/60',
                  ].join(' ')}
                >
                  <span>{CAPABILITY_LABELS[cap]}</span>
                  <span
                    className={[
                      'rounded-full px-1.5 text-[10px]',
                      installedCount(cap) > 0
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200'
                        : 'bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300',
                    ].join(' ')}
                  >
                    {installedCount(cap)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* 右侧主体 */}
      <section className="flex-1 overflow-y-auto pr-1">
        {!canInstall && (
          <div
            className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200"
            data-testid="install-gated-warning"
          >
            服务端要求登录后才能下载 / 安装模型；请先在右上角登录后再操作。
          </div>
        )}

        {localCap && (
          <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2 text-xs">
            <Cpu className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />
            <span className="text-[var(--cy-text-secondary)]">本地 runtime:</span>
            {localStatus?.state === 'ready' ? (
              <code className="text-emerald-700">运行中 ({localStatus.endpoint})</code>
            ) : (
              <span className="text-[var(--cy-text-tertiary)]">
                {localStatus?.state === 'failed' ? `失败:${localStatus.last_error ?? ''}` : '未运行'}
              </span>
            )}
            <div className="flex-1" />
            <Button
              size="sm"
              variant="outline"
              disabled={localPending !== null || localStatus?.state === 'ready'}
              onClick={() => void localRuntime.startCapability(localCap)}
            >
              {localPending === 'start' ? '启动中…' : '启动本地 runtime'}
            </Button>
          </div>
        )}

        <h4 className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
          已安装 · {CAPABILITY_LABELS[activeCap]}
        </h4>
        <ul className="mb-4 space-y-1.5 text-sm">
          {(installedByCap.get(activeCap) ?? []).length === 0 && (
            <li className="rounded bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-900/40">
              尚未安装；从下方"推荐"挑一个 ↓
            </li>
          )}
          {(installedByCap.get(activeCap) ?? []).map((m) => (
            <InstalledModelRow
              key={m.id}
              model={m}
              capability={activeCap}
              onSetDefault={() => void setDefault(activeCap, m.id)}
            />
          ))}
        </ul>

        <h4 className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
          推荐
        </h4>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {(recommendedByCap[activeCap] ?? []).map((rec) => (
            <RecommendedCard
              key={rec.id}
              rec={rec}
              capability={activeCap}
              canInstall={canInstall}
              installing={Object.values(installer.active).find((a) => a.repo === rec.id)}
              onInstall={() => installer.start(rec, activeCap)}
            />
          ))}
          {(!recommendedByCap[activeCap] || recommendedByCap[activeCap]!.length === 0) && (
            <div className="col-span-2 rounded bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-900/40">
              暂无推荐；可使用下方"自定义安装"。
            </div>
          )}
        </div>

        <h4 className="mb-2 mt-4 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
          自定义安装
        </h4>
        <div className="flex items-center gap-2">
          <input
            value={customRepo}
            onChange={(e) => setCustomRepo(e.target.value)}
            placeholder="hf-repo，例如 BAAI/bge-m3 或 Qwen/Qwen2.5-7B-Instruct"
            className="h-8 flex-1 rounded border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"
            data-testid="custom-repo-input"
          />
          <button
            type="button"
            disabled={!canInstall || !customRepo.trim()}
            onClick={installCustom}
            data-testid="custom-repo-install"
            className="h-8 rounded border border-emerald-500 bg-emerald-50 px-3 text-xs text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200"
          >
            安装到 {CAPABILITY_LABELS[activeCap]}
          </button>
        </div>

        {activeInstalls.length > 0 && (
          <div className="mt-4 space-y-1.5 text-xs">
            <h4 className="font-semibold text-zinc-700 dark:text-zinc-200">下载进度</h4>
            {activeInstalls.map((a) => (
              <div
                key={a.taskId}
                className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-700"
                data-testid={`install-progress-${a.repo}`}
              >
                <div className="flex justify-between">
                  <span className="font-mono">{a.repo}</span>
                  <span className="text-zinc-500">
                    {a.state} · {a.percent.toFixed(1)}%
                  </span>
                </div>
                <div className="mt-1 h-1 rounded bg-zinc-200 dark:bg-zinc-800">
                  <div
                    className="h-1 rounded bg-emerald-500 transition-[width]"
                    style={{ width: `${Math.max(0, Math.min(100, a.percent))}%` }}
                  />
                </div>
                {a.message && (
                  <div className="mt-1 text-[10px] text-zinc-500">{a.message}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 已安装模型一行：展开后异步拉 ``/v1/admin/models/<id>/locate`` 显示文件路径。
//
// 设计目标：
//   * 默认收起（不要一加载就给后端连发 N 次 locate 请求，N 是已装模型数量）；
//   * 一旦展开 → 拉一次，缓存到组件 state；
//   * 路径可复制（``navigator.clipboard.writeText``，没权限时降级 toast）；
//   * Ollama 给"blob digest 列表"小折叠；HF cache 给 snapshot 路径；ComfyUI / local
//     给主权重路径。
// ---------------------------------------------------------------------------

const InstalledModelRow: React.FC<{
  model: AiPlatformModel;
  capability: Capability;
  onSetDefault: () => void;
}> = ({ model, capability: _capability, onSetDefault }) => {
  const [open, setOpen] = React.useState(false);
  const [loc, setLoc] = React.useState<ModelLocateResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const ensureLoaded = React.useCallback(async () => {
    if (loc || loading) return;
    setLoading(true);
    try {
      const r = await aiPlatform.models.locate(model.id, { runtime: model.runtime });
      setLoc(r);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [loc, loading, model.id, model.runtime]);

  const onToggle = React.useCallback(() => {
    setOpen((o) => !o);
    void ensureLoaded();
  }, [ensureLoaded]);

  // 模型在 to_public 里已经带 path / dir / exists，先用它做"立即可见"，
  // 等 locate 拿回更详细的（含 blobs）再覆盖。
  const initialPath = model.path ?? null;
  const path = loc?.path ?? initialPath;
  const dir = loc?.dir ?? model.dir ?? null;
  const exists = loc?.found ?? model.exists ?? false;

  const copyPath = async () => {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      notifyInfo('已复制', path, 1800);
    } catch {
      notifyInfo('请手动复制', path, 4000);
    }
  };

  return (
    <li className="rounded border border-zinc-200 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-xs">{model.id}</div>
          <div className="text-[10px] text-zinc-500">
            runtime={model.runtime ?? '-'} · format={model.format ?? '-'}
            {' · '}
            <button
              type="button"
              onClick={onToggle}
              className="text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-300 dark:hover:text-white"
              data-testid={`locate-toggle-${model.id}`}
            >
              {open ? '收起文件位置' : '查看文件位置'}
            </button>
          </div>
        </div>
        <button
          type="button"
          onClick={onSetDefault}
          className="ml-2 rounded border border-zinc-300 px-2 py-0.5 text-[10px] hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          设为默认
        </button>
      </div>

      {open && (
        <div className="mt-2 rounded bg-zinc-50 p-2 text-[11px] dark:bg-zinc-950/60" data-testid={`locate-panel-${model.id}`}>
          {loading && !loc && <div className="text-zinc-500">查询中…</div>}
          {error && <div className="text-amber-600">查询失败：{error}</div>}
          {(loc || initialPath) && (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-zinc-500">主文件</span>
                <span className={exists ? 'text-emerald-600' : 'text-amber-600'}>
                  {exists ? '✓ 存在' : '! 路径未命中或文件已删除'}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <code className="break-all rounded bg-white px-1.5 py-0.5 font-mono text-[11px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-100">
                  {path ?? '—'}
                </code>
                {path && (
                  <button
                    type="button"
                    onClick={() => void copyPath()}
                    className="shrink-0 rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                  >
                    复制
                  </button>
                )}
              </div>
              {dir && (
                <div className="mt-1 text-[10px] text-zinc-500">
                  目录：<code className="break-all">{dir}</code>
                </div>
              )}
              {loc?.cache_kind && (
                <div className="mt-1 text-[10px] text-zinc-500">
                  来源：{cacheKindLabel(loc.cache_kind)}
                  {loc.size_bytes ? ` · ${fmtBytes(loc.size_bytes)}` : ''}
                </div>
              )}
              {loc?.blobs && loc.blobs.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-zinc-500">
                    {loc.blobs.length} 个 blob（按 sha256 寻址）
                  </summary>
                  <ul className="mt-1 space-y-0.5">
                    {loc.blobs.map((b) => (
                      <li key={b.digest} className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
                        {b.exists ? '✓' : '×'} {b.digest.slice(0, 18)}…{' · '}
                        {fmtBytes(b.size_bytes)}
                        {b.media_type ? ` · ${b.media_type}` : ''}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {loc?.notes && <div className="mt-1 text-[10px] text-zinc-500">{loc.notes}</div>}
            </>
          )}
        </div>
      )}
    </li>
  );
};

function cacheKindLabel(k: ModelLocateResponse['cache_kind']): string {
  switch (k) {
    case 'ollama-blobs':
      return 'Ollama (manifest + blobs)';
    case 'hf-cache':
      return 'HuggingFace 缓存';
    case 'comfyui':
      return 'ComfyUI 模型目录';
    case 'local':
      return '察元自管理（model_dir_for）';
    default:
      return String(k ?? '未知');
  }
}

function fmtBytes(n: number): string {
  if (!n || n <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// 单条推荐卡片
// ---------------------------------------------------------------------------

const RecommendedCard: React.FC<{
  rec: RecommendedModelDto;
  capability: Capability;
  canInstall: boolean;
  installing?: ActiveInstall;
  onInstall: () => void;
}> = ({ rec, canInstall, installing, onInstall }) => {
  const installed = !!rec.installed;
  const inProgress = installing && installing.state !== 'done' && installing.state !== 'rescanned';

  return (
    <div
      className="flex flex-col gap-1.5 rounded border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900"
      data-testid={`recommended-card-${rec.id}`}
    >
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xs">{rec.id}</span>
        <span className="text-[10px] text-zinc-500">{rec.runtime}</span>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
        {rec.intent}
      </p>
      <div className="flex items-center gap-2 text-[10px] text-zinc-500">
        <span>{fmtSizeMB(rec.size_mb)}</span>
        {rec.offline_friendly && <span>· 离线友好</span>}
        {rec.optional_for_lite && <span>· lite 包不带</span>}
      </div>
      <div className="mt-1 flex items-center justify-between text-[10px]">
        <span className="text-zinc-400">{rec.hf_repo}</span>
        {installed ? (
          <span className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
            已安装
          </span>
        ) : (
          <button
            type="button"
            disabled={!canInstall || !!inProgress}
            onClick={onInstall}
            className="rounded border border-emerald-500 bg-emerald-50 px-2 py-0.5 text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200"
          >
            {inProgress ? '安装中…' : '一键安装'}
          </button>
        )}
      </div>
    </div>
  );
};

export default CapabilityCenter;
