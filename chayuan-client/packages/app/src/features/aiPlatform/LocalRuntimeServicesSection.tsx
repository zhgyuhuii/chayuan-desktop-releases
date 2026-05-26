/**
 * 设置页 `/settings` 中的「本地模型服务」分组。
 *
 * 5 个 capability(chat / embedding / rerank / asr / image-embedding)各一行:
 *   - 状态徽标 (LocalRuntimeStatusBadge)
 *   - 中文名(沿用 LocalRuntimeCapabilityCard 的 CAPABILITY_LABEL)
 *   - 当前 endpoint · pid 或 lastError
 *   - 模型下拉(从 GET /runtime/models 按 capability 分组)
 *   - 启动 / 停止 / 重启 按钮(按 state 切换)
 *
 * 顶部右侧:
 *   - 「刷新」按钮:手动触发 status + models 重拉
 *   - 「诊断」按钮:打开 DiagnoseModal
 *
 * 注:LocalRuntimePanel 完整版(配置表单 / 装机路径)保持原位,
 * 此分组只暴露"启停 + 选模型 + 诊断"三个高频动作。
 */
import * as React from 'react';
import {
  ChevronDown,
  ChevronRight,
  ClipboardList,
  CloudDownload,
  Copy,
  FileBarChart2,
  HelpCircle,
  Play,
  RefreshCw,
  RotateCw,
  Square,
} from 'lucide-react';
import {
  type AutoStartDict,
  type AutoStartKey,
  localRuntime,
  type LocalModelEntry,
  type LocalRuntimeCapability,
  modality,
  type OcrSidecarStatus,
  type PytorchInstallTask,
  type PytorchLocationsResult,
  type PytorchScanResult,
  type PytorchStatus,
  type PytorchVariant,
  type RuntimeServiceEngine,
  type RuntimeServiceInstallJob,
  runtimeModels,
} from '@chayuan/api';
import { Button, Switch } from '@chayuan/ui';
import { notifySuccess, reportError } from '../../store/errorDialog';
import { useLocalRuntimeStore } from '../../store/localRuntime';
import { CallLogDialog } from './CallLogDialog';
import { DiagnoseModal } from './DiagnoseModal';
import { InstallModelDialog } from './InstallModelDialog';
import { LocalRuntimeStatusBadge } from './LocalRuntimeStatusBadge';
import { PytorchHelpDialog } from './PytorchHelpDialog';

const CAPABILITIES: LocalRuntimeCapability[] = [
  'chat',
  'embedding',
  'rerank',
  'asr',
  // image-embedding 已隐藏 —— 图像向量化是系统里唯一依赖 PyTorch 的能力,
  // 现在文档库直接 OCR 处理图片,不再需要 PyTorch / image-embedding 服务。
];

/** 与 LocalRuntimeCapabilityCard.tsx 保持同一处真源(后续两边一起改)。 */
const CAP_LABEL: Record<LocalRuntimeCapability, string> = {
  chat: '聊天',
  embedding: '文本嵌入',
  rerank: '重排',
  asr: '语音识别',
  'image-embedding': '图像嵌入',
};

/** 后端 install_info.bundled_caps / missing_caps 用的短名 ↔ 前端 capability key 映射。
 *  后端用 'image' 表示图像嵌入,前端用 'image-embedding';其它一致。 */
const BACKEND_CAP_KEY: Record<LocalRuntimeCapability, string> = {
  chat: 'chat',
  embedding: 'embedding',
  rerank: 'rerank',
  asr: 'asr',
  'image-embedding': 'image',
};

/** scanner identifier(chayuan-server identifier.py:_CAPABILITY_BY_MODELTYPE)给的 capability 字符串
 *  ↔ 前端 LocalRuntimeCapability 映射。scanner 用的是 HF-style 长名,前端用短名。
 *  没匹配上返回 null,跳过这条 entry(不计入任何 cap 的模型清单)。 */
function mapScannerCapability(
  scannerCap: string,
  format: string,
): LocalRuntimeCapability | null {
  switch (scannerCap) {
    case 'chat':           return 'chat';
    case 'text-embedding':
    case 'embedding':      return 'embedding';
    case 'rerank':         return 'rerank';
    case 'speech-to-text':
    case 'asr':            return 'asr';
    case 'image-to-text':
    case 'image-embedding':
    case 'image':
      // OCR(PP-OCRv3/v4)scanner 同样标 image-to-text/onnx,但 OCR 走进程内
      // rapidocr,不出现在 image-embedding 下拉;按 format 过滤掉。
      if (format === 'onnx') return null;
      return 'image-embedding';
    default:
      return null;
  }
}

