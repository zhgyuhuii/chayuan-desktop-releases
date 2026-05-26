/**
 * Platform Abstraction Layer (PAL) — 接口契约。
 *
 * 业务层（packages/app/...）只依赖此包；
 * 桌面 = packages/platform-tauri；Web = packages/platform-web。
 *
 * 所有方法返回 Promise，便于内部异步实现（Tauri invoke / IndexedDB / fetch）；
 * 平台不可用的能力以 optional 字段表达，UI 必须 capability check：
 *   `if (platform.shortcut) { ... }`
 */

export type PlatformKind = 'desktop' | 'web' | 'mobile';

export interface SecureStore {
  /** 安全 KV：桌面走 OS keychain（Tauri Stronghold）；Web 走 httpOnly cookie 兜底 + sessionStorage */
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  del(key: string): Promise<void>;
}

/**
 * 「记住我」开关在 localStorage 里的固定 key。
 * 业务层(LoginModal)写,平台层(platform-web/platform-tauri fallback)读。
 *
 * 取值约定:
 *   '1' / 缺省 → 记住(token 存 localStorage,跨刷新/关浏览器都保留)
 *   '0'        → 仅本会话(token 存 sessionStorage,关 tab 即清)
 */
export const AUTH_REMEMBER_KEY = 'cy.auth.remember';

export function isAuthPersistent(): boolean {
  try {
    return globalThis.localStorage?.getItem(AUTH_REMEMBER_KEY) !== '0';
  } catch {
    return true;
  }
}

export function setAuthPersistent(persist: boolean): void {
  try {
    globalThis.localStorage?.setItem(AUTH_REMEMBER_KEY, persist ? '1' : '0');
  } catch {
    /* 隐私模式 / SSR;静默 */
  }
}

export type DbValue = string | number | boolean | null | Uint8Array;

export interface Database {
  /** 通用 SQL 入口；桌面 = SQLite；Web = sql.js / dexie 适配 */
  exec(sql: string, params?: DbValue[]): Promise<void>;
  query<T = Record<string, DbValue>>(sql: string, params?: DbValue[]): Promise<T[]>;
}

export interface PickOptions {
  multiple?: boolean;
  /** MIME / extension 过滤；如 'image/*'、['.pdf','.docx'] */
  accept?: string | string[];
}

export interface FsApi {
  pickFiles(opts?: PickOptions): Promise<File[]>;
  /** 选择目录(含其下全部文件,含嵌套子目录)。
   * 返回的 File 上 `webkitRelativePath` 会被填成相对所选根的路径,
   * 比如选了 `~/docs`,内含 `~/docs/sub/a.pdf`,则该 File 的
   * webkitRelativePath = `docs/sub/a.pdf`(浏览器原生行为)。
   * Tauri 端用 readDir 自实现,同样填 webkitRelativePath 以保持调用方零分支。 */
  pickDirectory?(): Promise<File[]>;
  saveText(name: string, content: string): Promise<void>;
  /** 从 DragEvent 中提取文件；统一 desktop / web 行为 */
  readDropped(event: DragEvent): Promise<File[]>;
}

export interface NetApi {
  /** 通用 fetch；桌面端走 Tauri http plugin（绕 CORS），web 端走 window.fetch */
  fetch(input: string | URL | Request, init?: RequestInit): Promise<Response>;
  /** SSE 入口：等同 fetch + 强制 Accept: text/event-stream，便于平台特化（worker） */
  sse(input: string | URL, init: RequestInit): Promise<Response>;
}

export interface ClipboardApi {
  readText(): Promise<string>;
  writeText(text: string): Promise<void>;
  readImage?(): Promise<Blob | null>;
}

export interface NotifyApi {
  show(title: string, body: string, opts?: { icon?: string }): Promise<void>;
  /** 是否需要先请求权限；web 上才需要 */
  requestPermission?(): Promise<'granted' | 'denied' | 'default'>;
}

export interface ShortcutApi {
  /** 全局快捷键；返回 unregister 句柄。仅桌面 */
  register(combo: string, handler: () => void): Promise<() => Promise<void>>;
}

export interface TrayItem {
  id: string;
  label: string;
  enabled?: boolean;
  separator?: boolean;
  onClick?: () => void;
}

export interface TrayApi {
  setMenu(items: TrayItem[]): Promise<void>;
  setTooltip(text: string): Promise<void>;
}

