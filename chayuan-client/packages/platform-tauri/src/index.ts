/**
 * Tauri 2 平台实现。
 *
 * 设计要点：
 * - 全部能力 lazy-import，避免启动时同步加载所有 plugin（冷启动性能）。
 * - DB 单例：app 生命周期共享一个连接。
 * - SecureStore 走 Stronghold（Argon2 + ChaCha20），失败软回退 sessionStorage（仅开发兜底）。
 * - net.fetch / SSE 都走 webview 原生 fetch:
 *   - 单机版后端 CORS 全开(``allow_origins=["*"]``),不需要 plugin-http 绕 CORS。
 *   - plugin-http 走 Rust reqwest,在某些 Windows 环境下对 loopback 会被中间层(系统代理 / 杀软网络层 / IPv6 协议栈差异)截到 502,而 webview 原生 fetch 走 WebView2 内置 HTTP 栈,跟浏览器一致 — 用户能在浏览器打开 ``127.0.0.1:62581`` 就一定走得通。
 *   - 副作用:DevTools Network 现在能看到全部业务请求(plugin-http 请求只暴露 IPC invocation,不显示真实 HTTP),排查问题更直观。
 *   - 历史代价:plugin-http 之前还能拦截 CSP 之外的 host;现在 connect-src 必须把后端 host 列全(已在 ``tauri.conf.json`` 列了 ``http://127.0.0.1:62581``)。
 */

import type {
  CaptureApi,
  ClipboardApi,
  DataDirApi,
  DataDirState,
  DbValue,
  DialogApi,
  FsApi,
  Database as IDatabase,
  NetApi,
  NotifyApi,
  PickDirectoryOptions,
  PickOptions,
  Platform,
  PreviewWindowApi,
  PreviewWindowHandle,
  PreviewWindowSpec,
  RuntimeInfo,
  SecureStore,
  ShellApi,
  ShortcutApi,
  SidecarApi,
  SidecarStateName,
  SidecarStatus,
  TrayApi,
  TrayItem,
  UpdaterApi,
  WindowApi,
} from '@chayuan/platform-shared';
import { isAuthPersistent } from '@chayuan/platform-shared';

// ──────────────────────────────────────────────────────────────
// SecureStore（Stronghold）
// ──────────────────────────────────────────────────────────────

const STRONGHOLD_VAULT = 'chayuan.vault';
const STRONGHOLD_CLIENT = 'chayuan';

let strongholdInstance: unknown = null;
let strongholdClient: unknown = null;
/**
 * Stronghold 一次性初始化失败 → 整个会话回落到 WebView 的 localStorage / sessionStorage,
 * 由「记住我」(isAuthPersistent) 决定走哪一个。
 *
 * 比 Stronghold 弱的是缺加密,但 dev 期间或 Stronghold 配错时这是合理折中。
 * Rust 侧 Builder::new(|password| argon2(...)) 配齐后才会回到 Stronghold 主路径。
 */
let strongholdBroken = false;
const FALLBACK_PREFIX = 'cy.tauri-secure.';
function _lsGet(k: string): string | null {
  try {
    return globalThis.localStorage?.getItem(FALLBACK_PREFIX + k) ?? null;
  } catch {
    return null;
  }
}
function _lsSet(k: string, v: string): void {
  try {
    globalThis.localStorage?.setItem(FALLBACK_PREFIX + k, v);
  } catch {
    /* 静默 */
  }
}
function _lsDel(k: string): void {
  try {
    globalThis.localStorage?.removeItem(FALLBACK_PREFIX + k);
  } catch {
    /* 静默 */
  }
}
function _ssGet(k: string): string | null {
  try {
    return globalThis.sessionStorage?.getItem(FALLBACK_PREFIX + k) ?? null;
  } catch {
    return null;
  }
}
function _ssSet(k: string, v: string): void {
  try {
    globalThis.sessionStorage?.setItem(FALLBACK_PREFIX + k, v);
  } catch {
    /* 静默 */
  }
}
function _ssDel(k: string): void {
  try {
    globalThis.sessionStorage?.removeItem(FALLBACK_PREFIX + k);
  } catch {
    /* 静默 */
  }
}
const fallbackStore = {
  get(key: string): string | null {
    return _lsGet(key) ?? _ssGet(key);
  },
  set(key: string, value: string): void {
    if (isAuthPersistent()) {
      _lsSet(key, value);
      _ssDel(key);
    } else {
      _ssSet(key, value);
      _lsDel(key);
    }
  },
  del(key: string): void {
    _lsDel(key);
    _ssDel(key);
  },
};

