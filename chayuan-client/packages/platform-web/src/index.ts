/**
 * Web 平台实现。
 *
 * 设计要点：
 * - SecureStore：浏览器没有真"安全存储"。生产模式建议把鉴权切到 httpOnly cookie，
 *   此处用 localStorage 让用户刷新 / 关浏览器后仍保持登录态(对应"记住账号" UX)。
 *   sessionStorage 因关 tab 即丢,体验差。SSR/隐私模式拿不到 storage 时静默 noop。
 * - Database：Dexie 开 IDB，schema 与桌面版 SQLite 等价表结构。
 * - net.fetch / net.sse：直通 window.fetch；SSE 同样依赖 ReadableStream。
 * - 没有 shortcut / tray / capture / updater；UI 必须 capability check。
 */

import type {
  CaptureApi,
  ClipboardApi,
  DbValue,
  FsApi,
  Database as IDatabase,
  NetApi,
  NotifyApi,
  PickOptions,
  Platform,
  PreviewWindowApi,
  PreviewWindowHandle,
  PreviewWindowSpec,
  RuntimeInfo,
  SecureStore,
  DialogApi,
  ShellApi,
  WindowApi,
} from '@chayuan/platform-shared';
import { isAuthPersistent } from '@chayuan/platform-shared';
import Dexie, { type Table } from 'dexie';

// ──────────────────────────────────────────────────────────────
// SecureStore (localStorage + 加密占位)
// ──────────────────────────────────────────────────────────────

const SECURE_PREFIX = 'cy.secure.';

/** localStorage / sessionStorage 在 SSR / 隐私模式 / quota 满 时会抛,统一 try/catch 静默 */
function lsGet(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(SECURE_PREFIX + key) ?? null;
  } catch {
    return null;
  }
}
function lsSet(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(SECURE_PREFIX + key, value);
  } catch {
    /* quota / 隐私模式;静默 */
  }
}
function lsDel(key: string): void {
  try {
    globalThis.localStorage?.removeItem(SECURE_PREFIX + key);
  } catch {
    /* 静默 */
  }
}
function ssGet(key: string): string | null {
  try {
    return globalThis.sessionStorage?.getItem(SECURE_PREFIX + key) ?? null;
  } catch {
    return null;
  }
}
function ssSet(key: string, value: string): void {
  try {
    globalThis.sessionStorage?.setItem(SECURE_PREFIX + key, value);
  } catch {
    /* 静默 */
  }
}
function ssDel(key: string): void {
  try {
    globalThis.sessionStorage?.removeItem(SECURE_PREFIX + key);
  } catch {
    /* 静默 */
  }
}

/**
 * 由「记住我」开关决定 token 落 local 还是 session storage。
 * 读时两边都查(local 优先) — 这样切换偏好不会让旧 token 残留导致幻觉:
 * 写时另一边的同名 key 同时清掉。
 */
const webSecure: SecureStore = {
  async get(key) {
    return lsGet(key) ?? ssGet(key);
  },
  async set(key, value) {
    if (isAuthPersistent()) {
      lsSet(key, value);
      ssDel(key);
    } else {
      ssSet(key, value);
      lsDel(key);
    }
  },
  async del(key) {
    lsDel(key);
    ssDel(key);
  },
};

// ──────────────────────────────────────────────────────────────
// Database（Dexie + 自实现 SQL 子集）
//
// 我们不需要"全 SQL"，业务层只用：
//   - INSERT INTO conversations / messages / lf_outbox
//   - SELECT ... WHERE conv_id = ?
//   - UPDATE / DELETE by id
// 所以 Dexie 表 + 一个最小 SQL 解析器足够。
//
// 复杂查询（FTS / JOIN）web 端要么走 minisearch（前端内存索引）
// 要么直接调更高层的 db.* 方法（业务层不要用 raw SQL）。
// ──────────────────────────────────────────────────────────────

interface ConversationRow {
  id: string;
  remote_id?: string | null;
  title: string;
  model: string;
  mode: string;
  meta?: string | null;
  created_at: number;
  updated_at: number;
}

interface MessageRow {
  id: string;
  conv_id: string;
  role: string;
  parts: string;
  trace_id?: string | null;
  created_at: number;
}

interface OutboxRow {
  id?: number;
  payload: string;
  attempts: number;
  created_at: number;
}