export interface CaptureApi {
  /** 截图（屏幕 / 窗口）；仅桌面。返回的是**全屏 PNG 原图**,caller 自己做裁剪。 */
  screenshot(opts?: { region?: 'fullscreen' | 'window' | 'area' }): Promise<Blob>;
  /**
   * 交互式截图:像微信 / QQ / 截图工具那样,弹一个**全屏覆盖整个桌面**的透明窗
   * 让用户拖框选区,返回**已裁剪**的 PNG Blob。Tauri 端实现,Web 端可不实现
   * (caller 应该 fallback 到 ``screenshot()`` + 应用内 dialog 选区)。
   *
   * 用户取消 reject ``DOMException('cancelled', 'AbortError')`` —— 跟 web 端
   * ``getDisplayMedia`` 用户拒绝的错误同形,caller 一条 catch 通吃。
   */
  screenshotInteractive?(): Promise<Blob>;
}

export interface UpdateInfo {
  available: boolean;
  version?: string;
  notes?: string;
}

export interface UpdaterApi {
  check(): Promise<UpdateInfo>;
  install(): Promise<void>;
}

export type WindowDockPosition = 'left' | 'center' | 'right';

export interface WindowApi {
  minimize?(): Promise<void>;
  maximize?(): Promise<void>;
  close?(): Promise<void>;
  /** 桌面端:把窗口贴到屏幕左/中/右半边(参考图"窗口位置")。Web 端不实现 */
  setDock?(position: WindowDockPosition): Promise<void>;
  /** 主题变更回调；返回 unsubscribe */
  onThemeChange(cb: (dark: boolean) => void): () => void;
  isDarkSystem(): boolean;
}

export interface ShellApi {
  /** 在系统默认浏览器打开 URL（Tauri 用 plugin-shell.open；Web 用 window.open） */
  openExternal(url: string): Promise<void>;
  /**
   * 在系统文件管理器里打开**本地文件夹路径**(Windows 资源管理器 /
   * macOS 访达 / Linux 文件管理器)。不是 URL —— 不要用 openExternal 传
   * file://(会被 plugin-shell 的 URL 正则拒)。Web 端无此能力,实现 noop。
   */
  openPath(path: string): Promise<void>;
}

/**
 * 跨平台 confirm / prompt / message 弹窗。
 *
 * 不要直接用浏览器原生 ``window.confirm`` / ``window.prompt``:
 * Tauri 2 webview 把这些 API 重定向到 plugin-dialog 的 IPC command,需要 capabilities 显式
 * 放行;某些版本 / 配置下重定向链路对不上,会报 "dialog.confirm not allowed. Command not
 * found"。统一走 ``platform.dialog`` 在 Tauri 端直接用 ``@tauri-apps/plugin-dialog`` 的 ask /
 * 在 Web 端兜底原生 confirm / prompt,避免跨平台分支。
 */
export interface DialogApi {
  /** Yes/No 确认框。返 true=确认 / false=取消 */
  confirm(message: string, opts?: { title?: string; okLabel?: string; cancelLabel?: string }): Promise<boolean>;
  /** 单行文本输入框。返用户输入(取消返 null)。Tauri 端目前没有原生 prompt,会用应用内 fallback。 */
  prompt(message: string, opts?: { title?: string; defaultValue?: string }): Promise<string | null>;
  /** 信息提示框。无返回 */
  message(message: string, opts?: { title?: string; kind?: 'info' | 'warning' | 'error' }): Promise<void>;
}

/**
 * 子窗口规格(用于文件预览的"独立窗口"模式)。
 *
 * 设计:
 *   - url 是相对应用根的 path,如 `/preview-window?ku=...&file=...`,
 *     Tauri 端需用 `WebviewWindow` 配合内部 devUrl + path,Web 端直接 window.open。
 *   - 业务侧不关心实现,只看 capability 是否存在(getPlatform().preview 不存在 → 退化 floating)。
 */
export interface PreviewWindowSpec {
  /** 应用内路径(以 / 开头);URL 同源,内部加载 */
  path: string;
  /** OS 窗口标题 */
  title?: string;
  /** 初始尺寸(逻辑像素) */
  width?: number;
  height?: number;
  /** 唯一 label,Tauri 用来索引;同 label 已存在则 focus */
  label?: string;
}

/** PreviewWindowApi.openWindow 的返回句柄 */
export interface PreviewWindowHandle {
  /** 程序化关闭(用户也可以直接点系统按钮关) */
  close(): Promise<void>;
  /** 把窗口提到前面 */
  focus(): Promise<void>;
  /** 用户/程序关闭时触发;只触发一次 */
  onClosed(cb: () => void): () => void;
}

