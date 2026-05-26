/**
 * 本地 LLM runtime 控制面 API 客户端。
 *
 * 对齐 chayuan-server `/runtime/llama/*`:
 *   GET  /runtime/llama/status        → 状态机 + endpoint + pid
 *   POST /runtime/llama/start         → 拉起 llama-server.exe
 *   POST /runtime/llama/stop          → 关停
 *   POST /runtime/llama/restart       → stop + start
 *   GET  /runtime/llama/config        → LocalRuntimeSettings
 *   POST /runtime/llama/config        → 部分更新 + 持久化 yaml
 *   GET  /runtime/llama/install-info  → 装机路径 + 二进制版本
 *
 * 设计:仅在桌面端有意义 (集成版 .msi 装机后 vendor llama-server 才在),
 * Web 端调用会返回 404 — 调用方需自己用 platform.kind 守卫。
 */

import { request } from './client';

// ─── types ───────────────────────────────────────────────────────────

export type LocalRuntimeState =
  | 'stopped'
  | 'starting'
  | 'ready'
  | 'failed'
  | 'restarting';

export interface LocalRuntimeStatus {
  state: LocalRuntimeState;
  endpoint?: string | null;
  pid?: number | null;
  model_id?: string | null;
  model_path?: string | null;
  started_at?: string | null;
  last_health_at?: string | null;
  last_error?: string | null;
}

export interface LocalRuntimeSettings {
  preload_on_startup: boolean;
  host: string;
  port: number;
  api_key: string;
  expose_lan: boolean;
  default_chat_model: string;
}

export type LocalRuntimeSettingsPatch = Partial<LocalRuntimeSettings>;

export interface LocalRuntimeInstallInfo {
  chayuan_root: string;
  models_root: string;
  bundled_models_root: string;
  llama_server_exe: string | null;
  build_version: string | null;
  /** 当前 installer 是否轻量版(没嵌 chat);后端 2026-05-16 起返回此字段。
   *  老服务端没返时前端取 undefined → 视为 false(向后兼容 full 行为)。 */
  is_lite_build?: boolean;
  /** 随包真实嵌入的 capability 短名列表(chat / embedding / rerank / asr / ocr / image) */
  bundled_caps?: string[];
  /** FULL_CAPS - bundled_caps,前端可在 footer / 设置页提示"需先下载"。 */
  missing_caps?: string[];
}

// 注:`request<T>(path)` 内部已经自动拆 `{code, msg, data}` 信封 (见 client.ts:198-211),
// `r.data` 就是已解包的内层 payload。我们这里只声明内层 T 类型即可。

// ─── client ──────────────────────────────────────────────────────────

async function getStatus(): Promise<LocalRuntimeStatus> {
  return (await request<LocalRuntimeStatus>('/runtime/llama/status')).data;
}

async function start(opts?: { model_id?: string }): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>('/runtime/llama/start', {
      method: 'POST',
      body: opts ?? {},
      timeoutMs: 90_000, // 启动 + 60s health probe 留余量
    })
  ).data;
}

async function stop(): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>('/runtime/llama/stop', {
      method: 'POST',
      timeoutMs: 20_000,
    })
  ).data;
}

async function restart(opts?: { model_id?: string }): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>('/runtime/llama/restart', {
      method: 'POST',
      body: opts ?? {},
      timeoutMs: 90_000,
    })
  ).data;
}

async function getConfig(): Promise<LocalRuntimeSettings> {
  return (await request<LocalRuntimeSettings>('/runtime/llama/config')).data;
}

async function setConfig(patch: LocalRuntimeSettingsPatch): Promise<LocalRuntimeSettings> {
  return (
    await request<LocalRuntimeSettings>('/runtime/llama/config', {
      method: 'POST',
      body: patch,
    })
  ).data;
}

async function getInstallInfo(): Promise<LocalRuntimeInstallInfo> {
  return (await request<LocalRuntimeInstallInfo>('/runtime/llama/install-info')).data;
}

// ─── Plan 3B 多 capability ───

export type LocalRuntimeCapability = 'chat' | 'embedding' | 'rerank' | 'asr' | 'image-embedding';

async function getStatusFor(cap: LocalRuntimeCapability): Promise<LocalRuntimeStatus> {
  return (await request<LocalRuntimeStatus>(`/runtime/llama/${cap}/status`)).data;
}

async function startFor(
  cap: LocalRuntimeCapability,
  opts?: { model_id?: string },
): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>(`/runtime/llama/${cap}/start`, {
      method: 'POST',
      body: opts ?? {},
      timeoutMs: 90_000,
    })
  ).data;
}

async function stopFor(cap: LocalRuntimeCapability): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>(`/runtime/llama/${cap}/stop`, {
      method: 'POST',
      timeoutMs: 20_000,
    })
  ).data;
}