class ChayuanDb extends Dexie {
  conversations!: Table<ConversationRow, string>;
  messages!: Table<MessageRow, string>;
  lf_outbox!: Table<OutboxRow, number>;

  constructor() {
    super('chayuan');
    this.version(1).stores({
      conversations: 'id, remote_id, updated_at',
      messages: 'id, conv_id, [conv_id+created_at]',
      lf_outbox: '++id, created_at',
    });
  }
}

let dbInstance: ChayuanDb | null = null;
function ensureDb(): ChayuanDb {
  if (!dbInstance) dbInstance = new ChayuanDb();
  return dbInstance;
}

const webDb: IDatabase = {
  async exec(sql, params) {
    await runSql(sql, params, false);
  },
  async query<T>(sql: string, params?: DbValue[]): Promise<T[]> {
    return (await runSql(sql, params, true)) as T[];
  },
};

/**
 * 极简 SQL 适配器。仅识别业务用到的几条形态；
 * 无法识别的 SQL 抛错，提示业务用 db.conversations / db.messages 等高层 API。
 */
async function runSql(sql: string, params: DbValue[] = [], isQuery: boolean): Promise<unknown[]> {
  const db = ensureDb();
  const norm = sql.replace(/\s+/g, ' ').trim().toUpperCase();

  // CREATE TABLE / INDEX / VIRTUAL TABLE → noop（schema 由 Dexie.version 管理）
  if (norm.startsWith('CREATE ')) return [];

  // INSERT INTO conversations (...) VALUES (?, ...)
  let m: RegExpMatchArray | null;
  if (
    (m = sql.match(
      /^\s*INSERT\s+(?:OR\s+REPLACE\s+)?INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)/i,
    ))
  ) {
    const [, table, cols] = m;
    if (!table) throw new Error('table missing');
    const colList = cols!.split(',').map((c) => c.trim());
    const row: Record<string, DbValue | undefined> = {};
    colList.forEach((c, i) => (row[c] = params[i]));
    // SQL → IDB 适配器:动态构造 row 对象,运行时由表 schema 校验。
    if (table === 'conversations') await db.conversations.put(row as unknown as ConversationRow);
    else if (table === 'messages') await db.messages.put(row as unknown as MessageRow);
    else if (table === 'lf_outbox') await db.lf_outbox.add(row as unknown as OutboxRow);
    else throw new Error(`unknown table: ${table}`);
    return [];
  }

  // SELECT * FROM messages WHERE conv_id = ? ORDER BY created_at
  if (
    (m = sql.match(
      /^\s*SELECT\s+\*\s+FROM\s+(\w+)(?:\s+WHERE\s+(\w+)\s*=\s*\?)?(?:\s+ORDER\s+BY\s+(\w+)(\s+DESC)?)?(?:\s+LIMIT\s+(\d+))?/i,
    ))
  ) {
    const [, table, whereCol, orderCol, desc, limit] = m;
    let coll = (db as any)[table!]?.toCollection?.();
    if (!coll) throw new Error(`unknown table: ${table}`);
    if (whereCol) coll = (db as any)[table!].where(whereCol).equals(params[0]);
    let arr = await coll.toArray();
    if (orderCol)
      arr = arr.sort((a: any, b: any) =>
        desc ? b[orderCol] - a[orderCol] : a[orderCol] - b[orderCol],
      );
    if (limit) arr = arr.slice(0, Number.parseInt(limit, 10));
    return isQuery ? arr : [];
  }

  // DELETE FROM xx WHERE id = ?
  if ((m = sql.match(/^\s*DELETE\s+FROM\s+(\w+)\s+WHERE\s+(\w+)\s*=\s*\?/i))) {
    const [, table, col] = m;
    await (db as any)[table!].where(col!).equals(params[0]).delete();
    return [];
  }

  throw new Error(`[platform-web db] 不支持的 SQL：${sql.slice(0, 80)}…  请改用业务层 store API`);
}

// ──────────────────────────────────────────────────────────────
// Fs（<input type=file> + Drag & Drop + File System Access API）
// ──────────────────────────────────────────────────────────────

