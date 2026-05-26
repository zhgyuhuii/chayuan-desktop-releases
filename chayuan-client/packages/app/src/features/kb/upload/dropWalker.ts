/**
 * DataTransfer → File[] 递归 walker。
 *
 * 浏览器拖拽 API 的两条腿:
 *   1) `e.dataTransfer.files` — 仅顶层文件;拖文件夹进来,这里是空的。
 *   2) `e.dataTransfer.items[i].webkitGetAsEntry()` — 拿到 FileSystemEntry,
 *      可以判断 isFile / isDirectory + 递归 readEntries。
 *
 * 我们走第二条腿,顺手把"相对所拖根的路径"塞到 file.webkitRelativePath
 * (Object.defineProperty 注入,与 `<input webkitdirectory>` 行为对齐),
 * 上层 uploadDocs 直接用这个字段作 multipart filename,后端 KnowledgeFile
 * 自带 Path(filename).as_posix() 把嵌套路径落到 KB/content/<sub>/<file>。
 *
 * 兼容性:Chromium / Safari / Firefox 都支持 webkitGetAsEntry;Tauri 内置
 * webview 同 Chromium。完全降级路径是 `e.dataTransfer.files`(只能扁平)。
 */

interface FileSystemEntryShim {
  isFile?: boolean;
  isDirectory?: boolean;
  name?: string;
  fullPath?: string;
  file?(cb: (f: File) => void, err?: (e: unknown) => void): void;
  createReader?(): {
    readEntries(cb: (entries: FileSystemEntryShim[]) => void, err?: (e: unknown) => void): void;
  };
}

interface ItemWithEntry {
  kind: string;
  webkitGetAsEntry?(): FileSystemEntryShim | null;
  getAsFile?(): File | null;
}

/** 把 webkitRelativePath 注入到一个 File 上,与浏览器 input webkitdirectory 行为对齐 */
function tagPath(file: File, relPath: string): File {
  try {
    Object.defineProperty(file, 'webkitRelativePath', {
      value: relPath,
      configurable: false,
      enumerable: true,
      writable: false,
    });
  } catch {
    /* 个别旧浏览器不让重定义,fallback:用 File.name 包含路径 */
    return new File([file], relPath, { type: file.type, lastModified: file.lastModified });
  }
  return file;
}

function readEntriesPaged(
  reader: { readEntries(cb: (e: FileSystemEntryShim[]) => void, err?: (e: unknown) => void): void },
): Promise<FileSystemEntryShim[]> {
  // readEntries 一页最多 ~100 条,要循环直到空才算遍历完
  return new Promise((resolve, reject) => {
    const all: FileSystemEntryShim[] = [];
    const drain = () => {
      reader.readEntries((entries) => {
        if (!entries.length) return resolve(all);
        all.push(...entries);
        drain();
      }, reject);
    };
    drain();
  });
}

async function walkEntry(entry: FileSystemEntryShim, prefix: string): Promise<File[]> {
  if (!entry) return [];
  // 跳过 dotfiles / 垃圾目录,与 Tauri pickDirectory 同口径
  const SKIP = /^(\.git|node_modules|\.DS_Store|__MACOSX)$/i;
  if (entry.name && (entry.name.startsWith('.') || SKIP.test(entry.name))) return [];
  if (entry.isFile && entry.file) {
    const file: File = await new Promise((resolve, reject) => {
      entry.file!((f) => resolve(f), reject);
    });
    const rel = prefix ? `${prefix}/${entry.name || file.name}` : (entry.name || file.name);
    return [tagPath(file, rel)];
  }
  if (entry.isDirectory && entry.createReader) {
    const reader = entry.createReader();
    const sub = await readEntriesPaged(reader);
    const nextPrefix = prefix ? `${prefix}/${entry.name}` : (entry.name || '');
    const out: File[] = [];
    for (const e of sub) out.push(...await walkEntry(e, nextPrefix));
    return out;
  }
  return [];
}

/**
 * 把 DataTransfer 里的所有可拖项展开成扁平 File[];文件夹会递归进去。
 * 文件夹里的文件 webkitRelativePath = '<rootDirName>/<sub>/<name>'。
 */
export async function expandDataTransfer(dt: DataTransfer | null): Promise<File[]> {
  if (!dt) return [];
  const items = (dt.items as unknown as ArrayLike<ItemWithEntry>) || null;
  // 优先 items + entry(支持文件夹);items 不可用时降级 dt.files(扁平)
  if (items && items.length > 0 && typeof items[0]?.webkitGetAsEntry === 'function') {
    const out: File[] = [];
    const seen = new Set<string>();  // 同名同大小去重(浏览器某些版本会双投递)
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (!it || it.kind !== 'file') continue;
      const entry = it.webkitGetAsEntry?.();
      if (entry) {
        const files = await walkEntry(entry, '');
        for (const f of files) {
          const key = `${(f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name}|${f.size}`;
          if (seen.has(key)) continue;
          seen.add(key);
          out.push(f);
        }
      } else {
        const f = it.getAsFile?.();
        if (f) out.push(f);
      }
    }
    return out;
  }
  // 全降级:只能拿到扁平 files
  return Array.from(dt.files || []);
}
