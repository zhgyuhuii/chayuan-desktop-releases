/**
 * 「系统设置 → AI 平台」面板。
 *
 * 三栏：
 *   1. 模型库（按 capability 分类，调用 /v1/models 列出 + SSE 实时增量）
 *   2. 服务状态（受管 / 外部 / 未启用三态；端口 + 凭据来自 /runtime/services）
 *   3. 自检 + 一键修复（防杀软 / 端口冲突 / 防火墙 / Docker 缺失）
 *
 * 设计要点（架构师视角）：
 * - 完全不引 Tauri / Electron 特有 API，桌面与 Web 同源；
 * - 列表态走 react query 风格的 useEffect + cancel；SSE 在 unmount 自动断开；
 * - 任何调用失败都本地降级展示「不可用」，不阻塞父对话框；
 * - 渲染 100+ 模型时仍丝滑（virtual list 由 ui 包提供，本组件做分类切片）。
 */
import * as React from 'react';
import {
  aiPlatform,
  runtimeServices,
  runtimeVendor,
  type AiPlatformModel,
  type Capability,
  type DoctorReport,
  type MirrorOption,
  type MirrorState,
  type RuntimeServiceEndpoint,
  type RuntimeServicesPayload,
  type ServiceTopology,
  type ServiceTopologyItem,
  type VendorPayload,
} from '@chayuan/api';
import { CapabilityCenter } from './CapabilityCenter';
import { LocalRuntimePanel } from './LocalRuntimePanel';
import { RuntimeCenter } from './RuntimeCenter';
import { DoctorGuidanceList, type DoctorCheckLike } from './DoctorGuidance';
import { MarketplacePage } from '../marketplace/MarketplacePage';
import { useAuthStore } from '../../store/auth';
import { useLoginModalStore } from '../../store/loginModal';

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

const ALL_CAPABILITIES = Object.keys(CAPABILITY_LABELS) as Capability[];

// ----- 数据 hooks ----------------------------------------------------------

function useModels(): {
  models: AiPlatformModel[];
  loading: boolean;
  error?: unknown;
  reload: () => Promise<void>;
} {
  const [state, setState] = React.useState<{
    models: AiPlatformModel[];
    loading: boolean;
    error?: unknown;
  }>({ models: [], loading: true });

  const reload = React.useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const list = await aiPlatform.models.list();
      setState({ models: list, loading: false });
    } catch (e) {
      setState({ models: [], loading: false, error: e });
    }
  }, []);

  React.useEffect(() => {
    void reload();
    const off = aiPlatform.models.subscribeEvents({
      onAdded: (m) =>
        setState((s) => ({ ...s, models: upsert(s.models, m) })),
      onUpdated: (m) =>
        setState((s) => ({ ...s, models: upsert(s.models, m) })),
      onRemoved: (id) =>
        setState((s) => ({ ...s, models: s.models.filter((x) => x.id !== id) })),
      onError: () => {
        // SSE 失败：静默降级到 30s 轮询
      },
    });
    const t = window.setInterval(() => void reload(), 30_000);
    return () => {
      off();
      window.clearInterval(t);
    };
  }, [reload]);

  return { ...state, reload };
}

function upsert(list: AiPlatformModel[], m: AiPlatformModel): AiPlatformModel[] {
  const i = list.findIndex((x) => x.id === m.id);
  if (i < 0) return [...list, m];
  const copy = list.slice();
  copy[i] = m;
  return copy;
}