const webFs: FsApi = {
  async pickFiles(opts?: PickOptions): Promise<File[]> {
    return new Promise<File[]>((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = !!opts?.multiple;
      if (opts?.accept) {
        input.accept = Array.isArray(opts.accept) ? opts.accept.join(',') : opts.accept;
      }
      input.onchange = () => resolve(Array.from(input.files ?? []));
      input.oncancel = () => resolve([]);
      input.click();
    });
  },

  async pickDirectory(): Promise<File[]> {
    // 浏览器路径 = `<input webkitdirectory>`;chosen 文件自带
    // file.webkitRelativePath = '<root>/<sub>/<name>',业务侧直接用即可。
    return new Promise<File[]>((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      // webkitdirectory 是非标准但 Chromium / Safari / Firefox 都支持
      (input as HTMLInputElement & { webkitdirectory: boolean }).webkitdirectory = true;
      input.multiple = true;
      input.onchange = () => resolve(Array.from(input.files ?? []));
      input.oncancel = () => resolve([]);
      input.click();
    });
  },

  async saveText(name, content) {
    // 优先 File System Access API（Chromium 系）；fallback 走下载
    const w = window as unknown as { showSaveFilePicker?: (opts: any) => Promise<any> };
    if (w.showSaveFilePicker) {
      try {
        const handle = await w.showSaveFilePicker({ suggestedName: name });
        const stream = await handle.createWritable();
        await stream.write(content);
        await stream.close();
        return;
      } catch {
        /* fallthrough to download */
      }
    }
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  },

  async readDropped(event: DragEvent): Promise<File[]> {
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

// ──────────────────────────────────────────────────────────────
// Net / Clipboard / Notify / Window / Shell
// ──────────────────────────────────────────────────────────────

const webNet: NetApi = {
  fetch(input, init) {
    return globalThis.fetch(input as any, init);
  },
  sse(input, init) {
    return globalThis.fetch(input.toString(), init);
  },
};

const webClipboard: ClipboardApi = {
  async readText() {
    return (await navigator.clipboard.readText()) ?? '';
  },
  async writeText(text) {
    await navigator.clipboard.writeText(text);
  },
  async readImage() {
    try {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        for (const t of it.types) {
          if (t.startsWith('image/')) return await it.getType(t);
        }
      }
    } catch {
      /* unsupported */
    }
    return null;
  },
};

const webNotify: NotifyApi = {
  async requestPermission() {
    if (!('Notification' in window)) return 'denied';
    return await Notification.requestPermission();
  },
  async show(title, body, opts) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') {
      const r = await Notification.requestPermission();
      if (r !== 'granted') return;
    }
    new Notification(title, { body, icon: opts?.icon });
  },
};