async function ensureStronghold(): Promise<{ client: any; stronghold: any } | null> {
  if (strongholdBroken) return null;
  try {
    const stronghold = await import('@tauri-apps/plugin-stronghold');
    const Stronghold = (stronghold as any).Stronghold;
    const { appDataDir, join } = await import('@tauri-apps/api/path');

    if (!strongholdInstance) {
      const dir = await appDataDir();
      const path = await join(dir, `${STRONGHOLD_VAULT}.hold`);
      const password = await getDeviceVaultPassword();
      strongholdInstance = await Stronghold.load(path, password);
    }
    if (!strongholdClient) {
      try {
        strongholdClient = await (strongholdInstance as any).loadClient(STRONGHOLD_CLIENT);
      } catch {
        strongholdClient = await (strongholdInstance as any).createClient(STRONGHOLD_CLIENT);
      }
    }
    return {
      client: strongholdClient,
      stronghold: strongholdInstance,
    };
  } catch (e) {
    strongholdBroken = true;
    console.warn('[chayuan] Stronghold 初始化失败,本会话回落到 localStorage(刷新可保留登录):', e);
    return null;
  }
}

async function getDeviceVaultPassword(): Promise<string> {
  const { invoke } = await import('@tauri-apps/api/core');
  // 由 Rust 侧返回派生密码（platform_id + appdata salt）；fallback 为 dev 密码
  try {
    return await invoke<string>('chayuan_vault_password');
  } catch {
    return 'chayuan-dev-vault-do-not-use-in-prod';
  }
}

const tauriSecure: SecureStore = {
  async get(key) {
    const sh = await ensureStronghold();
    if (!sh) return fallbackStore.get(key);
    try {
      const data = await (sh.client as any).getStore().get(key);
      if (!data) return null;
      return new TextDecoder().decode(new Uint8Array(data));
    } catch {
      return null;
    }
  },
  async set(key, value) {
    const sh = await ensureStronghold();
    if (!sh) {
      fallbackStore.set(key, value);
      return;
    }
    try {
      const bytes = Array.from(new TextEncoder().encode(value));
      await (sh.client as any).getStore().insert(key, bytes);
      await (sh.stronghold as any).save();
    } catch (e) {
      console.warn('[chayuan] secure.set 写 Stronghold 失败,回落 localStorage:', e);
      fallbackStore.set(key, value);
    }
  },
  async del(key) {
    fallbackStore.del(key);
    const sh = await ensureStronghold();
    if (!sh) return;
    try {
      await (sh.client as any).getStore().remove(key);
      await (sh.stronghold as any).save();
    } catch {
      /* ignore */
    }
  },
};

// ──────────────────────────────────────────────────────────────
// Database（plugin-sql / sqlite）
// ──────────────────────────────────────────────────────────────

let dbInstance: any = null;

async function ensureDb(): Promise<any> {
  if (dbInstance) return dbInstance;
  const Database = (await import('@tauri-apps/plugin-sql')).default;
  dbInstance = await Database.load('sqlite:chayuan.db');
  await runMigrations(dbInstance);
  return dbInstance;
}

async function runMigrations(db: any): Promise<void> {
  // 增量迁移；表结构与 packages/observability、packages/app 共用
  const stmts = [
    `CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)`,
    `CREATE TABLE IF NOT EXISTS conversations (
       id TEXT PRIMARY KEY,
       remote_id TEXT,
       title TEXT NOT NULL,
       model TEXT NOT NULL,
       mode TEXT NOT NULL,
       meta JSON,
       created_at INTEGER NOT NULL,
       updated_at INTEGER NOT NULL
     )`,
    `CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC)`,
    `CREATE TABLE IF NOT EXISTS messages (
       id TEXT PRIMARY KEY,
       conv_id TEXT NOT NULL,
       role TEXT NOT NULL,
       parts JSON NOT NULL,
       trace_id TEXT,
       created_at INTEGER NOT NULL,
       FOREIGN KEY(conv_id) REFERENCES conversations(id) ON DELETE CASCADE
     )`,
    `CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, created_at)`,
    `CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='', tokenize='porter unicode61')`,
    `CREATE TABLE IF NOT EXISTS lf_outbox (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       payload JSON NOT NULL,
       attempts INTEGER NOT NULL DEFAULT 0,
       created_at INTEGER NOT NULL
     )`,
    `CREATE INDEX IF NOT EXISTS idx_lf_outbox_created ON lf_outbox(created_at)`,
  ];
  for (const sql of stmts) await db.execute(sql);
}

const tauriDb: IDatabase = {
  async exec(sql, params) {
    const db = await ensureDb();
    await db.execute(sql, params);
  },
  async query<T>(sql: string, params?: DbValue[]): Promise<T[]> {
    const db = await ensureDb();
    return (await db.select(sql, params)) as T[];
  },
};

// ──────────────────────────────────────────────────────────────
// Fs（plugin-dialog + plugin-fs）
// ──────────────────────────────────────────────────────────────