export const LocalRuntimeServicesSection: React.FC = () => {
  const { statuses, pendingFor, reachable, startCapability, stopCapability, restartCapability } =
    useLocalRuntimeStore();
  const installInfo = useLocalRuntimeStore((s) => s.installInfo);

  // 当前安装包随包嵌入的 cap 集合(后端 install_info.bundled_caps)。
  // 老服务端没返此字段 → null,所有 cap 都视为已随包(向后兼容)。
  const bundledCaps = React.useMemo<Set<string> | null>(() => {
    if (!installInfo?.bundled_caps) return null;
    return new Set(installInfo.bundled_caps);
  }, [installInfo?.bundled_caps]);

  // 本 section 涉及的 5 个 cap 里,有哪些未随包(用于顶部 banner 列表)。
  const sectionMissingCaps = React.useMemo<LocalRuntimeCapability[]>(() => {
    if (bundledCaps == null) return [];
    return CAPABILITIES.filter((cap) => !bundledCaps.has(BACKEND_CAP_KEY[cap]));
  }, [bundledCaps]);

  const isLite = installInfo?.is_lite_build === true;

  const [models, setModels] = React.useState<Record<LocalRuntimeCapability, LocalModelEntry[]>>({
    chat: [],
    embedding: [],
    rerank: [],
    asr: [],
    'image-embedding': [],
  });
  const [modelsLoading, setModelsLoading] = React.useState(false);
  const [modelsError, setModelsError] = React.useState<string | null>(null);
  const [chosen, setChosen] = React.useState<Partial<Record<LocalRuntimeCapability, string>>>({});
  const [diagnoseOpen, setDiagnoseOpen] = React.useState(false);
  const [callLogOpen, setCallLogOpen] = React.useState(false);
  // 折叠状态:持久化在 localStorage,刷新后保留;默认展开。
  // 已配云端模型可用的用户多半不需要看本地 sidecar 行,允许折叠减少干扰。
  const [collapsed, setCollapsed] = React.useState<boolean>(() => {
    try {
      return localStorage.getItem('cy:local-runtime-section-collapsed') === '1';
    } catch {
      return false;
    }
  });
  const toggleCollapsed = React.useCallback(() => {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem('cy:local-runtime-section-collapsed', next ? '1' : '0'); } catch { /* ignore */ }
      return next;
    });
  }, []);
  // 「下载模型」入口 — null 表示关闭;非 null 表示当前哪个 cap 在弹 Dialog
  const [installFor, setInstallFor] = React.useState<LocalRuntimeCapability | null>(null);

  // 5 个 capability + rapidocr 的 auto-start 开关字典 — 拉一次,Switch 切换写回。
  // 后端默认 5 cap 全 false / rapidocr true(见 auto_start_store._DEFAULTS),
  // 让老用户体验不变。
  const [autoStart, setAutoStart] = React.useState<AutoStartDict>({});
  const [autoStartPending, setAutoStartPending] = React.useState<AutoStartKey | null>(null);
  React.useEffect(() => {
    void (async () => {
      try {
        setAutoStart(await localRuntime.listAutoStart());
      } catch {
        // 老服务端没有这个端点,静默 — UI 显示默认值(全 off,OCR on)
      }
    })();
  }, []);
  const toggleAutoStart = React.useCallback(async (cap: AutoStartKey, enabled: boolean) => {
    setAutoStartPending(cap);
    try {
      setAutoStart(await localRuntime.setAutoStart(cap, enabled));
    } finally {
      setAutoStartPending(null);
    }
  }, []);

  const reloadModels = React.useCallback(async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      // runtime.ts 的 runtimeModels.list 返回 { total, items } envelope
      const { items } = await runtimeModels.list();
      const grouped: Record<LocalRuntimeCapability, LocalModelEntry[]> = {
        chat: [], embedding: [], rerank: [], asr: [], 'image-embedding': [],
      };
      for (const m of items) {
        // scanner identifier 给的 capability 跟前端 LocalRuntimeCapability enum 不一致:
        //   text-embedding   ↔ embedding
        //   speech-to-text   ↔ asr
        //   image-to-text    ↔ image-embedding(注意:OCR onnx 也是 image-to-text,要排除)
        // 原来直接 grouped[m.capability] 永远 undefined,模型被静默丢弃 → UI 显示"未安装"。
        const cap = mapScannerCapability(m.capability, m.format);
        if (cap && grouped[cap]) grouped[cap].push(m);
      }
      setModels(grouped);
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e));
    } finally {
      setModelsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void reloadModels();
  }, [reloadModels]);

  // 自己挂 capability registry 轮询(5s)。 Shell.tsx 全局轮询只跑 refreshStatus,
  // 不跑 refreshRegistry; LocalRuntimePanel 的轮询挂在 LocalRuntimePanel 内部,
  // 用户不打开它就拿不到 statuses,首次进 /settings 会看到 5 行 capability 全显示
  // stopped 即使实际 ready。这里独立挂一个,跟 LocalRuntimePanel 互不干扰。
  React.useEffect(() => {
    const { refreshStatus, refreshRegistry, refreshInstallInfo } = useLocalRuntimeStore.getState();
    void refreshStatus();
    void refreshRegistry();
    // installInfo 只有 LocalRuntimePanel mount 时才拉,这里独立兜底一次,
    // 不然用户没打开 LocalRuntimePanel 就拿不到 bundled_caps / is_lite_build
    // → 无法在轻量版下显示「未随包」横幅。
    void refreshInstallInfo();
    const t = window.setInterval(() => {
      void useLocalRuntimeStore.getState().refreshStatus();
      void useLocalRuntimeStore.getState().refreshRegistry();
    }, 5_000);
    return () => window.clearInterval(t);
  }, []);

  const resolveModelId = (cap: LocalRuntimeCapability): string | undefined =>
    chosen[cap] ?? statuses[cap]?.model_id ?? models[cap][0]?.model_id;

  const onRefresh = () => {
    void useLocalRuntimeStore.getState().refreshStatus();
    void useLocalRuntimeStore.getState().refreshRegistry();
    void reloadModels();
  };

  if (!reachable) {
    const lastError = useLocalRuntimeStore.getState().lastError;
    return (
      <section
        id="local-runtime-services"
        className="rounded-md border border-rose-500/30 bg-rose-50 p-4 text-sm text-rose-800 dark:bg-rose-950/30 dark:text-rose-200 space-y-2"
      >
        <div>
          无法连接 sidecar (
          <code className="rounded bg-rose-100 px-1 dark:bg-rose-900/40">/runtime/llama/*</code>)。
        </div>
        {lastError && (
          <div className="rounded bg-rose-100 px-2 py-1 font-mono text-xs dark:bg-rose-900/40">
            {lastError}
          </div>
        )}
        <div className="text-xs opacity-80">
          常见原因:① chayuan-server 没起来(检查端口 62581 / 看终端是否在跑);
          ② 后端启动中(过几秒再试);
          ③ Web / Thin 模式下不带本地服务(只在集成版 .msi 里可用)。
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            void useLocalRuntimeStore.getState().refreshStatus();
            void useLocalRuntimeStore.getState().refreshRegistry();
            void useLocalRuntimeStore.getState().refreshInstallInfo();
          }}
        >
          <RefreshCw className="mr-1 h-3.5 w-3.5" /> 重试
        </Button>
      </section>
    );
  }

  return (
    <section
      id="local-runtime-services"
      className="rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] p-4 space-y-3"
    >
      <header className="flex items-center justify-between">
        <button
          type="button"
          onClick={toggleCollapsed}
          className="-ml-1 flex items-center gap-1 rounded px-1 py-0.5 text-sm font-medium text-[var(--cy-text-primary)] hover:bg-[var(--cy-surface-1)]"
          aria-expanded={!collapsed}
          aria-controls="local-runtime-services-body"
        >
          {collapsed
            ? <ChevronRight className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />
            : <ChevronDown className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />}
          本地模型服务
        </button>
        <div className="flex items-center gap-2">
          {/* 折叠后这些按钮没意义,隐藏避免误操作 */}
          {!collapsed && (
            <>
              <Button size="sm" variant="outline" onClick={onRefresh} disabled={modelsLoading}>
                <RefreshCw className={'mr-1 h-3.5 w-3.5' + (modelsLoading ? ' animate-spin' : '')} />
                刷新
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCallLogOpen(true)}
                title="查看语音识别 / OCR 最近调用日志"
              >
                <FileBarChart2 className="mr-1 h-3.5 w-3.5" />
                调用日志
              </Button>
              <Button size="sm" variant="outline" onClick={() => setDiagnoseOpen(true)}>
                <ClipboardList className="mr-1 h-3.5 w-3.5" />
                诊断
              </Button>
            </>
          )}
        </div>
      </header>

      {!collapsed && (
      <div id="local-runtime-services-body" className="space-y-3">
      {modelsError && (
        <div className="rounded-sm border border-amber-400/40 bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          模型清单加载失败:{modelsError} ·
          <button
            type="button"
            className="ml-1 underline"
            onClick={() => void reloadModels()}
          >
            重试
          </button>
        </div>
      )}

      {isLite && sectionMissingCaps.length > 0 && (
        <div className="rounded-sm border border-amber-400/40 bg-amber-50 px-2.5 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="font-medium">当前是轻量版安装包</div>
          <div className="mt-0.5 text-amber-800 dark:text-amber-300">
            以下能力未随包嵌入,需在「模型广场」下载模型后才能启动:
            {sectionMissingCaps.map((cap, i) => (
              <span key={cap}>
                {i > 0 ? '、' : ' '}
                <span className="font-medium">{CAP_LABEL[cap]}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {CAPABILITIES.map((cap) => {
          const isMissing =
            bundledCaps != null && !bundledCaps.has(BACKEND_CAP_KEY[cap]);
          return (
            <CapabilityRow
              key={cap}
              capability={cap}
              label={CAP_LABEL[cap]}
              status={statuses[cap]}
              pending={pendingFor[cap]}
              models={models[cap]}
              chosen={chosen[cap]}
              resolvedModelId={resolveModelId(cap)}
              isMissing={isMissing}
              autoStart={autoStart[cap] ?? false}
              autoStartPending={autoStartPending === cap}
              onChoose={(id) => setChosen((s) => ({ ...s, [cap]: id }))}
              onStart={() => {
                const modelId = resolveModelId(cap);
                void startCapability(cap, modelId ? { model_id: modelId } : undefined);
              }}
              onStop={() => {
                void stopCapability(cap);
              }}
              onRestart={() => {
                const modelId = resolveModelId(cap);
                void restartCapability(cap, modelId ? { model_id: modelId } : undefined);
              }}
              onToggleAutoStart={(enabled) => void toggleAutoStart(cap, enabled)}
              onOpenDiagnose={() => setDiagnoseOpen(true)}
              onOpenInstall={() => setInstallFor(cap)}
            />
          );
        })}
        {/* OCR sidecar — 不在 useLocalRuntimeStore 管的 5 个 capability 内
            (那个管 llama/whisper/Infinity 三类 sidecar 的 SidecarRuntimeManager);
            RapidOCR 是独立的 python -m daemon,端口 18380,跟其它行视觉一致但走
            独立 API(modality.ocrSidecar.*)。后端 startup 已自动拉起一次。*/}
        <OcrSidecarRow />
        {/* PyTorch 行已隐藏 —— 系统中只有图像向量化(image-embedding)用
            PyTorch,现在图像向量功能已下线(改走 OCR + 文本嵌入),PyTorch
            的下载 / 安装 / 选用功能对用户不再有意义,隐藏避免误导。
            `false && <PytorchRow />` 保留对组件的静态引用,避免 tsc 把它
            判成未用代码;后续若彻底确认不再需要,再连同 PytorchRow /
            PytorchHelpDialog / Pytorch* 类型 / 后端运行时一起清除。*/}
        {false && <PytorchRow />}
        {/* 运行时服务(sidecar 二进制)— llama-server / whisper-server。
            完整安装包已内置;轻量版 / 缺失时可按平台一键下载补回。*/}
        <RuntimeServicesRow />
      </div>

      {installFor && (
        <InstallModelDialog
          open
          onOpenChange={(o) => { if (!o) setInstallFor(null); }}
          capability={installFor}
          onSuccess={() => {
            // 下载完成 / 扫描挂载完成 → 重拉模型下拉,让用户立刻看到新模型
            void reloadModels();
          }}
        />
      )}
      <DiagnoseModal open={diagnoseOpen} onOpenChange={setDiagnoseOpen} />
      <CallLogDialog open={callLogOpen} onOpenChange={setCallLogOpen} />
      </div>
      )}
    </section>
  );
};

interface CapabilityRowProps {
  capability: LocalRuntimeCapability;
  label: string;
  status: import('@chayuan/api').LocalRuntimeStatus | null;
  pending: 'start' | 'stop' | 'restart' | null;
  models: LocalModelEntry[];
  chosen?: string;
  resolvedModelId?: string;
  isMissing: boolean;
  autoStart: boolean;
  autoStartPending: boolean;
  onChoose(id: string): void;
  onStart(): void;
  onStop(): void;
  onRestart(): void;
  onToggleAutoStart(enabled: boolean): void;
  onOpenDiagnose(): void;
  /** 点行内「下载模型」按钮 → 父组件打开 InstallModelDialog,预选当前 cap。 */
  onOpenInstall(): void;
}

const CapabilityRow: React.FC<CapabilityRowProps> = ({
  capability,
  label,
  status,
  pending,
  models,
  resolvedModelId,
  isMissing,
  autoStart,
  autoStartPending,
  onChoose,
  onStart,
  onStop,
  onRestart,
  onToggleAutoStart,
  onOpenDiagnose,
  onOpenInstall,
}) => {
  const state = status?.state ?? 'stopped';
  const isPending = pending !== null;
  const isReady = state === 'ready';
  const isFailed = state === 'failed';
  const noModels = models.length === 0;

  const selectedId = resolvedModelId ?? '';

  return (
    <div
      id={`local-runtime-${capability}`}
      className="rounded-md border border-[var(--cy-border-subtle)] p-3 space-y-1.5 scroll-mt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <LocalRuntimeStatusBadge status={status} />
        <span className="text-sm font-medium text-[var(--cy-text-primary)]">{label}</span>
        {isMissing && (
          <span
            className="rounded-sm border border-amber-400/50 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
            title="该能力未随当前安装包嵌入,需先在「模型广场」下载模型"
          >
            未随包
          </span>
        )}
        {isReady && status?.endpoint && (
          <code className="text-xs text-[var(--cy-text-secondary)]">{status.endpoint}</code>
        )}
        {isReady && status?.pid != null && (
          <span className="text-xs text-[var(--cy-text-tertiary)]">pid {status.pid}</span>
        )}
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <label className="text-xs text-[var(--cy-text-tertiary)]">模型</label>
        <select
          value={selectedId}
          onChange={(e) => onChoose(e.target.value)}
          disabled={noModels || isPending}
          // ⚠ max-w + truncate:原生 <select> 在 Chromium / WebView2 上的下拉
          // 宽度由"最长 option 文本"撑;像 jinaai/jina-clip-v2(420.5 MB)
          // 这种字符串会让 select 整体撑爆,在窄面板里直接超出页面。这里
          // 限定 select 自身最大 14rem,option 文本走 _shortModelLabel 去掉
          // org/ 前缀只显模型短名 — 完整 id 还是作为 value
          className="max-w-[14rem] truncate rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-2 py-1 text-xs"
          title={selectedId || ''}
        >
          {noModels ? (
            <option value="">未安装,前往「模型广场」下载</option>
          ) : (
            models.map((m) => (
              <option key={m.model_id} value={m.model_id} title={m.model_id}>
                {_shortModelLabel(m.model_id)}
                {m.size_bytes ? ` · ${prettyMB(m.size_bytes)}` : ''}
              </option>
            ))
          )}
        </select>

        <div className="ml-auto flex items-center gap-1.5">
          {/* 「下载模型」入口 — 任何状态下都可点;noModels 时是主要 CTA。
             跟启动/停止按钮并列,放在最左侧让缺模型用户第一眼看到。 */}
          <Button
            size="sm"
            variant={noModels && !isReady ? 'default' : 'outline'}
            onClick={onOpenInstall}
            title="去 ModelScope / Hugging Face 找一个模型下到本地"
          >
            <CloudDownload className="mr-1 h-3.5 w-3.5" />
            下载模型
          </Button>
          {!isReady && (
            <Button
              size="sm"
              onClick={onStart}
              disabled={isPending || noModels}
            >
              <Play
                className={'mr-1 h-3.5 w-3.5' + (pending === 'start' ? ' animate-pulse' : '')}
              />
              {isFailed ? '重试' : '启动'}
            </Button>
          )}
          {isReady && (
            <>
              <Button size="sm" variant="outline" onClick={onRestart} disabled={isPending}>
                <RotateCw
                  className={'mr-1 h-3.5 w-3.5' + (pending === 'restart' ? ' animate-spin' : '')}
                />
                重启
              </Button>
              <Button size="sm" variant="outline" onClick={onStop} disabled={isPending}>
                <Square className="mr-1 h-3.5 w-3.5" />
                停止
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 pt-1">
        <label
          htmlFor={`auto-start-${capability}`}
          className="flex flex-col gap-0.5 cursor-pointer select-none"
          title="开启:chayuan-server 启动时自动拉起;关闭:需手动点上方「启动」"
        >
          <span className="text-xs font-medium text-[var(--cy-text-primary)]">
            随 chayuan-server 自动启动
          </span>
          <span className="text-[10px] text-[var(--cy-text-tertiary)]">
            切换不影响当前进程,只决定下次 chayuan-server 启动行为
          </span>
        </label>
        <Switch
          id={`auto-start-${capability}`}
          checked={autoStart}
          disabled={autoStartPending}
          onCheckedChange={(checked) => onToggleAutoStart(Boolean(checked))}
        />
      </div>

      {isFailed && status?.last_error && (
        <div className="rounded-sm border border-rose-500/30 bg-rose-50 p-2 text-xs text-rose-800 dark:bg-rose-950/30 dark:text-rose-200 whitespace-pre-wrap break-all">
          {status.last_error}
          <button
            type="button"
            className="ml-2 underline"
            onClick={onOpenDiagnose}
          >
            查看诊断
          </button>
        </div>
      )}
    </div>
  );
};

/**
 * 「OCR 文字识别 (RapidOCR)」行 — UI 风格跟前 5 个 CapabilityRow 一致,但走
 * 独立 API(modality.ocrSidecar.*)。不进 useLocalRuntimeStore 是因为 OCR
 * sidecar 是独立的 python -m daemon,SidecarRuntimeManager 不管它。
 *
 * 状态色复用 LocalRuntimeStatusBadge 的配色规则,但用 inline JSX 实现以避免
 * 跟 LocalRuntimeStatus 类型耦合。
 */
const OcrSidecarRow: React.FC = () => {
  const [status, setStatus] = React.useState<OcrSidecarStatus | null>(null);
  const [pending, setPending] = React.useState<'start' | 'stop' | 'autostart' | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    try {
      const s = await modality.ocrSidecar.status();
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  React.useEffect(() => {
    void refresh();
    // 5s 轮询 — 跟 LocalRuntimePanel 的 statuses 轮询节奏对齐
    const t = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(t);
  }, [refresh]);

  const onStart = async () => {
    setPending('start');
    try {
      const s = await modality.ocrSidecar.start();
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  const onStop = async () => {
    setPending('stop');
    try {
      const s = await modality.ocrSidecar.stop();
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  const onToggleAutoStart = async (enabled: boolean) => {
    setPending('autostart');
    try {
      const s = await modality.ocrSidecar.setAutoStart(enabled);
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };

  const state = status?.state ?? 'stopped';
  const isReady = state === 'ready';
  const isStarting = state === 'starting';
  const isFailed = state === 'failed';
  const isPending = pending !== null;
  const stateLabel = isReady ? '运行中' : isStarting ? '启动中' : isFailed ? '失败' : '已停止';
  const stateColor =
    isReady
      ? 'bg-emerald-500/15 text-emerald-700 border-emerald-500/30'
      : isStarting
        ? 'bg-sky-500/15 text-sky-700 border-sky-500/30 animate-pulse'
        : isFailed
          ? 'bg-rose-500/15 text-rose-700 border-rose-500/30'
          : 'bg-zinc-500/15 text-zinc-600 border-zinc-500/30';
  const dotColor =
    isReady ? 'bg-emerald-500'
      : isStarting ? 'bg-sky-500'
        : isFailed ? 'bg-rose-500' : 'bg-zinc-400';

  return (
    <div
      id="local-runtime-ocr"
      className="rounded-md border border-[var(--cy-border-subtle)] p-3 space-y-1.5 scroll-mt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          title={status?.error || stateLabel}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${stateColor}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} />
          {stateLabel}
        </span>
        <span className="text-sm font-medium text-[var(--cy-text-primary)]">
          OCR 文字识别
        </span>
        <span
          className="rounded-sm border border-zinc-300 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          title="独立 Python daemon,端口 18380;chayuan-server 启动时自动拉起"
        >
          RapidOCR · 18380
        </span>

        <div className="ml-auto flex items-center gap-1">
          {isReady ? (
            <Button size="sm" variant="outline" onClick={onStop} disabled={isPending}>
              <Square className="mr-1 h-3.5 w-3.5" />
              {pending === 'stop' ? '停止中…' : '停止'}
            </Button>
          ) : (
            <Button size="sm" onClick={onStart} disabled={isPending}>
              <Play className="mr-1 h-3.5 w-3.5" />
              {pending === 'start' ? '启动中…' : '启动'}
            </Button>
          )}
          {isReady && (
            <Button size="sm" variant="outline" onClick={onStart} disabled={isPending}>
              <RotateCw className="mr-1 h-3.5 w-3.5" />
              重启
            </Button>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 pt-1">
        <label
          htmlFor="ocr-auto-start"
          className="flex flex-col gap-0.5 cursor-pointer select-none"
          title="开启:chayuan-server 启动时一并拉起 RapidOCR daemon;关闭:需要手动点上方启动"
        >
          <span className="text-xs font-medium text-[var(--cy-text-primary)]">
            随 chayuan-server 自动启动
          </span>
          <span className="text-[10px] text-[var(--cy-text-tertiary)]">
            关闭后重启 chayuan-server 不会拉起 OCR,需手动点上方「启动」
          </span>
        </label>
        <Switch
          id="ocr-auto-start"
          checked={status?.auto_start ?? true}
          disabled={pending === 'autostart' || status == null}
          onCheckedChange={(checked) => void onToggleAutoStart(Boolean(checked))}
        />
      </div>

      <div className="text-[11px] text-[var(--cy-text-tertiary)] space-y-0.5">
        {status?.pid && <div>pid={status.pid} · 端口 {status.port}</div>}
        {status?.log_path && (
          <div className="font-mono break-all">日志:{status.log_path}</div>
        )}
        {error && (
          <div className="text-rose-600 dark:text-rose-400">{error}</div>
        )}
        {status?.error && !error && (
          <div className="text-rose-600 dark:text-rose-400">{status.error}</div>
        )}
      </div>
    </div>
  );
};

/**
 * 「PyTorch」行 — 显示 torch/torchvision 版本 + GPU 检测 + 一键安装 + 手动放置/扫描。
 *
 * 设计:transformers image 链的硬依赖,wheel 体积大不打包进 installer。
 *   - import_ok=true:显示已就绪(绿)+ 版本号
 *   - import_ok=false:显示需安装(灰/红)+ 提供安装按钮
 *   - 安装时:disable 按钮 + 显示最近几行 log + 提示装完需重启 chayuan-server
 *   - 优先内置 wheel(offline_wheels_available=true,无网也能装);否则在线
 *   - 「放置目录」:展示 install_target_dir,告诉用户手动装好的 torch 放哪;
 *     可复制路径 / 用资源管理器打开(类比模型下载对话框的「扫描挂载」)
 *   - 「扫描」:GET /runtime/pytorch/scan 识别 py_packages/ 里已放好的 torch,
 *     展示版本 + CPU/CUDA
 *   - GPU:has_nvidia_gpu=true 且 suggested_variant 为 cuXXX 时,除「安装 CPU 版」
 *     再给「安装 GPU(CUDA)版」入口;CUDA 版体积 2-3 GB,按钮上注明
 */
const PytorchRow: React.FC = () => {
  const [status, setStatus] = React.useState<PytorchStatus | null>(null);
  const [task, setTask] = React.useState<PytorchInstallTask | null>(null);
  const [installing, setInstalling] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // 「扫描挂载」结果 — null 表示还没扫过
  const [scanResult, setScanResult] = React.useState<PytorchScanResult | null>(null);
  const [scanning, setScanning] = React.useState(false);
  // 跨目录探测(CPU 版 / GPU 版各自是否存在)+ 当前选用配置
  const [locations, setLocations] = React.useState<PytorchLocationsResult | null>(null);
  // 「使用已装好的 PyTorch」:用户粘贴 / 选择的外部目录
  const [pickBusy, setPickBusy] = React.useState(false);
  const [pickMsg, setPickMsg] = React.useState<string | null>(null);
  // 「手动安装 PyTorch」帮助弹窗(标题后的「?」图标触发)
  const [helpOpen, setHelpOpen] = React.useState(false);

  const refreshStatus = React.useCallback(async () => {
    try {
      setStatus(await localRuntime.pytorchStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refreshLocations = React.useCallback(async () => {
    try {
      setLocations(await localRuntime.pytorchLocations());
    } catch {
      /* 探测失败不阻塞主流程;主状态仍由 refreshStatus 提供 */
    }
  }, []);

  const onScan = React.useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const r = await localRuntime.pytorchScan();
      setScanResult(r);
      if (r.torch_installed) {
        notifySuccess(
          '扫描完成',
          `已识别 torch ${r.torch_version ?? '?'}（${r.is_cuda ? 'GPU/CUDA' : 'CPU'} 版）`,
        );
      }
      // 顺手刷新跨目录探测,让「CPU 版 / GPU 版」徽标同步
      await refreshLocations();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      reportError(e, '扫描失败');
    } finally {
      setScanning(false);
    }
  }, [refreshLocations]);

  // 设置 torch 选用配置(auto / disabled / prefer)
  const onSetSelection = React.useCallback(
    async (opts: { mode: 'auto' | 'disabled'; prefer?: 'cpu' | 'cuda' }) => {
      setPickBusy(true);
      setPickMsg(null);
      try {
        await localRuntime.pytorchSetSelection(opts);
        notifySuccess(
          '已更新 PyTorch 选用',
          opts.mode === 'disabled'
            ? '已选择「不使用 PyTorch」,图像向量化等依赖功能将不可用'
            : `已切换为「自动」${opts.prefer ? `(优先 ${opts.prefer === 'cpu' ? 'CPU' : 'GPU'} 版)` : ''}`,
        );
        await Promise.all([refreshStatus(), refreshLocations()]);
      } catch (e) {
        setPickMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setPickBusy(false);
      }
    },
    [refreshStatus, refreshLocations],
  );


  React.useEffect(() => {
    void refreshStatus();
    void refreshLocations();
    const t = window.setInterval(() => void refreshStatus(), 5_000);
    return () => window.clearInterval(t);
  }, [refreshStatus, refreshLocations]);

  // 安装中:轮询 task,直到 terminal state
  React.useEffect(() => {
    if (!task || !installing) return;
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const t = await localRuntime.pytorchInstallTask(task.task_id);
          setTask(t);
          if (['done', 'failed', 'cancelled'].includes(t.state)) {
            setInstalling(false);
            await refreshStatus();
            // 装完顺手扫一次,让「放置目录」/「CPU 版 GPU 版」状态同步
            if (t.state === 'done') {
              try {
                setScanResult(await localRuntime.pytorchScan());
              } catch {
                /* 扫描失败不影响安装结果展示 */
              }
              await refreshLocations();
            }
            return;
          }
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          setInstalling(false);
          return;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    };
    void poll();
    return () => { cancelled = true; };
  }, [task, installing, refreshStatus, refreshLocations]);

  const startInstall = async (variant: PytorchVariant) => {
    setError(null);
    setInstalling(true);
    setTask(null);
    try {
      // force:true — 用户点「安装 / 重装」都是显式动作,后端解压前清空
      // py_packages/,去掉残留的旧 torch/torchvision,装出来版本严格配套。
      const t = await localRuntime.pytorchInstall(variant, { force: true });
      setTask(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setInstalling(false);
    }
  };

  const isReady = status?.import_ok === true;
  const stateLabel = isReady ? '就绪' : status ? '未就绪' : '检测中…';
  const stateColor =
    isReady
      ? 'bg-emerald-500/15 text-emerald-700 border-emerald-500/30'
      : status
        ? 'bg-rose-500/15 text-rose-700 border-rose-500/30'
        : 'bg-zinc-500/15 text-zinc-600 border-zinc-500/30';
  const dotColor = isReady ? 'bg-emerald-500' : status ? 'bg-rose-500' : 'bg-zinc-400';

  const variantLabel: Record<PytorchVariant, string> = {
    cpu: 'CPU',
    cu118: 'CUDA 11.8',
    cu121: 'CUDA 12.1',
    cu124: 'CUDA 12.4',
    cu126: 'CUDA 12.6',
    auto: '自动',
  };

  // 检测到 NVIDIA GPU 且后端按驱动反推出某个 cuXXX variant 时,才提供 GPU(CUDA)版入口。
  // 驱动太老 → 后端 suggested_variant 退回 cpu,这时不显示 CUDA 按钮(装了也用不了)。
  const cudaVariant: PytorchVariant | null =
    status?.has_nvidia_gpu && status?.suggested_variant && status.suggested_variant !== 'cpu'
      ? status.suggested_variant
      : null;

  const targetDir = status?.install_target_dir ?? scanResult?.target_dir ?? null;

  return (
    <div
      id="local-runtime-pytorch"
      className="rounded-md border border-[var(--cy-border-subtle)] p-3 space-y-1.5 scroll-mt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          title={status?.import_error || stateLabel}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${stateColor}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} />
          {stateLabel}
        </span>
        <span className="text-sm font-medium text-[var(--cy-text-primary)]">
          PyTorch
        </span>
        <button
          type="button"
          onClick={() => setHelpOpen(true)}
          title="手动安装 PyTorch 帮助(离线 / 内网)"
          aria-label="手动安装 PyTorch 帮助"
          className="inline-flex items-center justify-center rounded-sm p-0.5 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        >
          <HelpCircle className="h-3.5 w-3.5" />
        </button>
        {status?.has_nvidia_gpu && (
          <span
            className="rounded-sm border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200"
            title={`检测到 NVIDIA GPU,驱动 ${status.nvidia_driver ?? '未知'}`}
          >
            GPU
          </span>
        )}
        {status?.offline_wheels_available && (
          <span
            className="rounded-sm border border-sky-300 bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-900/30 dark:text-sky-200"
            title={`内置 wheel 可用 ${status.offline_wheels_dir ?? ''}`}
          >
            离线 wheel 就绪
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          {/* 「扫描」— 任何状态可点;识别用户手动放进 py_packages/ 的 torch */}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void onScan()}
            disabled={scanning || installing}
            title="扫描 py_packages/ 识别手动放好的 torch（版本 / CPU / GPU）"
          >
            <RotateCw className={'mr-1 h-3.5 w-3.5' + (scanning ? ' animate-spin' : '')} />
            {scanning ? '扫描中…' : '扫描'}
          </Button>
          {!isReady && status?.can_install !== false && (
            <>
              {/* CPU 版 — 始终提供。无 GPU 时它是唯一/主按钮;有 GPU 时是次选。 */}
              <Button
                size="sm"
                variant={cudaVariant ? 'outline' : 'default'}
                onClick={() => void startInstall('cpu')}
                disabled={installing}
                title="CPU 版 PyTorch，~250 MB，无需 GPU"
              >
                {installing ? '安装中…' : '安装 CPU 版'}
              </Button>
              {/* GPU(CUDA)版 — 仅检测到 NVIDIA GPU 且驱动够新时显示 */}
              {cudaVariant && (
                <Button
                  size="sm"
                  onClick={() => void startInstall(cudaVariant)}
                  disabled={installing}
                  title={`GPU 加速版 PyTorch（${variantLabel[cudaVariant]}）。wheel 含 CUDA runtime，体积 2-3 GB，下载较慢`}
                >
                  {installing
                    ? '安装中…'
                    : `安装 GPU 版（${variantLabel[cudaVariant]}）`}
                </Button>
              )}
            </>
          )}
          {isReady && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void startInstall(status?.suggested_variant ?? 'auto')}
              disabled={installing}
              title="重装(本机版本异常 / 升级 wheel 时用)"
            >
              重装
            </Button>
          )}
        </div>
      </div>

      {/* PyTorch 安装位置 —「安装」装到这里;手动放好的 torch 也放这里再「扫描」。 */}
      {targetDir && (
        <div className="space-y-1 rounded-sm border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2">
          <div className="text-[11px] text-[var(--cy-text-secondary)]">
            PyTorch 安装位置(点上方「安装」会装到这里):
          </div>
          <div className="flex items-center gap-1.5">
            <code className="flex-1 truncate rounded border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-2 py-1 text-[11px]">
              {targetDir}
            </code>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                if (!targetDir) return;
                void navigator.clipboard?.writeText(targetDir);
                notifySuccess('已复制路径', targetDir);
              }}
              title="复制安装位置路径"
            >
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div className="text-[11px] text-[var(--cy-text-tertiary)]">
            迁移已有的 PyTorch:把整套 torch / torchvision / transformers 等目录直接
            复制进上面这个位置(并排放,别多套一层文件夹),再点上方「扫描」识别。
            最省事是直接点「安装」,让程序自动下载并装好全套。
          </div>
        </div>
      )}

      {/* ── 手动安装帮助弹窗 ── 由标题后的「?」图标触发,全流程说明在这里 */}
      <PytorchHelpDialog
        open={helpOpen}
        onOpenChange={setHelpOpen}
        status={status}
        targetDir={targetDir}
      />

      {/* ── CPU 版 / GPU 版双检测 + 选用哪个 / 不使用 ── */}
      {locations && (locations.has_cpu || locations.has_cuda || locations.selection.mode !== 'auto') && (
        <div className="space-y-1.5 rounded-sm border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="text-[var(--cy-text-secondary)]">已检测到:</span>
            <span
              className={
                'rounded-sm border px-1.5 py-0.5 text-[10px] font-medium ' +
                (locations.has_cpu
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200'
                  : 'border-zinc-300 bg-zinc-50 text-zinc-500 dark:bg-zinc-800/40')
              }
              title={locations.cpu_dir ?? ''}
            >
              CPU 版 {locations.has_cpu ? '✓' : '—'}
            </span>
            <span
              className={
                'rounded-sm border px-1.5 py-0.5 text-[10px] font-medium ' +
                (locations.has_cuda
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200'
                  : 'border-zinc-300 bg-zinc-50 text-zinc-500 dark:bg-zinc-800/40')
              }
              title={locations.cuda_dir ?? ''}
            >
              GPU(CUDA)版 {locations.has_cuda ? '✓' : '—'}
            </span>
          </div>

          {/* 两个版本都装了 → 让用户选用哪个 */}
          {locations.has_cpu && locations.has_cuda && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="text-[var(--cy-text-secondary)]">
                CPU / GPU 两个版本都装了,选用:
              </span>
              <Button
                size="sm"
                variant={
                  locations.selection.mode === 'auto' &&
                  locations.selection.prefer !== 'cpu'
                    ? 'default'
                    : 'outline'
                }
                disabled={pickBusy}
                onClick={() => void onSetSelection({ mode: 'auto', prefer: 'cuda' })}
                title="优先用 GPU(CUDA)版"
              >
                GPU 版
              </Button>
              <Button
                size="sm"
                variant={
                  locations.selection.mode === 'auto' &&
                  locations.selection.prefer === 'cpu'
                    ? 'default'
                    : 'outline'
                }
                disabled={pickBusy}
                onClick={() => void onSetSelection({ mode: 'auto', prefer: 'cpu' })}
                title="优先用 CPU 版"
              >
                CPU 版
              </Button>
            </div>
          )}

          {/* 选用哪个目录 / 不使用 PyTorch */}
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="text-[var(--cy-text-secondary)]">PyTorch:</span>
            <Button
              size="sm"
              variant={locations.selection.mode !== 'disabled' ? 'default' : 'outline'}
              disabled={pickBusy || locations.selection.mode === 'auto'}
              onClick={() => void onSetSelection({ mode: 'auto' })}
              title="自动选用扫到的 torch"
            >
              {locations.selection.mode === 'path' ? '改回自动' : '自动'}
            </Button>
            <Button
              size="sm"
              variant={locations.selection.mode === 'disabled' ? 'default' : 'outline'}
              disabled={pickBusy}
              onClick={() => void onSetSelection({ mode: 'disabled' })}
              title="不使用 PyTorch — 图像向量化等依赖功能将不可用"
            >
              不使用 PyTorch
            </Button>
            {locations.selection.mode === 'disabled' && (
              <span className="text-amber-700 dark:text-amber-300">
                已禁用:图像向量化(image-embedding)等依赖 PyTorch 的功能不可用。
              </span>
            )}
            {locations.selection.mode === 'path' && locations.selection.path && (
              <span className="text-[var(--cy-text-tertiary)] break-all">
                当前用外部目录:{locations.selection.path}
              </span>
            )}
          </div>
        </div>
      )}

      {/* PyTorch 操作错误(切换选用 / 安装失败等)统一在此显示 */}
      {pickMsg && (
        <div className="rounded-sm border border-rose-300 bg-rose-50 px-2 py-1 text-[11px] text-rose-600 dark:border-rose-900/60 dark:bg-rose-900/20 dark:text-rose-400 break-all">
          {pickMsg}
        </div>
      )}

      <div className="text-[11px] text-[var(--cy-text-tertiary)] space-y-0.5">
        {status && (
          <div>
            torch: {status.torch_version ?? '未安装'}
            {status.torch_cuda_build && ` (${status.torch_cuda_build})`}
            {' · '}
            torchvision: {status.torchvision_version ?? '未安装'}
          </div>
        )}
        {!isReady && status?.import_error && (
          <div className="text-rose-600 dark:text-rose-400 break-all">
            {status.import_error}
          </div>
        )}
        {status?.notes?.map((n, i) => (
          <div key={i} className="text-amber-700 dark:text-amber-300">{n}</div>
        ))}
        {/* 扫描结果 — 用户点「扫描」后展示 py_packages/ 里识别到的 torch */}
        {scanResult && (
          <div className="mt-1 rounded-sm border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-1.5">
            {scanResult.torch_installed ? (
              <div className="text-emerald-700 dark:text-emerald-300">
                扫描:已安装 torch {scanResult.torch_version ?? '?'}
                {' · '}
                <span className="font-medium">
                  {scanResult.is_cuda
                    ? `GPU/CUDA 版${scanResult.torch_cuda_build ? `(${scanResult.torch_cuda_build})` : ''}`
                    : 'CPU 版'}
                </span>
                {scanResult.torchvision_installed && (
                  <> · torchvision {scanResult.torchvision_version ?? '?'}</>
                )}
              </div>
            ) : (
              <div className="text-amber-700 dark:text-amber-300">
                扫描:py_packages/ 下未发现 torch。把手动下好 / 解压好的 torch
                放进上方目录后再点「扫描」,或用上方「安装」按钮在线安装。
              </div>
            )}
          </div>
        )}
        {error && (
          <div className="text-rose-600 dark:text-rose-400">{error}</div>
        )}
        {task && (
          <details className="mt-1 rounded border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-1.5"
                   open={installing}>
            <summary className="cursor-pointer text-xs text-[var(--cy-text-secondary)]">
              安装日志 · {task.recipe_label} · {task.state}
              {task.state === 'done' && ' ✓ 装完请重启 chayuan-server 才能生效'}
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-[10px]
                            font-mono text-[var(--cy-text-tertiary)]">
              {task.log.slice(-30).join('\n') || '(等待日志…)'}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
};

/**
 * 「运行时服务」行 — llama-server / whisper-server 这两个 sidecar 二进制的
 * 就绪状态 + 一键下载补回。
 *
 * 设计:「运行时服务双保险」的应用内补回侧。
 *   - 完整安装包已把 vendor/services 打进去 → 这两个引擎显示「已内置」(绿)
 *   - 轻量版 / 完整包构建漏装 → 显示「缺失」(琥珀)+「下载」按钮
 *   - whisper-server 在非 Windows 平台 upstream 没预编译 → downloadable=false,
 *     显示原因,不给下载按钮(对齐 install-whisper-server.sh 的能力边界)
 *   - 下载中:轮询 job,展示步骤 + 进度条 + 日志,跟 PyTorch 行体验一致
 *   - 平台名由后端探测返回,前端不硬编码
 */
const ENGINE_LABEL: Record<string, string> = {
  'llama-server': '模型服务',
  'whisper-server': '语音识别服务',
};

const RuntimeServicesRow: React.FC = () => {
  const [status, setStatus] = React.useState<{
    platform: string | null;
    engines: RuntimeServiceEngine[];
  } | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // engine 名 → 当前下载 job;null 表示该 engine 没有进行中的下载
  const [jobs, setJobs] = React.useState<Record<string, RuntimeServiceInstallJob | null>>({});
  // engine 名 → 是否正在轮询(用来 disable 按钮)
  const [busy, setBusy] = React.useState<Record<string, boolean>>({});

  const refresh = React.useCallback(async () => {
    try {
      setStatus(await localRuntime.servicesStatus());
      setError(null);
    } catch (e) {
      // 老服务端没有这个端点 → 静默(整行不渲染)
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  // 下载中:轮询对应 job 直到终态
  const pollJob = React.useCallback(
    async (engine: string, jobId: string) => {
      setBusy((b) => ({ ...b, [engine]: true }));
      try {
        for (;;) {
          const job = await localRuntime.installServiceTask(jobId);
          setJobs((j) => ({ ...j, [engine]: job }));
          if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
            // 下完重拉一次 status,让「缺失」翻成「已就绪」
            await refresh();
            return;
          }
          await new Promise((r) => setTimeout(r, 1500));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy((b) => ({ ...b, [engine]: false }));
      }
    },
    [refresh],
  );

  const onDownload = React.useCallback(
    async (engine: string) => {
      setError(null);
      setBusy((b) => ({ ...b, [engine]: true }));
      try {
        const job = await localRuntime.installService(engine);
        setJobs((j) => ({ ...j, [engine]: job }));
        void pollJob(engine, job.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy((b) => ({ ...b, [engine]: false }));
      }
    },
    [pollJob],
  );

  const onCancel = React.useCallback(async (engine: string, jobId: string) => {
    try {
      const job = await localRuntime.cancelInstallService(jobId);
      setJobs((j) => ({ ...j, [engine]: job }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // 老服务端没有此端点 — 整行不渲染,避免误导
  if (!status && error) return null;
  if (!status) return null;

  return (
    <div
      id="local-runtime-services-binaries"
      className="rounded-md border border-[var(--cy-border-subtle)] p-3 space-y-2 scroll-mt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[var(--cy-text-primary)]">
          运行时服务
        </span>
        <span
          className="rounded-sm border border-zinc-300 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          title="本地模型推理依赖的 sidecar 二进制;完整安装包已内置,缺失时可按平台下载补回"
        >
          {status.platform ?? '未知平台'}
        </span>
      </div>

      {status.engines.map((eng) => {
        const job = jobs[eng.engine] ?? null;
        const isDownloading =
          (job != null && (job.status === 'queued' || job.status === 'running')) ||
          busy[eng.engine] === true;
        const stateLabel = eng.present
          ? '已就绪'
          : eng.downloadable
            ? '可下载'
            : '不可用';
        const stateColor = eng.present
          ? 'bg-emerald-500/15 text-emerald-700 border-emerald-500/30'
          : eng.downloadable
            ? 'bg-amber-500/15 text-amber-700 border-amber-500/30'
            : 'bg-zinc-500/15 text-zinc-600 border-zinc-500/30';
        const dotColor = eng.present
          ? 'bg-emerald-500'
          : eng.downloadable
            ? 'bg-amber-500'
            : 'bg-zinc-400';
        return (
          <div
            key={eng.engine}
            className="rounded-sm border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2.5 space-y-1.5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${stateColor}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} />
                {stateLabel}
              </span>
              <span className="text-xs font-medium text-[var(--cy-text-primary)]">
                {ENGINE_LABEL[eng.engine] ?? eng.engine}
              </span>
              {eng.present && eng.version && (
                <code className="text-[11px] text-[var(--cy-text-tertiary)]">
                  {eng.version}
                </code>
              )}

              <div className="ml-auto flex items-center gap-1.5">
                {!eng.present && eng.downloadable && !isDownloading && (
                  <Button size="sm" onClick={() => void onDownload(eng.engine)}>
                    <CloudDownload className="mr-1 h-3.5 w-3.5" />
                    下载
                  </Button>
                )}
                {isDownloading && job && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void onCancel(eng.engine, job.id)}
                  >
                    取消
                  </Button>
                )}
              </div>
            </div>

            {/* 缺失原因 — 仅在未就绪时显示 */}
            {!eng.present && eng.reason && !isDownloading && (
              <div className="text-[11px] text-[var(--cy-text-tertiary)]">
                {eng.reason}
              </div>
            )}

            {/* 下载进度 */}
            {job && (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[11px] text-[var(--cy-text-tertiary)]">
                  <span>
                    {job.progress_message || job.step || job.status}
                    {job.mirror ? ` · 镜像 ${job.mirror}` : ''}
                  </span>
                  <span>{Math.round(job.progress_pct)}%</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
                  <div
                    className={
                      'h-full rounded-full transition-all ' +
                      (job.status === 'failed'
                        ? 'bg-rose-500'
                        : job.status === 'succeeded'
                          ? 'bg-emerald-500'
                          : 'bg-sky-500')
                    }
                    style={{ width: `${Math.min(100, Math.max(0, job.progress_pct))}%` }}
                  />
                </div>
                {job.status === 'failed' && (
                  <details className="mt-1 rounded border border-rose-500/30 bg-rose-50 p-1.5 dark:bg-rose-950/30">
                    <summary className="cursor-pointer text-[11px] text-rose-700 dark:text-rose-300">
                      下载失败 — 查看日志
                    </summary>
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px] font-mono text-rose-700 dark:text-rose-300">
                      {job.log_tail.slice(-30).join('\n')}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        );
      })}

      {error && (
        <div className="text-[11px] text-rose-600 dark:text-rose-400">{error}</div>
      )}
    </div>
  );
};

function prettyMB(bytes: number): string {
  const mb = bytes / 1024 / 1024;
  if (mb < 1024) return `${mb.toFixed(0)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

/** 模型短名:去掉 ``org/`` 前缀只保留 repo 名,过长再截。
 *  HF 模型 id 经常 30+ 字符(jinaai/jina-embeddings-v3 / google/siglip2-base-
 *  patch16-224),原生 <select> 在 Chromium / WebView2 上会按最长 option 撑出宽度,
 *  窄面板里超出视口。完整 id 还是 option value / title,只显示截短。 */
function _shortModelLabel(id: string): string {
  if (!id) return '';
  const tail = id.includes('/') ? (id.split('/').pop() ?? id) : id;
  // GGUF 量化后缀往往最长,优先去掉
  const stripped = tail.replace(/-(GGUF|gguf|Q\d_[A-Z0-9_]+)$/, '');
  return stripped.length > 32 ? stripped.slice(0, 30) + '…' : stripped;
}