async function restartFor(
  cap: LocalRuntimeCapability,
  opts?: { model_id?: string },
): Promise<LocalRuntimeStatus> {
  return (
    await request<LocalRuntimeStatus>(`/runtime/llama/${cap}/restart`, {
      method: 'POST',
      body: opts ?? {},
      timeoutMs: 90_000,
    })
  ).data;
}

async function getRegistry(): Promise<Record<LocalRuntimeCapability, LocalRuntimeStatus>> {
  return (await request<Record<LocalRuntimeCapability, LocalRuntimeStatus>>('/runtime/llama/registry'))
    .data;
}

// ─── auto-start 开关 ───
// 5 个 capability + rapidocr 各自的"是否在 chayuan-server 启动时自动拉起"开关。
// 切换 UI 不影响当前进程,只决定下次 server 启动行为(用户仍可手动启停)。
// 持久化到 <CHAYUAN_ROOT>/data/sidecar_settings.json。

export type AutoStartKey =
  | LocalRuntimeCapability
  | 'rapidocr';

export type AutoStartDict = Partial<Record<AutoStartKey, boolean>>;

async function listAutoStart(): Promise<AutoStartDict> {
  return (await request<AutoStartDict>('/runtime/llama/auto-start')).data;
}

async function setAutoStart(capability: AutoStartKey, enabled: boolean): Promise<AutoStartDict> {
  return (
    await request<AutoStartDict>('/runtime/llama/auto-start', {
      method: 'POST',
      body: { capability, enabled },
    })
  ).data;
}

// ─── PyTorch 检测 + 安装 ───
// transformers image 链的硬依赖,wheel 体积巨大不打包进 installer,装机时按需下载。
// 走「设置 → 本地模型服务 → PyTorch」一键检测 + 安装,跟 OCR 体验一致。

export type PytorchVariant = 'cpu' | 'cu118' | 'cu121' | 'cu124' | 'cu126' | 'auto';

export interface PytorchStatus {
  torch_version: string | null;
  torch_cuda_build: string | null;        // "cu124" / "cpu" / null
  torchvision_version: string | null;
  /** 整个 import 链是否健康(transformers image 路径能否走通)*/
  import_ok: boolean;
  import_error: string | null;
  has_nvidia_gpu: boolean;
  nvidia_driver: string | null;
  /** 一键自动安装时该用哪个 variant(CPU 或 cuXXX)*/
  suggested_variant: PytorchVariant;
  can_install: boolean;
  /** 内置 wheel 是否可用 — true 时即使无网也能装上(走 vendor/torch_wheels) */
  offline_wheels_available: boolean;
  offline_wheels_dir: string | null;
  /** torch 的运行时安装目标目录(<CHAYUAN_ROOT>/py_packages)。
   *  UI 展示「手动装好的 torch / 解压好的 wheel 放这里」。 */
  install_target_dir: string | null;
  /** 给前端的额外提示(驱动太老 / 版本不配等)*/
  notes: string[];
  /** PyTorch 官方下载页 —— 给「不想在线装、想自己下 wheel」的用户。 */
  download_page: string;
  /** 按建议 variant 的 wheel index 直链(CPU → /whl/cpu;GPU → /whl/cuXXX)。 */
  download_index: string;
  /** CPU 版 wheel index 直链。 */
  download_index_cpu: string;
  /** GPU 版 wheel index 直链;无 GPU / 驱动太老时为 null。 */
  download_index_cuda: string | null;
  /** 本应用固化的 torch / torchvision 版本(自己下 wheel 时对齐用)。 */
  pinned_torch_version: string;
  pinned_torchvision_version: string;
}

/** 单个候选目录里的 torch 探测结果(GET /runtime/pytorch/locations)。 */
export interface PytorchLocation {
  /** 含 torch/ 的目录绝对路径。 */
  dir: string;
  /** 目录来源:'user-selected' | 'py_packages' | 'system-python'。 */
  source: string;
  /** 目录里有合法 torch。 */
  valid: boolean;
  torch_version: string | null;
  torch_cuda_build: string | null;
  /** true = CUDA(GPU)版。 */
  is_cuda: boolean;
  torchvision_installed: boolean;
  torchvision_version: string | null;
  /** invalid 时的人类可读原因。 */
  reason: string;
}

/** torch 选用配置:auto / 指定外部目录 / 不使用 PyTorch。 */
export type PytorchSelectionMode = 'auto' | 'path' | 'disabled';

export interface PytorchSelection {
  mode: PytorchSelectionMode;
  /** mode=path 时指向的外部已装 torch 目录。 */
  path: string | null;
  /** CPU + GPU 两版都装时优先用哪个;null = 默认(优先 GPU)。 */
  prefer: 'cpu' | 'cuda' | null;
}