const tauriFs: FsApi = {
  async pickFiles(opts?: PickOptions): Promise<File[]> {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const { readFile } = await import('@tauri-apps/plugin-fs');
    const filters = optsToFilters(opts);
    const selected = await open({
      multiple: !!opts?.multiple,
      directory: false,
      filters,
    });
    if (!selected) return [];
    const paths = Array.isArray(selected) ? selected : [selected];
    const files: File[] = [];
    for (const p of paths) {
      const bytes = await readFile(p);
      const name = p.split(/[\\/]/).pop() ?? 'file';
      // ⚠ 不传 type 会导致 caller 的 `f.type.startsWith('image/')` 全失效 —
      // 比如"图转文"页面会因此拒收所有 Tauri 选出的图片。按扩展名兜底推 MIME,
      // 跟浏览器 File 行为对齐。
      files.push(new File([new Uint8Array(bytes)], name, { type: mimeFromName(name) }));
    }
    return files;
  },

  async pickDirectory(): Promise<File[]> {
    // 桌面端选目录:plugin-dialog 的 directory: true,然后 readDir 递归遍历
    const { open } = await import('@tauri-apps/plugin-dialog');
    const { readDir, readFile } = await import('@tauri-apps/plugin-fs');
    const root = await open({ directory: true, multiple: false });
    if (!root || Array.isArray(root)) return [];
    const rootPath = root as string;
    const rootName = rootPath.split(/[\\/]/).pop() || 'folder';
    const out: File[] = [];

    // 自实现递归 walk;跳过 dotfiles / node_modules / .git 等垃圾目录
    const SKIP = new Set(['.git', 'node_modules', '.DS_Store', '__MACOSX']);
    type Entry = { name: string; isDirectory?: boolean; isFile?: boolean };
    const walk = async (dir: string, rel: string): Promise<void> => {
      let entries: Entry[];
      try { entries = (await readDir(dir)) as Entry[]; }
      catch { return; }
      for (const e of entries) {
        if (!e.name || SKIP.has(e.name) || e.name.startsWith('.')) continue;
        const sub = `${dir}/${e.name}`;
        const subRel = rel ? `${rel}/${e.name}` : e.name;
        if (e.isDirectory) {
          await walk(sub, subRel);
        } else if (e.isFile) {
          try {
            const bytes = await readFile(sub);
            const f = new File([new Uint8Array(bytes)], e.name, { type: mimeFromName(e.name) });
            // 对齐 web 行为:暴露 webkitRelativePath = '<rootName>/<rel>'
            Object.defineProperty(f, 'webkitRelativePath', {
              value: `${rootName}/${subRel}`,
              configurable: false, enumerable: true, writable: false,
            });
            out.push(f);
          } catch {
            /* 单文件失败不阻塞;最终上传时 backend 自然报缺失 */
          }
        }
      }
    };
    await walk(rootPath, '');
    return out;
  },

  async saveText(name, content) {
    const { save } = await import('@tauri-apps/plugin-dialog');
    const { writeTextFile } = await import('@tauri-apps/plugin-fs');
    const path = await save({ defaultPath: name });
    if (!path) return;
    await writeTextFile(path, content);
  },

  async readDropped(event: DragEvent): Promise<File[]> {
    // Tauri 也透传标准 DataTransfer，复用 web 实现
    const list: File[] = [];
    if (event.dataTransfer?.items) {
      for (const item of Array.from(event.dataTransfer.items)) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) list.push(f);
        }
      }
    } else if (event.dataTransfer?.files) {
      for (const f of Array.from(event.dataTransfer.files)) list.push(f);
    }
    return list;
  },
};

/**
 * 按文件扩展名推 MIME — Tauri plugin-fs.readFile 只给 bytes 不给 MIME,这里补一
 * 张最小映射表,覆盖图像/文档/视频/音频/办公场景。命中失败返空(File.type 留空,
 * 不影响 caller 后续的 MIME-agnostic 处理 — 但有名字总比啥都没有强)。
 */
function mimeFromName(name: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(name);
  if (!m) return '';
  const ext = m[1]!.toLowerCase();
  // 高频图像 — 跟 'image/*' picker 接受的扩展名对齐
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg';
  if (ext === 'png') return 'image/png';
  if (ext === 'webp') return 'image/webp';
  if (ext === 'bmp') return 'image/bmp';
  if (ext === 'gif') return 'image/gif';
  if (ext === 'tif' || ext === 'tiff') return 'image/tiff';
  if (ext === 'avif') return 'image/avif';
  if (ext === 'heic' || ext === 'heif') return 'image/heic';
  if (ext === 'svg') return 'image/svg+xml';
  if (ext === 'ico') return 'image/x-icon';
  // 文档 / 文本
  if (ext === 'pdf') return 'application/pdf';
  if (ext === 'md' || ext === 'markdown') return 'text/markdown';
  if (ext === 'txt') return 'text/plain';
  if (ext === 'csv') return 'text/csv';
  if (ext === 'json') return 'application/json';
  if (ext === 'html' || ext === 'htm') return 'text/html';
  // 办公(MS / WPS)
  if (ext === 'docx') return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  if (ext === 'doc') return 'application/msword';
  if (ext === 'xlsx') return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  if (ext === 'xls') return 'application/vnd.ms-excel';
  if (ext === 'pptx') return 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
  if (ext === 'ppt') return 'application/vnd.ms-powerpoint';
  // 音视频(语音消息 / KB 上传)
  if (ext === 'mp3') return 'audio/mpeg';
  if (ext === 'wav') return 'audio/wav';
  if (ext === 'webm') return 'audio/webm';
  if (ext === 'ogg') return 'audio/ogg';
  if (ext === 'mp4') return 'video/mp4';
  if (ext === 'mov') return 'video/quicktime';
  return '';
}