export interface PreviewWindowApi {
  /** 打开预览子窗口;label 已存在则 focus 并复用句柄 */
  openWindow(spec: PreviewWindowSpec): Promise<PreviewWindowHandle>;
  /** 当前是否在子窗内运行(决定 standalone route 是否生效) */
  isStandalone(): boolean;
}

/**
 * 单机版数据目录管理(Phase 1 引入)。
 *
 * 桌面端首启动时,Shell 通过 ``state()`` 检测用户是否已选过数据目录。
 * 未选 → 弹 FirstRunSetup,用户挑路径后调 ``set()`` 持久化。
 * 选定的路径在下一次启动通过 ``CHAYUAN_ROOT`` 环境变量注入服务端 sidecar。
 *
 * Web 构建无此能力(``getPlatform().dataDir`` 为 undefined),Shell 直接放行。
 */
export interface DataDirState {
  /** 用户是否已经在向导里选过路径(持久化文件存在且可解析) */
  configured: boolean;
  /** 当前生效路径:已配置 → 用户选的;未配置 → OS 默认 */
  path: string;
  /** OS 默认路径(向导 placeholder / "使用默认"按钮) */
  defaultPath: string;
  /** 持久化文件位置,诊断 / "切换数据目录"向导回显 */
  configFile: string;
  /** ``path`` 下是否已有察元历史数据(marker / sqlite / data 子目录命中) */
  hasExistingData: boolean;
  /** 便携模式候选目录(``<exe_dir>/data/``),可写且不在 Program Files 时填,
   *  否则 ``null``。前端首启向导可据此渲染"便携模式(整盘可拷贝)"按钮。 */
  portableCandidate: string | null;
}

export interface PickDirectoryOptions {
  title?: string;
  defaultPath?: string;
}

export interface DataDirApi {
  /** 启动时探测当前数据目录配置态;Shell 用它决定是否弹首启动向导。 */
  state(): Promise<DataDirState>;
  /** 仅返回 OS 默认路径。 */
  defaultPath(): Promise<string>;
  /** 检测候选路径下是否已经有察元历史数据(用户在 input 里改路径时调) */
  checkExisting(path: string): Promise<boolean>;
  /** 用原生「选择文件夹」对话框挑路径;取消返回 null。 */
  pickDirectory(opts?: PickDirectoryOptions): Promise<string | null>;
  /** 持久化用户选定的数据目录;后端会 mkdir -p + 写 marker。返回新状态。 */
  set(path: string): Promise<DataDirState>;
  /** 清空持久化(切换数据目录向导用),**不删除数据本身**。 */
  reset(): Promise<void>;
}

/**
 * 单机模式 chayuan-server sidecar 状态(Phase 3)。
 *
 * - ``idle``:未启动(等 FirstRunSetup 确认后调 ``start``)
 * - ``starting``:已 spawn,``/healthz`` 还没返 200
 * - ``ready``:业务请求可发,``baseUrl`` 可用
 * - ``failed``:启动失败 / 健康探测超时 / 进程异常退出
 * - ``stopped``:用户主动停(关窗 / 切换数据目录)
 * - ``disabled``:dev 模式跳过 spawn(开发者自己跑 ``poetry run chayuan start -a``)
 */
export type SidecarStateName =
  | 'idle'
  | 'starting'
  | 'ready'
  | 'failed'
  | 'stopped'
  | 'disabled';

export interface SidecarStatus {
  state: SidecarStateName;
  port: number;
  /** 业务侧直接拿这个填 ``configureClient({ baseURL })``;dev/disabled 时也是 62581 */
  baseUrl: string;
  restarts: number;
  error: string | null;
  /** 子进程 stdout/stderr 最新一行,banner 实时滚动 */
  lastLogLine: string | null;
  /** 当前数据目录(诊断 / 切换向导回显) */
  dataDir: string;
}