/** GET /runtime/pytorch/locations —— 跨目录扫 torch 汇总。 */
export interface PytorchLocationsResult {
  locations: PytorchLocation[];
  /** 任一目录有 CPU 版 torch。 */
  has_cpu: boolean;
  /** 任一目录有 CUDA(GPU)版 torch。 */
  has_cuda: boolean;
  cpu_dir: string | null;
  cuda_dir: string | null;
  selection: PytorchSelection;
  /** 当前生效的 torch 目录;disabled / 都没装时为 null。 */
  active_dir: string | null;
}

/** POST /runtime/pytorch/validate-dir —— 验证用户指定目录里的 torch。 */
export interface PytorchValidateDirResult {
  valid: boolean;
  dir: string | null;
  torch_version: string | null;
  torch_cuda_build: string | null;
  is_cuda: boolean;
  torchvision_installed: boolean;
  torchvision_version: string | null;
  /** invalid 时的人类可读原因。 */
  reason: string;
}

/** GET /runtime/pytorch/scan —— 扫描 py_packages/ 里手动放好的 torch。 */
export interface PytorchScanResult {
  /** 用户该把 torch / 解压好的 wheel 放进这个目录(绝对路径)。 */
  target_dir: string | null;
  /** py_packages/torch/ 是否完整(__init__.py + version.py 都在)。 */
  torch_installed: boolean;
  torch_version: string | null;
  /** "cu124" / "cpu" / null —— 从 torch/version.py 读出。 */
  torch_cuda_build: string | null;
  /** true = CUDA(GPU)版,false = CPU 版。 */
  is_cuda: boolean;
  torchvision_installed: boolean;
  torchvision_version: string | null;
}

/** install_task_manager.InstallTask 子集(只暴露 UI 用得到的字段)*/
export interface PytorchInstallTask {
  task_id: string;
  framework: string;
  recipe_label: string;
  state: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  log: string[];
  return_code: number | null;
  error: string;
  started_at: number;
  finished_at: number | null;
}

async function pytorchStatus(): Promise<PytorchStatus> {
  return (await request<PytorchStatus>('/runtime/pytorch/status')).data;
}

/** 扫描 py_packages/ 看用户手动放好的 torch —— 类比模型下载对话框的「扫描挂载」。 */
async function pytorchScan(): Promise<PytorchScanResult> {
  return (await request<PytorchScanResult>('/runtime/pytorch/scan')).data;
}

async function pytorchInstall(
  variant: PytorchVariant = 'auto',
  opts: { preferOffline?: boolean; force?: boolean } = {},
): Promise<PytorchInstallTask> {
  return (
    await request<PytorchInstallTask>('/runtime/pytorch/install', {
      method: 'POST',
      // force=true:后端解压前先清空 py_packages/,避免新 torch 叠在旧 torchvision
      // 上版本对不上。UI 的「安装 / 重装」按钮都传 true。
      body: { variant, prefer_offline: opts.preferOffline ?? true, force: opts.force ?? false },
      timeoutMs: 30_000,  // start 本身只是入队,几百 ms;真实安装异步跑
    })
  ).data;
}

async function pytorchInstallTask(taskId: string): Promise<PytorchInstallTask> {
  return (
    await request<PytorchInstallTask>(
      `/runtime/pytorch/install/${encodeURIComponent(taskId)}`,
    )
  ).data;
}

/** GET /runtime/pytorch/locations —— 跨目录扫 torch(CPU / GPU 版各自是否存在)。 */
async function pytorchLocations(): Promise<PytorchLocationsResult> {
  return (await request<PytorchLocationsResult>('/runtime/pytorch/locations')).data;
}

/** POST /runtime/pytorch/validate-dir —— 验证用户指定目录里有没有合法 torch。 */
async function pytorchValidateDir(path: string): Promise<PytorchValidateDirResult> {
  return (
    await request<PytorchValidateDirResult>('/runtime/pytorch/validate-dir', {
      method: 'POST',
      body: { path },
    })
  ).data;
}

/** GET /runtime/pytorch/select —— 读当前 torch 选用配置。 */
async function pytorchGetSelection(): Promise<{
  selection: PytorchSelection;
  active_dir: string | null;
}> {
  return (
    await request<{ selection: PytorchSelection; active_dir: string | null }>(
      '/runtime/pytorch/select',
    )
  ).data;
}

/** POST /runtime/pytorch/select —— 设置 torch 选用配置(auto / 指定目录 / 不使用)。 */
async function pytorchSetSelection(opts: {
  mode: PytorchSelectionMode;
  path?: string;
  prefer?: 'cpu' | 'cuda';
}): Promise<{ selection: PytorchSelection; active_dir: string | null }> {
  return (
    await request<{ selection: PytorchSelection; active_dir: string | null }>(
      '/runtime/pytorch/select',
      { method: 'POST', body: opts },
    )
  ).data;
}