function optsToFilters(opts?: PickOptions) {
  if (!opts?.accept) return undefined;
  const exts = (Array.isArray(opts.accept) ? opts.accept : [opts.accept])
    .flatMap((s) =>
      s.startsWith('.')
        ? [s.slice(1)]
        : s === 'image/*'
          ? ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']
          : [],
    )
    .filter(Boolean);
  return exts.length ? [{ name: 'files', extensions: exts }] : undefined;
}

// ──────────────────────────────────────────────────────────────
// Net(全部走 webview 原生 fetch — 含 REST + SSE)
// ──────────────────────────────────────────────────────────────
//
// 历史:这里曾经把 REST 走 ``@tauri-apps/plugin-http``(Rust reqwest)、SSE 走原生
// fetch。原因是当时担心浏览器 CORS。但单机版后端 CORS 全开 + tauri.conf.json
// 的 ``connect-src`` 已经把 ``http://127.0.0.1:62581`` 列入,跨域不再是问题。
//
// 切回原生 fetch 的强动机是:Windows 上 reqwest 对 loopback 在部分用户机器上
// 会拿到 502 + ``connection: close`` + ``content-length: 0`` 的响应(浏览器和
// PowerShell 直连同地址都 200);定位过系统代理 / NO_PROXY / IPv6 dual stack
// 都不命中。WebView2 自带的 HTTP 栈跟浏览器一致,用户能在浏览器打开 62581 就
// 一定能跑通,直接绕开 reqwest 这一层。
//
// 副作用:plugin-http capabilities 仍然保留(没有别处依赖,但移除收益小且会
// 让旧版 dev 客户端连不上,不动它)。

const tauriNet: NetApi = {
  async fetch(input, init) {
    return globalThis.fetch(input as RequestInfo, init);
  },
  async sse(input, init) {
    return globalThis.fetch(input.toString(), init);
  },
};

// ──────────────────────────────────────────────────────────────
// Clipboard / Notify / Shortcut / Tray / Capture / Updater / Window / Shell
// ──────────────────────────────────────────────────────────────

const tauriClipboard: ClipboardApi = {
  async readText() {
    const m = await import('@tauri-apps/plugin-clipboard-manager');
    return (await m.readText()) ?? '';
  },
  async writeText(text) {
    const m = await import('@tauri-apps/plugin-clipboard-manager');
    await m.writeText(text);
  },
  async readImage() {
    try {
      const m = await import('@tauri-apps/plugin-clipboard-manager');
      const img = await m.readImage();
      const bytes = await img.rgba();
      return new Blob([new Uint8Array(bytes)], { type: 'image/png' });
    } catch {
      return null;
    }
  },
};

const tauriNotify: NotifyApi = {
  async show(title, body, opts) {
    const m = await import('@tauri-apps/plugin-notification');
    let granted = await m.isPermissionGranted();
    if (!granted) granted = (await m.requestPermission()) === 'granted';
    if (granted) m.sendNotification({ title, body, icon: opts?.icon });
  },
};

const tauriShortcut: ShortcutApi = {
  async register(combo, handler) {
    const m = await import('@tauri-apps/plugin-global-shortcut');
    await m.register(combo, (ev) => {
      if (ev.state === 'Released') handler();
    });
    return async () => {
      await m.unregister(combo);
    };
  },
};

const tauriTray: TrayApi = {
  async setMenu(items: TrayItem[]) {
    // 需要 Rust 侧 commands 配合；这里仅做事件分发占位
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('chayuan_tray_set_menu', {
      items: items.map((i) => ({
        id: i.id,
        label: i.label,
        enabled: i.enabled !== false,
        separator: !!i.separator,
      })),
    }).catch(() => undefined);
    // 监听点击：Rust 侧发出 'tray-click' event，前端在此分发
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    const win = getCurrentWindow();
    win.listen<string>('tray-click', (ev: { payload: string }) => {
      const id = ev.payload;
      const target = items.find((i) => i.id === id);
      target?.onClick?.();
    });
  },
  async setTooltip(text) {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('chayuan_tray_set_tooltip', { text }).catch(() => undefined);
  },
};