export interface SidecarApi {
  /** 当前快照。Shell 启动后第一次拿状态用。 */
  status(): Promise<SidecarStatus>;
  /**
   * 启动 sidecar(把数据目录通过 ``CHAYUAN_ROOT`` env 注入子进程)。
   * FirstRunSetup 确认后 Shell 调一次。已 ready/starting 是 no-op。
   *
   * vendorPlatform: 可选 CPU 变体覆盖,对齐 chayuan-server 的 CHAYUAN_VENDOR_PLATFORM env。
   * 「本地模型」设置页选了非默认变体(win-x64-noavx / win-x64-avx512 等)时填这个;
   * 不传 = sidecar 走 _platform_subdir_candidates 自动检测。
   */
  start(args: { dataDir: string; vendorPlatform?: string }): Promise<SidecarStatus>;
  /** 主动停(关窗 / 切换数据目录向导)。 */
  kill(): Promise<SidecarStatus>;
  /**
   * 订阅状态/日志事件(Tauri ``Emitter``):
   *   - ``cy:sidecar-state``: SidecarStateName
   *   - ``cy:sidecar-log``: string(stdout/stderr 单行)
   * 返回 unsubscribe。
   */
  subscribe(handlers: {
    onState?: (state: SidecarStateName) => void;
    onLog?: (line: string) => void;
  }): Promise<() => void>;
}

export interface RuntimeInfo {
  appName: string;
  appVersion: string;
  /** 用于 Sentry/Langfuse release 字段：`<kind>@<version>` */
  release: string;
  /** 默认后端 base，由各平台决定（desktop 直连，web 同源） */
  defaultApiBase: string;
  /**
   * BCP-47 系统语言。
   * - Tauri:tauri-plugin-os.locale() 拿到的真实 OS locale,如 'zh-CN'
   * - Web:navigator.language(WebView2 在 Tauri 下不可靠,仅 web 用)
   * 客户端 i18n 用此值 + 'system' 偏好做语言推导。
   */
  systemLocale?: string;
}

export interface Platform {
  kind: PlatformKind;
  runtime: RuntimeInfo;
  secure: SecureStore;
  db: Database;
  fs: FsApi;
  net: NetApi;
  clipboard: ClipboardApi;
  notify: NotifyApi;
  window: WindowApi;
  shell: ShellApi;
  dialog: DialogApi;

  // 可选能力
  shortcut?: ShortcutApi;
  tray?: TrayApi;
  capture?: CaptureApi;
  updater?: UpdaterApi;
  /** 文件预览子窗口能力(Tauri 用 WebviewWindow,Web 用 window.open) */
  preview?: PreviewWindowApi;
  /** 单机版数据目录管理(仅桌面端;Web 构建为 undefined) */
  dataDir?: DataDirApi;
  /** 单机版 chayuan-server sidecar 启停(仅桌面端;Web 构建为 undefined) */
  sidecar?: SidecarApi;
}

// ──────────────────────────────────────────────────────────────
// 单例桥接（业务层 import { platform } from '@chayuan/platform'，
// 而具体哪一份实现在 apps/{desktop,web}/src/main.tsx 注入）
// ──────────────────────────────────────────────────────────────

let _platform: Platform | null = null;

export function setPlatform(p: Platform): void {
  _platform = p;
}

export function getPlatform(): Platform {
  if (!_platform) {
    throw new Error(
      '[platform] 尚未注入；请在 apps/*/main.tsx 中 setPlatform(platformImpl) 后再调用业务代码',
    );
  }
  return _platform;
}

export function isDesktop(): boolean {
  return _platform?.kind === 'desktop';
}

export function isWeb(): boolean {
  return _platform?.kind === 'web';
}

// ──────────────────────────────────────────────────────────────
// 共享小工具：所有平台都要用的纯函数放这里
// ──────────────────────────────────────────────────────────────

/** 跨端生成 UUID v4；优先 crypto.randomUUID */
export function uuid(): string {
  const c = (globalThis as unknown as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  // RFC4122 v4 polyfill
  const r = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(r);
  else for (let i = 0; i < 16; i++) r[i] = Math.floor(Math.random() * 256);
  r[6] = (r[6]! & 0x0f) | 0x40;
  r[8] = (r[8]! & 0x3f) | 0x80;
  const h = Array.from(r, (b) => b.toString(16).padStart(2, '0'));
  return `${h.slice(0, 4).join('')}-${h.slice(4, 6).join('')}-${h.slice(6, 8).join('')}-${h.slice(8, 10).join('')}-${h.slice(10, 16).join('')}`;
}

/** W3C traceparent header；与 Langfuse / OTel 对齐 */
export function makeTraceparent(traceId: string, parentSpanId?: string): string {
  const tid = traceId.replaceAll('-', '').padEnd(32, '0').slice(0, 32);
  const sid = (parentSpanId ?? uuid().replaceAll('-', '').slice(0, 16))
    .padEnd(16, '0')
    .slice(0, 16);
  return `00-${tid}-${sid}-01`;
}