const webWindow: WindowApi = {
  onThemeChange(cb) {
    const mq = globalThis.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return () => undefined;
    const handler = (e: MediaQueryListEvent) => cb(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  },
  isDarkSystem() {
    return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  },
};

const webShell: ShellApi = {
  async openExternal(url) {
    window.open(url, '_blank', 'noopener,noreferrer');
  },
  async openPath(path) {
    // Web 端没有"打开本地文件管理器"的能力,noop + 提示。
    console.warn('[platform-web] openPath 在 Web 端不可用:', path);
  },
};

// Web 端原生 confirm/prompt/alert 在浏览器里完全可用,直接走 — 不需要弹 plugin。
const webDialog: DialogApi = {
  async confirm(message) {
    return window.confirm(message);
  },
  async prompt(message, opts) {
    return window.prompt(message, opts?.defaultValue ?? '');
  },
  async message(message) {
    window.alert(message);
  },
};

// ──────────────────────────────────────────────────────────────
// Preview Window (web 用 window.open 弹窗;同源共享 localStorage)
// ──────────────────────────────────────────────────────────────

const webPreview: PreviewWindowApi = {
  async openWindow(spec: PreviewWindowSpec): Promise<PreviewWindowHandle> {
    const w = spec.width ?? 960;
    const h = spec.height ?? 720;
    const features = `popup=yes,width=${w},height=${h},resizable=yes,scrollbars=yes`;
    // 用 label 作为 target;同 label 二次打开会复用同一个 popup
    const target = spec.label ? `cy-preview-${spec.label}` : '_blank';
    const popup = window.open(spec.path, target, features);
    if (!popup) {
      throw new Error('浏览器拦截了弹窗;请允许弹窗后再试。');
    }
    if (spec.title) {
      try {
        popup.document.title = spec.title;
      } catch {
        /* cross-origin: 忽略 */
      }
    }
    const closedCbs = new Set<() => void>();
    let timer: number | null = null;
    const checkClosed = () => {
      if (popup.closed) {
        for (const cb of closedCbs) {
          try {
            cb();
          } catch {
            /* ignore */
          }
        }
        closedCbs.clear();
        if (timer != null) {
          clearInterval(timer);
          timer = null;
        }
      }
    };
    return {
      async close() {
        try {
          popup.close();
        } catch {
          /* ignore */
        }
      },
      async focus() {
        try {
          popup.focus();
        } catch {
          /* ignore */
        }
      },
      onClosed(cb: () => void): () => void {
        closedCbs.add(cb);
        if (timer == null) timer = window.setInterval(checkClosed, 500);
        return () => {
          closedCbs.delete(cb);
          if (closedCbs.size === 0 && timer != null) {
            clearInterval(timer);
            timer = null;
          }
        };
      },
    };
  },
  isStandalone() {
    if (typeof window === 'undefined') return false;
    return window.location.pathname.startsWith('/preview-window');
  },
};

// ──────────────────────────────────────────────────────────────
// Capture(屏幕截图)— Web 端兜底:getDisplayMedia 选屏 → canvas → toBlob
// ──────────────────────────────────────────────────────────────
//
// Tauri 端是直接调 Rust xcap 一次成图,毫无 UX 摩擦;Web 浏览器没有"静默截
// 屏"能力,只能借 getDisplayMedia 走标准的"屏幕共享"链路:
//   1. 用户点截图按钮 → 浏览器弹原生选屏对话框(整屏 / 单窗口 / 单 tab)
//   2. 拿到 MediaStream → 抽 video track → 设到隐藏 <video> 上 → 取一帧
//   3. drawImage 进 canvas → canvas.toBlob 拿 PNG
//   4. 立刻 stop track,不留权限痕迹
//
// 限制:用户每次都要在弹框里手选屏 / 窗口,无法记忆;并且 https 才有 API。
// 桌面版用户走 Tauri 路径无感,这里仅作 web 兜底。

const webCapture: CaptureApi = {
  async screenshot(): Promise<Blob> {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getDisplayMedia) {
      throw new Error('当前浏览器不支持 getDisplayMedia;请用桌面版或升级浏览器');
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({
      // preferCurrentTab=false:让用户自选,默认提示"整屏 / 应用窗口 / 当前 Tab"
      video: { displaySurface: 'monitor' } as MediaTrackConstraints,
      audio: false,
    });
    try {
      const track = stream.getVideoTracks()[0];
      if (!track) throw new Error('未拿到视频轨');
      // 用 <video> 串接 stream 取一帧 —— 比 ImageCapture 兼容性更好
      // (Safari + Firefox 没全量支持 ImageCapture)
      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      // 等 metadata + 真正有帧 —— 直接 drawImage 可能拿到空帧
      if (video.readyState < 2) {
        await new Promise<void>((resolve) => {
          video.onloadeddata = () => resolve();
        });
      }
      const w = video.videoWidth;
      const h = video.videoHeight;
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('canvas 2d 上下文初始化失败');
      ctx.drawImage(video, 0, 0, w, h);
      video.srcObject = null;
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), 'image/png'),
      );
      if (!blob) throw new Error('canvas.toBlob 返回空');
      return blob;
    } finally {
      // 必须立刻 stop,否则 Chrome / Edge 顶部会一直显示"正在共享屏幕"
      for (const t of stream.getTracks()) t.stop();
    }
  },
};

// ──────────────────────────────────────────────────────────────
// Bootstrap
// ──────────────────────────────────────────────────────────────

export function createWebPlatform(env: {
  appName: string;
  appVersion: string;
  defaultApiBase: string;
}): Platform {
  const runtime: RuntimeInfo = {
    appName: env.appName,
    appVersion: env.appVersion,
    release: `web@${env.appVersion}`,
    defaultApiBase: env.defaultApiBase,
    systemLocale: typeof navigator !== 'undefined' ? navigator.language : undefined,
  };
  return {
    kind: 'web',
    runtime,
    secure: webSecure,
    db: webDb,
    fs: webFs,
    net: webNet,
    clipboard: webClipboard,
    notify: webNotify,
    window: webWindow,
    shell: webShell,
    dialog: webDialog,
    preview: webPreview,
    capture: webCapture,
    // 不提供 shortcut / tray / updater；UI 必须 capability check
  };
}