const tauriCapture: CaptureApi = {
  async screenshot() {
    const { invoke } = await import('@tauri-apps/api/core');
    const bytes = await invoke<number[]>('chayuan_screenshot');
    return new Blob([new Uint8Array(bytes)], { type: 'image/png' });
  },

  /**
   * 微信 / QQ / Lightshot 风格的"系统级交互截图":
   *   1. 抓主显示器原图 → 存 Rust state(零序列化开销)
   *   2. 主窗用 ``WebviewWindow`` 创建一个**无边框 / 透明 / always-on-top /
   *      skipTaskbar / 全显示器尺寸** 的 overlay 窗(label=screenshot-overlay)
   *   3. overlay 加载 ``/screenshot-overlay`` 路由 → 主程序 main.tsx 早期分支
   *      检测到这个路径,渲染独立的 ``ScreenshotOverlayApp``(不挂 Shell / 路由 / 鉴权)
   *   4. ScreenshotOverlayApp 从 Rust 拉原图字节 → 渲全屏 → 拖框选区 → 用户
   *      Enter/确认 → canvas 裁剪 → ``chayuan_submit_screenshot_result(bytes)``
   *      → Rust emit ``chayuan://screenshot/done`` + 自关 overlay 窗
   *   5. 主窗 listen 到事件 → 调 ``chayuan_take_screenshot_result`` → Blob
   *
   * 用户 Esc / 关窗 → 同样 submit 但 bytes=null → Rust 拿到 None → 主窗 reject
   * AbortError,caller 跟 web 取消同形处理。
   *
   * 多显示器:当前 V1 只覆盖主显示器(xcap 也只抓主屏);多屏用户在主屏框任意
   * 区域。后续要支持的话:Rust 端走 ``Monitor::all()`` 拼大图,window 几何
   * 设成所有屏的 union bounding box。
   */
  async screenshotInteractive(): Promise<Blob> {
    const { invoke } = await import('@tauri-apps/api/core');
    const { listen } = await import('@tauri-apps/api/event');
    const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');

    // 1. 抓屏写到 state + 拿主屏几何
    const geom = await invoke<{ x: number; y: number; width: number; height: number }>(
      'chayuan_capture_for_overlay',
    );

    // 2. 关掉残留的 overlay(异常路径下可能没清干净;Tauri 不允许同 label 重开)
    try {
      const stale = await WebviewWindow.getByLabel('screenshot-overlay');
      if (stale) await stale.close();
    } catch { /* ignore */ }

    // 3. 创窗口:无边框 / 透明 / 置顶 / 不进任务栏;盖在主显示器上
    const win = new WebviewWindow('screenshot-overlay', {
      url: '/screenshot-overlay',
      decorations: false,
      transparent: true,
      alwaysOnTop: true,
      skipTaskbar: true,
      resizable: false,
      maximizable: false,
      fullscreen: false,  // 用 inner_size 精确盖主屏 — fullscreen=true 在 Windows 上会触发 OS 全屏动画,跟"截图"语义不搭
      focus: true,
      x: geom.x,
      y: geom.y,
      width: geom.width,
      height: geom.height,
      // 不写 title — 反正 decorations=false 没标题栏
    });

    // 4. 等 overlay 创建完(throw 时直接抛错,主窗 caller 看到失败)
    await new Promise<void>((resolve, reject) => {
      const offCreated = win.once('tauri://created', () => {
        offCreated.then((u) => u()).catch(() => undefined);
        resolve();
      });
      const offError = win.once('tauri://error', (ev) => {
        offError.then((u) => u()).catch(() => undefined);
        reject(new Error(`open screenshot overlay failed: ${ev.payload}`));
      });
    });

    // 5. 监听 overlay 提交结果 — 只取第一次,然后 unlisten
    return new Promise<Blob>((resolve, reject) => {
      let settled = false;
      // 兜底:如果 overlay 因 crash / 用户关 X 等异常关掉,主窗这边会卡住等永
      // 远 — 多挂一道 destroyed 监听
      let unlistenDone: (() => void) | null = null;
      let unlistenDestroyed: (() => void) | null = null;

      const cleanup = () => {
        unlistenDone?.();
        unlistenDestroyed?.();
      };

      listen('chayuan://screenshot/done', async () => {
        if (settled) return;
        settled = true;
        cleanup();
        try {
          const bytes = await invoke<number[] | null>('chayuan_take_screenshot_result');
          if (bytes == null) {
            // 跟 web getDisplayMedia 用户拒绝同 error type,caller 一条 catch 通吃
            reject(new DOMException('用户取消了截图', 'AbortError'));
            return;
          }
          resolve(new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
        } catch (e) {
          reject(e instanceof Error ? e : new Error(String(e)));
        }
      }).then((fn) => { unlistenDone = fn; });

      // overlay 被外力关掉(用户 alt+f4 / 系统终止)— 当作取消处理
      win.onCloseRequested(() => {
        if (settled) return;
        // close request 来了,但 submit 事件可能还在路上,给个短延迟兜底
        setTimeout(() => {
          if (settled) return;
          settled = true;
          cleanup();
          reject(new DOMException('用户取消了截图', 'AbortError'));
        }, 150);
      }).then((fn) => { unlistenDestroyed = fn; });
    });
  },
};

const tauriUpdater: UpdaterApi = {
  async check() {
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const upd = await check();
      if (!upd) return { available: false };
      return { available: true, version: upd.version, notes: upd.body };
    } catch {
      return { available: false };
    }
  },
  async install() {
    const { check } = await import('@tauri-apps/plugin-updater');
    const upd = await check();
    if (upd) {
      await upd.downloadAndInstall();
      // 拼字符串绕过 Vite/Rollup 静态解析(@tauri-apps/plugin-process 未安装),
      // `as any` 会被 esbuild 擦掉,Rollup 仍按字面量去解析。
      const procName = '@tauri-apps' + '/plugin-process';
      const procMod = (await import(/* @vite-ignore */ procName)) as {
        relaunch: () => Promise<void>;
      };
      await procMod.relaunch();
    }
  },
};