// ─── 运行时服务(sidecar 二进制)下载 ───
// 「运行时服务双保险」:llama-server / whisper-server 既打包进完整安装包,
// 又支持缺失时按平台一键下载补回。对齐后端 /runtime/service-binaries/*
// (刻意避开 /runtime/services/* —— 那里 GET /{name} 会吞静态段)。

/** 单个运行时引擎(llama-server / whisper-server)的就绪状态。 */
export interface RuntimeServiceEngine {
  /** 引擎名:'llama-server' | 'whisper-server' */
  engine: string;
  /** 主 binary 名(不含 .exe) */
  binary: string;
  /** 当前平台已有可用 binary */
  present: boolean;
  /** 缺失时是否支持应用内下载补回(whisper 在非 Windows 平台为 false) */
  downloadable: boolean;
  /** 已就绪时的 binary 路径 */
  path: string | null;
  /** VERSION 文件首行(如 'b9174') */
  version: string | null;
  /** present=false 时的人类可读说明 */
  reason: string | null;
}

export interface RuntimeServicesStatus {
  /** 后端探测到的当前 vendor 平台名(如 'win-x64');无法识别为 null */
  platform: string | null;
  engines: RuntimeServiceEngine[];
}

/** 运行时服务下载任务 — 跟 PytorchInstallTask 同构。 */
export interface RuntimeServiceInstallJob {
  id: string;
  engine: string;
  platform: string;
  /** 实际使用的 GitHub 镜像前缀('' = 直连) */
  mirror: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  step: string;
  started_at: number;
  ended_at: number | null;
  exit_code: number | null;
  progress_pct: number;
  progress_message: string;
  target_dir: string | null;
  log_tail: string[];
}

async function servicesStatus(): Promise<RuntimeServicesStatus> {
  return (await request<RuntimeServicesStatus>('/runtime/service-binaries/status')).data;
}

async function installService(
  engine: string,
  opts: { platform?: string } = {},
): Promise<RuntimeServiceInstallJob> {
  return (
    await request<RuntimeServiceInstallJob>('/runtime/service-binaries/install', {
      method: 'POST',
      body: { engine, ...(opts.platform ? { platform: opts.platform } : {}) },
      timeoutMs: 30_000, // start 只是入队;真实下载异步跑
    })
  ).data;
}

async function installServiceTask(jobId: string): Promise<RuntimeServiceInstallJob> {
  return (
    await request<RuntimeServiceInstallJob>(
      `/runtime/service-binaries/install/${encodeURIComponent(jobId)}`,
    )
  ).data;
}

async function cancelInstallService(jobId: string): Promise<RuntimeServiceInstallJob> {
  return (
    await request<RuntimeServiceInstallJob>(
      `/runtime/service-binaries/install/${encodeURIComponent(jobId)}/cancel`,
      { method: 'POST' },
    )
  ).data;
}

// ─── OCR (RapidOCR 进程内 onnxruntime,没有独立 sidecar) ───

export interface RuntimeOcrHealth {
  /** 'ready' = 模型齐全可加载;'failed' = python 包或 ONNX 模型缺失 */
  state: 'ready' | 'failed';
  /** state=failed 时给出原因(用户友好,可直接展示在 footer hover title) */
  reason?: string | null;
  /** 内部诊断字段:rapidocr_pkg / models / models_dir 等 */
  detail?: Record<string, unknown>;
}

async function getOcrHealth(): Promise<RuntimeOcrHealth> {
  return (await request<RuntimeOcrHealth>('/runtime/ocr/health')).data;
}

export const runtimeOcr = {
  health: getOcrHealth,
};

export const localRuntime = {
  // 旧:chat 别名(Plan 1+2 已用,不动)
  getStatus,
  start,
  stop,
  restart,
  getConfig,
  setConfig,
  getInstallInfo,
  // Plan 3B 多 capability
  getStatusFor,
  startFor,
  stopFor,
  restartFor,
  getRegistry,
  // auto-start 开关(5 cap + rapidocr 共用一份字典)
  listAutoStart,
  setAutoStart,
  // PyTorch 检测 + 安装(transformers image 链硬依赖)
  pytorchStatus,
  pytorchScan,
  pytorchInstall,
  pytorchInstallTask,
  // PyTorch 多目录探测 + 用户自选 torch 目录 + 选用哪个/不用
  pytorchLocations,
  pytorchValidateDir,
  pytorchGetSelection,
  pytorchSetSelection,
  // 运行时服务(sidecar 二进制)下载补回
  servicesStatus,
  installService,
  installServiceTask,
  cancelInstallService,
};