function useServices() {
  const [data, setData] = React.useState<RuntimeServicesPayload | undefined>();
  const [vendor, setVendor] = React.useState<VendorPayload | undefined>();
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<unknown>();

  const reload = React.useCallback(async () => {
    setLoading(true);
    try {
      const [svc, ven] = await Promise.all([
        runtimeServices.list({ reveal: false }),
        runtimeVendor.list().catch(() => undefined),
      ]);
      setData(svc);
      setVendor(ven);
      setError(undefined);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void reload();
    const t = window.setInterval(() => void reload(), 15_000);
    return () => window.clearInterval(t);
  }, [reload]);

  return { data, vendor, loading, error, reload };
}

// ----- 子组件 --------------------------------------------------------------

const ModelsByCapability: React.FC<{ models: AiPlatformModel[] }> = ({ models }) => {
  const grouped = React.useMemo(() => {
    const out = Object.fromEntries(
      ALL_CAPABILITIES.map((c) => [c, [] as AiPlatformModel[]]),
    ) as unknown as Record<Capability, AiPlatformModel[]>;
    for (const m of models) {
      const cap = (m.capability ?? 'chat') as Capability;
      (out[cap] ?? out.chat).push(m);
    }
    return out;
  }, [models]);

  return (
    <div className="space-y-3">
      {ALL_CAPABILITIES.map((cap) => {
        const items = grouped[cap];
        return (
          <details key={cap} className="rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50/50 dark:bg-zinc-900/40">
            <summary className="px-3 py-2 cursor-pointer flex items-center justify-between text-sm font-medium">
              <span>{CAPABILITY_LABELS[cap]}</span>
              <span className="text-xs text-zinc-500">{items.length}</span>
            </summary>
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800 text-sm">
              {items.length === 0 && (
                <li className="px-3 py-2 text-xs text-zinc-500">暂无模型；可拖入 models/{cap}/ 或 `chayuan ai-platform model pull` 下载</li>
              )}
              {items.map((m) => (
                <li key={m.id} className="px-3 py-2 flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono text-xs">{m.id}</div>
                    <div className="text-[10px] text-zinc-500">
                      runtime={m.runtime ?? '-'} · format={m.format ?? '-'} · {prettyBytes(m.size_bytes)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
};

const ServiceList: React.FC<{
  services: RuntimeServiceEndpoint[];
  vendor?: VendorPayload;
}> = ({ services, vendor }) => {
  const knownVendor = new Set([
    ...(vendor?.services?.map((v) => v.name) ?? []),
    ...(vendor?.runtimes?.map((v) => v.name) ?? []),
  ]);
  return (
    <table className="w-full text-sm border-collapse">
      <thead className="text-xs text-zinc-500">
        <tr>
          <th className="text-left p-1.5">服务</th>
          <th className="text-left p-1.5">地址</th>
          <th className="text-left p-1.5">用户</th>
          <th className="text-left p-1.5">密码</th>
          <th className="text-left p-1.5">来源</th>
        </tr>
      </thead>
      <tbody>
        {services.length === 0 && (
          <tr><td colSpan={5} className="text-center text-xs text-zinc-500 py-4">尚未运行任何受管服务（vendor/ 为空？）</td></tr>
        )}
        {services.map((s) => (
          <tr key={s.name} className="border-t border-zinc-100 dark:border-zinc-800">
            <td className="p-1.5 font-mono text-xs">{s.name}</td>
            <td className="p-1.5 font-mono text-xs">{s.host}:{s.port}</td>
            <td className="p-1.5 font-mono text-xs">{s.user || '-'}</td>
            <td className="p-1.5 font-mono text-xs">{s.password || '-'}</td>
            <td className="p-1.5 text-xs">
              {knownVendor.has(s.name ?? '') ? '受管' : '外部'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

const DoctorPanel: React.FC = () => {
  const [report, setReport] = React.useState<DoctorReport | undefined>();
  const [running, setRunning] = React.useState(false);

  const run = async () => {
    setRunning(true);
    try {
      setReport(await aiPlatform.doctor.run());
    } catch (e) {
      setReport({ ok: false, checks: [{ name: 'doctor', status: 'error', message: String(e) }] });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button
          className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          disabled={running}
          onClick={() => void run()}
        >
          {running ? '运行中…' : '运行自检'}
        </button>
        {report && (
          <span className={report.ok ? 'text-xs text-green-600' : 'text-xs text-amber-600'}>
            {report.ok ? '✓ 全部通过' : `${report.checks.filter((c) => c.status !== 'ok').length} 项需要关注`}
          </span>
        )}
      </div>
      {report && (
        <div className="space-y-3">
          {/* 关键问题 (warn / fatal) 用 GuidanceCard 三段式呈现, 比一行红字可执行 */}
          <DoctorGuidanceList
            checks={report.checks as DoctorCheckLike[]}
            includeOk={false}
            className="space-y-2"
            emptyHint={
              <div className="text-xs text-emerald-600">✓ 所有关键检查项通过</div>
            }
          />
          {/* 折叠区: 全量检查项 (含 ok) — 给有故障排查需求的用户 */}
          <details className="text-xs">
            <summary className="cursor-pointer text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
              查看全部 {report.checks.length} 项
            </summary>
            <ul className="text-xs space-y-1 max-h-60 overflow-auto mt-2">
              {report.checks.map((c, i) => (
                <li key={i} className={statusClass(c.status)}>
                  <span className="inline-block w-12">{statusLabel(c.status)}</span>
                  <span className="font-medium">{c.name}</span>
                  <span className="ml-2">{c.message}</span>
                  {c.hint && <span className="ml-2 text-zinc-500">{c.hint}</span>}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
};

// ----- 镜像源切换 ---------------------------------------------------------

const MirrorSection: React.FC = () => {
  const [state, setState] = React.useState<MirrorState | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [customUrl, setCustomUrl] = React.useState('');

  const reload = React.useCallback(async () => {
    try {
      setError(null);
      const s = await aiPlatform.mirror.get();
      setState(s);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  const apply = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      await reload();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="text-xs text-amber-600">未连上 /v1/admin/mirror：{error}</div>
    );
  }
  if (!state) return <div className="text-xs text-zinc-500">加载中…</div>;

  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">
        当前：<span className="font-mono">{state.current.endpoint}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {state.available.map((opt: MirrorOption) => {
          const active = opt.endpoint === state.current.endpoint;
          return (
            <button
              key={opt.name}
              type="button"
              disabled={busy}
              onClick={() =>
                void apply(async () => {
                  await aiPlatform.mirror.setByName(opt.name);
                })
              }
              className={[
                'text-xs px-2 py-1 rounded border',
                active
                  ? 'bg-zinc-900 text-white border-zinc-900 dark:bg-white dark:text-zinc-900 dark:border-white'
                  : 'border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800',
              ].join(' ')}
            >
              {opt.name}
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <input
          value={customUrl}
          onChange={(e) => setCustomUrl(e.target.value)}
          placeholder="自定义镜像 URL（http(s)://…）"
          className="flex-1 text-xs px-2 py-1 rounded border border-zinc-300 bg-transparent dark:border-zinc-700"
        />
        <button
          type="button"
          disabled={busy || !/^https?:\/\//i.test(customUrl)}
          onClick={() =>
            void apply(async () => {
              await aiPlatform.mirror.setByEndpoint(customUrl);
            })
          }
          className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
        >
          应用
        </button>
      </div>
      <p className="text-[11px] text-zinc-500 leading-relaxed">
        切换镜像后，所有"模型下载 / 增量探测"立即走新地址；正在进行中的下载不会中断。
      </p>
    </div>
  );
};

// ----- 安全策略引导（防 AV 阻断 / Gatekeeper / SELinux） ------------------

interface SystemHint {
  os: 'win' | 'mac' | 'linux';
  title: string;
  body: string;
  cmd?: string;
}

const _UA = (typeof navigator !== 'undefined' ? navigator.userAgent : '').toLowerCase();
const _DETECT_OS: SystemHint['os'] =
  _UA.includes('win') ? 'win' : _UA.includes('mac') ? 'mac' : 'linux';

const SAFETY_HINTS: SystemHint[] = [
  {
    os: 'win',
    title: 'Windows Defender 把 ollama / vllm 误杀？',
    body:
      '设置 → 隐私和安全 → 病毒和威胁防护 → "管理设置" → "排除项" → 添加文件夹，把 chayuan 安装目录加进去。然后重启察元。',
    cmd: 'Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\\chayuan"',
  },
  {
    os: 'mac',
    title: 'macOS Gatekeeper 拦截 .app 启动？',
    body:
      '系统设置 → 隐私与安全性 → 在底部找到"已阻止"提示 → "仍要打开"。或开发期临时全局放开（不建议长期）。',
    cmd: 'sudo spctl --master-disable',
  },
  {
    os: 'linux',
    title: 'AppImage 无执行权限 / SELinux 拦截网络？',
    body:
      'chmod +x ./chayuan.AppImage 后再运行；如果是 RHEL 系发行版（带 SELinux），允许 webview 访问网络。',
    cmd: 'sudo setsebool -P httpd_can_network_connect 1',
  },
];

const SafetyHintsSection: React.FC = () => {
  const local = (SAFETY_HINTS.find((h) => h.os === _DETECT_OS) ?? SAFETY_HINTS[0])!;
  return (
    <details className="rounded-md border border-amber-300/50 bg-amber-50/40 p-3 dark:border-amber-900/50 dark:bg-amber-950/20">
      <summary className="cursor-pointer text-xs font-medium text-amber-900 dark:text-amber-200">
        ⚠ 子进程被杀软 / 安全策略拦截？
      </summary>
      <div className="mt-2 space-y-2 text-xs">
        <div className="font-medium">{local.title}</div>
        <p className="leading-relaxed text-zinc-700 dark:text-zinc-300">{local.body}</p>
        {local.cmd && (
          <pre className="overflow-x-auto rounded bg-zinc-900 px-2 py-1.5 font-mono text-[11px] text-zinc-100">
            {local.cmd}
          </pre>
        )}
        <details>
          <summary className="cursor-pointer text-zinc-500">其他平台 / 高级</summary>
          <ul className="mt-2 space-y-2">
            {SAFETY_HINTS.filter((h) => h.os !== local.os).map((h) => (
              <li key={h.os} className="leading-relaxed">
                <span className="font-medium">[{h.os}] {h.title}</span>
                <p className="text-zinc-600 dark:text-zinc-400">{h.body}</p>
                {h.cmd && (
                  <pre className="mt-1 overflow-x-auto rounded bg-zinc-900 px-2 py-1 font-mono text-[11px] text-zinc-100">
                    {h.cmd}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        </details>
      </div>
    </details>
  );
};

// ----- 服务拓扑（新端点 /v1/admin/services/topology）-----------------------

const TOPOLOGY_OS_LABELS: Record<NonNullable<keyof ServiceTopologyItem['install']['all']>, string> = {
  'linux-apt': 'Linux · apt',
  'linux-pacman': 'Linux · pacman',
  'mac-brew': 'macOS · brew',
  'win-winget': 'Windows · winget',
  docker: 'Docker',
} as unknown as Record<string, string>;

const ServiceTopologySection: React.FC = () => {
  const [data, setData] = React.useState<ServiceTopology | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const t = await aiPlatform.topology.get();
        if (!cancelled) setData(t);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const t = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  if (error) {
    return (
      <div className="text-xs text-amber-600">未连上 /v1/admin/services/topology：{error}</div>
    );
  }
  if (!data) return <div className="text-xs text-zinc-500">加载中…</div>;

  return (
    <div className="space-y-2">
      <p className="text-xs text-zinc-500">
        本机系统：<code>{data.host_os}</code> · 共 {data.services.length} 个服务
      </p>
      <table className="w-full text-sm border-collapse">
        <thead className="text-xs text-zinc-500">
          <tr>
            <th className="p-1.5 text-left">服务</th>
            <th className="p-1.5 text-left">状态</th>
            <th className="p-1.5 text-left">地址</th>
            <th className="p-1.5 text-left">操作</th>
          </tr>
        </thead>
        <tbody>
          {data.services.map((s) => (
            <ServiceTopologyRow key={s.name} item={s} />
          ))}
        </tbody>
      </table>
    </div>
  );
};

const ServiceTopologyRow: React.FC<{ item: ServiceTopologyItem }> = ({ item }) => {
  const [open, setOpen] = React.useState(false);
  const statusBadge =
    item.status === 'healthy' ? (
      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
        ✓ 健康
      </span>
    ) : item.status === 'configured' ? (
      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-200">
        ! 已配置 / 未启动
      </span>
    ) : (
      <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-700 dark:text-zinc-200">
        × 未安装
      </span>
    );

  const recipes = item.install.all;

  return (
    <>
      <tr className="border-t border-zinc-100 dark:border-zinc-800">
        <td className="p-1.5 font-mono text-xs">{item.name}</td>
        <td className="p-1.5">{statusBadge}</td>
        <td className="p-1.5 font-mono text-[11px]">
          {item.url ?? (item.host && item.port ? `${item.host}:${item.port}` : '-')}
        </td>
        <td className="p-1.5">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="rounded border border-zinc-300 px-2 py-0.5 text-[10px] hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            data-testid={`toggle-install-${item.name}`}
          >
            {open ? '收起' : '安装指引'}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} className="bg-zinc-50/50 p-2 dark:bg-zinc-900/40">
            <p className="mb-1 text-[11px] text-zinc-600 dark:text-zinc-300">
              {item.binary_present
                ? '本机检测到二进制，已自动注入 ~/.local/share/chayuan'
                : '本机未检测到二进制；推荐先用平台原生方式，或直接 docker fallback：'}
            </p>
            {Object.entries(recipes).map(([os, cmds]) => (
              <details key={os} className="mb-1 text-[11px]">
                <summary className="cursor-pointer">{TOPOLOGY_OS_LABELS[os as keyof typeof TOPOLOGY_OS_LABELS] ?? os}</summary>
                <pre className="mt-1 overflow-x-auto rounded bg-zinc-900 px-2 py-1 font-mono text-[11px] text-zinc-100">
                  {(cmds as string[]).join('\n')}
                </pre>
              </details>
            ))}
          </td>
        </tr>
      )}
    </>
  );
};

// ----- 主面板 --------------------------------------------------------------

type AiPlatformTab = 'models' | 'services' | 'doctor' | 'mirror' | 'local';

const TAB_ITEMS: ReadonlyArray<{ value: AiPlatformTab; label: string }> = [
  { value: 'models', label: '9 类模型库' },
  { value: 'services', label: '服务拓扑' },
  { value: 'doctor', label: '系统自检' },
  { value: 'mirror', label: '镜像源' },
  { value: 'local', label: '本地模型' },
];

export const AiPlatformPanel: React.FC = () => {
  const s = useServices();
  const [tab, setTab] = React.useState<AiPlatformTab>('models');
  const isLoggedIn = useAuthStore((a) => a.isLoggedIn);
  const authRequired = useLoginModalStore((a) => a.authRequired);

  return (
    <div className="space-y-3">
      {/* 顶部 tabs：marketplace 设计语言一致 */}
      <div className="flex items-center gap-1 rounded-md border border-zinc-200 p-0.5 dark:border-zinc-700">
        {TAB_ITEMS.map((t) => {
          const active = tab === t.value;
          return (
            <button
              key={t.value}
              type="button"
              onClick={() => setTab(t.value)}
              data-testid={`ai-platform-tab-${t.value}`}
              className={[
                'flex-1 rounded px-2 py-1 text-xs',
                active
                  ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                  : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800',
              ].join(' ')}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'models' && (
        <div className="space-y-4">
          {/* 顶部：模型框架卡片 + 9 类默认模型 */}
          <RuntimeCenter
            onJumpToCapability={(panelTab) => {
              // panelTab 是 ``image-embedding`` / ``chat`` 等；先把 dataset 写到 window，
              // CapabilityCenter mount 时读它（避免引第三个 store 仅为传一个 string）。
              try {
                window.dispatchEvent(
                  new CustomEvent('chayuan:capability-jump', {
                    detail: { capability: panelTab },
                  }),
                );
              } catch {
                /* noop */
              }
            }}
          />

          <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
            <h3 className="mb-2 text-sm font-semibold">按能力浏览模型</h3>
            <CapabilityCenter
              authRequired={authRequired === true}
              isLoggedIn={isLoggedIn}
            />
          </div>

          {/*
            第三行：8 个模型供应商（云）+ 类似 chayuan-client 模型广场。
            本地推理服务（Ollama / vLLM / LM Studio / Xinference / GPUStack）
            已经在最上面的 RuntimeCenter 里管理过了，这里把 ``excludeLocal``
            打开避免与之重复；``embedded`` 关掉外层 max-w 留白让它贴合面板宽度。
          */}
          <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
            <div className="mb-2 flex items-baseline justify-between">
              <h3 className="text-sm font-semibold">模型供应商 & 模型广场</h3>
              <span className="text-[11px] text-zinc-500">
                云端 8 大供应商一行展示，"更多"看全部；下方按能力 / 来源筛选模型。
              </span>
            </div>
            <MarketplacePage embedded excludeLocal />
          </div>
        </div>
      )}

      {tab === 'services' && (
        <div className="space-y-3">
          <section>
            <h3 className="mb-2 text-sm font-semibold">运行中（runtime.json）</h3>
            {s.loading && !s.data ? (
              <div className="text-xs text-zinc-500">加载中…</div>
            ) : s.error ? (
              <div className="text-xs text-amber-600">
                未连上 /runtime/services：{String(s.error)}
              </div>
            ) : (
              <ServiceList services={s.data?.services ?? []} vendor={s.vendor} />
            )}
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold">服务拓扑 + 安装指引</h3>
            <ServiceTopologySection />
          </section>
        </div>
      )}

      {tab === 'doctor' && (
        <div className="space-y-3">
          <SafetyHintsSection />
          <DoctorPanel />
        </div>
      )}

      {tab === 'mirror' && <MirrorSection />}

      {tab === 'local' && (
        <section className="max-h-[70vh] overflow-y-auto pr-1">
          <LocalRuntimePanel />
        </section>
      )}
    </div>
  );
};

// ----- helpers -------------------------------------------------------------

function prettyBytes(n?: number): string {
  if (!n || n <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function statusClass(s: 'ok' | 'warn' | 'error' | 'skip'): string {
  return {
    ok: 'text-green-600',
    warn: 'text-amber-600',
    error: 'text-red-600',
    skip: 'text-zinc-500',
  }[s];
}

function statusLabel(s: 'ok' | 'warn' | 'error' | 'skip'): string {
  return { ok: '✓', warn: '⚠', error: '✘', skip: '·' }[s];
}

export default AiPlatformPanel;