const tauriWindow: WindowApi = {
  async minimize() {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    await getCurrentWindow().minimize();
  },
  async maximize() {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    const w = getCurrentWindow();
    (await w.isMaximized()) ? await w.unmaximize() : await w.maximize();
  },
  async close() {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    await getCurrentWindow().close();
  },
  async setDock(position) {
    // 把窗口贴到屏幕左/中/右半边(参考图"窗口位置")。
    // 用当前显示器尺寸计算目标矩形,Win/Linux/Mac 通用。
    const { getCurrentWindow, currentMonitor, LogicalPosition, LogicalSize } = await import(
      '@tauri-apps/api/window'
    );
    const win = getCurrentWindow();
    const mon = await currentMonitor();
    if (!mon) return;
    const { size, scaleFactor } = mon;
    const w = Math.floor(size.width / scaleFactor);
    const h = Math.floor(size.height / scaleFactor);
    if (position === 'left') {
      await win.setPosition(new LogicalPosition(0, 0));
      await win.setSize(new LogicalSize(Math.floor(w / 2), h));
    } else if (position === 'right') {
      await win.setPosition(new LogicalPosition(Math.floor(w / 2), 0));
      await win.setSize(new LogicalSize(Math.floor(w / 2), h));
    } else {
      const targetW = Math.min(1200, w);
      const targetH = Math.min(800, h);
      await win.setSize(new LogicalSize(targetW, targetH));
      await win.setPosition(
        new LogicalPosition(Math.floor((w - targetW) / 2), Math.floor((h - targetH) / 2)),
      );
    }
  },
  onThemeChange(cb) {
    let unsub: (() => void) | null = null;
    void (async () => {
      const { getCurrentWindow } = await import('@tauri-apps/api/window');
      const off = await getCurrentWindow().onThemeChanged(({ payload }: { payload: string }) =>
        cb(payload === 'dark'),
      );
      unsub = off;
    })();
    return () => unsub?.();
  },
  isDarkSystem() {
    return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  },
};

const tauriShell: ShellApi = {
  async openExternal(url) {
    const { open } = await import('@tauri-apps/plugin-shell');
    await open(url);
  },
  async openPath(path) {
    // 不能用 plugin-shell.open(file://...) —— 它按 URL 正则校验,
    // 文件夹路径过不了。走 Rust 端 chayuan_open_path,按平台分别调
    // explorer / open / xdg-open。
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('chayuan_open_path', { path });
  },
};

// ──────────────────────────────────────────────────────────────
// Dialog(plugin-dialog 包装,跨平台 confirm/prompt/message)
// ──────────────────────────────────────────────────────────────
//
// 为什么不用浏览器原生 ``window.confirm``:Tauri 2 webview 把它重定向到
// plugin-dialog IPC,需要 capabilities 显式授权,某些版本下还会抛 "Command
// not found"。这里直接用 plugin-dialog 的 ``ask`` / ``message`` JS API,绕开
// 那条不可靠的 webview 重定向链路;capabilities 已显式授权 ``dialog:allow-ask``
// / ``dialog:allow-message``,稳定可用。
//
// prompt:plugin-dialog 没原生 prompt(Rust 端没单行输入框 widget)。这里用
// ``window.prompt`` 兜底 — 它在 Tauri webview 是被允许的(WebView2 自带的输入框,
// 不走 IPC)。如果哪天兜底也失效,可以改成应用内 Modal + Input 组件。

