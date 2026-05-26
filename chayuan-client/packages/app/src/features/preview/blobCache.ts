/**
 * 跨 renderer 的 Blob LRU 缓存。
 *
 * 用途:
 *   - 同一文件可能被 PdfRenderer 用 url(直接喂 iframe),又被 DocxRenderer 用 ArrayBuffer。
 *   - 用户先 dock 看,后切到独立窗口 — 不希望重新拉一遍。
 *
 * 上限:8 项 / 256 MB(可调,实测大文件场景需要更宽松)
 *
 * 注意:Blob 在 Chromium 里其实是磁盘 backed 的,不会立刻吃 RAM,
 * 但 objectURL 必须显式 revoke,否则 GC 不会主动清。
 */

const MAX_ITEMS = 8;
const MAX_BYTES = 256 * 1024 * 1024;

interface Entry {
  key: string;
  blob: Blob;
  size: number;
  /** 该 entry 对应的 objectURL(惰性创建) */
  objectURL: string | null;
  lastUsed: number;
}

const store = new Map<string, Entry>();
let totalBytes = 0;

function touch(e: Entry) {
  e.lastUsed = Date.now();
  // 移到末尾(LRU)
  store.delete(e.key);
  store.set(e.key, e);
}

function evictIfNeeded() {
  while (store.size > MAX_ITEMS || totalBytes > MAX_BYTES) {
    const oldest = store.values().next().value as Entry | undefined;
    if (!oldest) break;
    if (oldest.objectURL) URL.revokeObjectURL(oldest.objectURL);
    totalBytes -= oldest.size;
    store.delete(oldest.key);
  }
}

export function getCachedBlob(key: string): Blob | null {
  const e = store.get(key);
  if (!e) return null;
  touch(e);
  return e.blob;
}

export function putCachedBlob(key: string, blob: Blob): void {
  const existing = store.get(key);
  if (existing) {
    if (existing.objectURL) URL.revokeObjectURL(existing.objectURL);
    totalBytes -= existing.size;
    store.delete(key);
  }
  const entry: Entry = {
    key,
    blob,
    size: blob.size,
    objectURL: null,
    lastUsed: Date.now(),
  };
  store.set(key, entry);
  totalBytes += entry.size;
  evictIfNeeded();
}

/** 拿(或惰性创建) blob 的 objectURL;同一 entry 只创建一次 */
export function getOrCreateObjectURL(key: string): string | null {
  const e = store.get(key);
  if (!e) return null;
  touch(e);
  if (!e.objectURL) e.objectURL = URL.createObjectURL(e.blob);
  return e.objectURL;
}

export function dropCachedBlob(key: string): void {
  const e = store.get(key);
  if (!e) return;
  if (e.objectURL) URL.revokeObjectURL(e.objectURL);
  totalBytes -= e.size;
  store.delete(key);
}

export function clearBlobCache(): void {
  for (const e of store.values()) {
    if (e.objectURL) URL.revokeObjectURL(e.objectURL);
  }
  store.clear();
  totalBytes = 0;
}