const tauriDialog: DialogApi = {
  async confirm(message, opts) {
    const { ask } = await import('@tauri-apps/plugin-dialog');
    return ask(message, {
      title: opts?.title ?? '确认',
      kind: 'info',
      okLabel: opts?.okLabel ?? '确定',
      cancelLabel: opts?.cancelLabel ?? '取消',
    });
  },
  async prompt(message, opts) {
    // Tauri 没有原生 prompt;webview 自带的 window.prompt 在 Tauri 是 fallback 可用的
    // 反正 prompt 用得很少,不再投资写应用内 Modal
    if (typeof window !== 'undefined' && typeof window.prompt === 'function') {
      const r = window.prompt(message, opts?.defaultValue ?? '');
      return r;
    }
    return null;
  },
  async message(message, opts) {
    const { message: msg } = await import('@tauri-apps/plugin-dialog');
    await msg(message, {
      title: opts?.title ?? '提示',
      kind: opts?.kind ?? 'info',
    });
  },
};

// ──────────────────────────────────────────────────────────────
// Preview Window (multi-window for file preview)
// ──────────────────────────────────────────────────────────────

/**
 * Tauri 2 多窗口实现:用 WebviewWindow 创建一个新原生窗口,
 * 加载主进程同样的 URL（同 origin → 共享 localStorage / cookie / Stronghold）。
 *
 * label 唯一性:Tauri 不允许同 label 重开;若 label 已存在则 focus 旧窗口。
 * Standalone 检测:URL 路径以 /preview-window 开头(主路由也允许直接进)。
 */
const tauriPreview: PreviewWindowApi = {
  async openWindow(spec: PreviewWindowSpec): Promise<PreviewWindowHandle> {
    const label = spec.label ?? `preview-${Date.now()}`;
    const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');

    // 已有同 label 窗口 → 直接 focus
    const existing = await WebviewWindow.getByLabel(label).catch(() => null);
    if (existing) {
      await existing.setFocus().catch(() => undefined);
      return wrapHandle(existing);
    }

    const win = new WebviewWindow(label, {
      url: spec.path, // 相对路径,Tauri 自动拼 devUrl/frontendDist
      title: spec.title ?? '文件预览',
      width: spec.width ?? 960,
      height: spec.height ?? 720,
      minWidth: 480,
      minHeight: 360,
      resizable: true,
      decorations: true,
      focus: true,
    });

    // 等待创建完成;new 出来时 created 事件之前不能 setFocus
    await new Promise<void>((resolve, reject) => {
      const offCreated = win.once('tauri://created', () => {
        offCreated.then((u) => u()).catch(() => undefined);
        resolve();
      });
      const offError = win.once('tauri://error', (ev) => {
        offError.then((u) => u()).catch(() => undefined);
        reject(new Error(`open preview window failed: ${ev.payload}`));
      });
    });

    return wrapHandle(win);
  },
  isStandalone() {
    if (typeof window === 'undefined') return false;
    return window.location.pathname.startsWith('/preview-window');
  },
};

/** 把 WebviewWindow 实例包成跨平台 handle */
function wrapHandle(win: any): PreviewWindowHandle {
  const closedCbs = new Set<() => void>();
  let listenerAttached = false;
  // 懒挂 close listener,onClosed 第一次被调用时再挂(避免多余 IPC)
  const ensureListener = () => {
    if (listenerAttached) return;
    listenerAttached = true;
    void win.onCloseRequested?.(() => {
      // close requested 不一定真关;真关后回调一次
    });
    void win.listen?.('tauri://destroyed', () => {
      for (const cb of closedCbs) {
        try {
          cb();
        } catch {
          /* ignore */
        }
      }
      closedCbs.clear();
    });
  };
  return {
    async close() {
      try {
        await win.close();
      } catch {
        /* 已关或不可关,忽略 */
      }
    },
    async focus() {
      try {
        await win.setFocus();
      } catch {
        /* ignore */
      }
    },
    onClosed(cb: () => void): () => void {
      ensureListener();
      closedCbs.add(cb);
      return () => closedCbs.delete(cb);
    },
  };
}

// ──────────────────────────────────────────────────────────────
// 数据目录管理(Phase 1 单机版首启动向导)
// ──────────────────────────────────────────────────────────────

/** 后端 ``data_dir.rs`` 返回的字段名是 snake_case;前端映射为 camelCase。 */
interface RawDataDirState {
  configured: boolean;
  path: string;
  default_path: string;
  config_file: string;
  has_existing_data: boolean;
  portable_candidate: string | null;
}

function normalizeDataDirState(raw: RawDataDirState): DataDirState {
  return {
    configured: !!raw.configured,
    path: raw.path ?? '',
    defaultPath: raw.default_path ?? '',
    configFile: raw.config_file ?? '',
    hasExistingData: !!raw.has_existing_data,
    portableCandidate: raw.portable_candidate ?? null,
  };
}

const tauriDataDir: DataDirApi = {
  async state() {
    const { invoke } = await import('@tauri-apps/api/core');
    const raw = await invoke<RawDataDirState>('chayuan_data_dir_state');
    return normalizeDataDirState(raw);
  },
  async defaultPath() {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke<string>('chayuan_data_dir_default');
  },
  async checkExisting(path: string) {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke<boolean>('chayuan_data_dir_check_existing', { path });
  },
  async pickDirectory(opts?: PickDirectoryOptions) {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const picked = await open({
      directory: true,
      multiple: false,
      title: opts?.title ?? '选择察元 AI 数据目录',
      ...(opts?.defaultPath ? { defaultPath: opts.defaultPath } : {}),
    });
    if (!picked) return null;
    if (Array.isArray(picked)) return picked[0] ?? null;
    return picked;
  },
  async set(path: string) {
    const { invoke } = await import('@tauri-apps/api/core');
    const raw = await invoke<RawDataDirState>('chayuan_data_dir_set', { path });
    return normalizeDataDirState(raw);
  },
  async reset() {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke<void>('chayuan_data_dir_reset');
  },
};

// ──────────────────────────────────────────────────────────────
// Sidecar(Phase 3:chayuan-server 子进程启停)
// ──────────────────────────────────────────────────────────────

interface RawSidecarStatus {
  state: SidecarStateName;
  port: number;
  base_url: string;
  restarts: number;
  error: string | null;
  last_log_line: string | null;
  data_dir: string;
}

function normalizeSidecarStatus(raw: RawSidecarStatus): SidecarStatus {
  return {
    state: raw.state,
    port: raw.port,
    baseUrl: raw.base_url,
    restarts: raw.restarts,
    error: raw.error,
    lastLogLine: raw.last_log_line,
    dataDir: raw.data_dir,
  };
}

const tauriSidecar: SidecarApi = {
  async status() {
    const { invoke } = await import('@tauri-apps/api/core');
    const raw = await invoke<RawSidecarStatus>('chayuan_sidecar_status');
    return normalizeSidecarStatus(raw);
  },
  async start(args) {
    const { invoke } = await import('@tauri-apps/api/core');
    const payload: { data_dir: string; vendor_platform?: string } = {
      data_dir: args.dataDir,
    };
    if (args.vendorPlatform && args.vendorPlatform.trim()) {
      payload.vendor_platform = args.vendorPlatform.trim();
    }
    const raw = await invoke<RawSidecarStatus>('chayuan_sidecar_start', {
      args: payload,
    });
    return normalizeSidecarStatus(raw);
  },
  async kill() {
    const { invoke } = await import('@tauri-apps/api/core');
    const raw = await invoke<RawSidecarStatus>('chayuan_sidecar_kill');
    return normalizeSidecarStatus(raw);
  },
  async subscribe(handlers) {
    const { listen } = await import('@tauri-apps/api/event');
    const unlistens: Array<() => void> = [];
    if (handlers.onState) {
      const u = await listen<SidecarStateName>('cy:sidecar-state', (e) => {
        handlers.onState?.(e.payload);
      });
      unlistens.push(u);
    }
    if (handlers.onLog) {
      const u = await listen<string>('cy:sidecar-log', (e) => {
        handlers.onLog?.(e.payload);
      });
      unlistens.push(u);
    }
    return () => {
      for (const u of unlistens) {
        try {
          u();
        } catch {
          /* noop */
        }
      }
    };
  },
};

// ──────────────────────────────────────────────────────────────
// Bootstrap
// ──────────────────────────────────────────────────────────────

export async function createTauriPlatform(env: {
  appName: string;
  appVersion: string;
  defaultApiBase: string;
}): Promise<Platform> {
  // 拉一次 OS locale;失败软降级 navigator.language。
  // Tauri WebView2 的 navigator.language 在 Windows 下经常恒返回 en-US,
  // 必须走 plugin-os 才能拿到真实系统语言(如 zh-CN)。
  let systemLocale: string | undefined;
  try {
    const osMod = (await import('@tauri-apps/plugin-os')) as {
      locale?: () => Promise<string | null>;
    };
    if (osMod.locale) {
      const v = await osMod.locale();
      if (v) systemLocale = v;
    }
  } catch {
    /* plugin-os 不在 / 调用失败:留空,上层兜底 */
  }
  if (!systemLocale && typeof navigator !== 'undefined') {
    systemLocale = navigator.language;
  }

  const runtime: RuntimeInfo = {
    appName: env.appName,
    appVersion: env.appVersion,
    release: `desktop@${env.appVersion}`,
    defaultApiBase: env.defaultApiBase,
    systemLocale,
  };

  return {
    kind: 'desktop',
    runtime,
    secure: tauriSecure,
    db: tauriDb,
    fs: tauriFs,
    net: tauriNet,
    clipboard: tauriClipboard,
    notify: tauriNotify,
    shortcut: tauriShortcut,
    tray: tauriTray,
    capture: tauriCapture,
    updater: tauriUpdater,
    window: tauriWindow,
    shell: tauriShell,
    dialog: tauriDialog,
    preview: tauriPreview,
    dataDir: tauriDataDir,
    sidecar: tauriSidecar,
  };
}
