/**
 * 94-4:按文件扩展名 / MIME 把文件列表分成"文档" + "图像" + "其它"。
 *
 * 用于集合上传时,前端把混合文件自动路由到子 doc-KB / 子 image-source。
 */

const DOC_EXTS = new Set([
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'txt', 'md', 'markdown', 'csv', 'tsv', 'html', 'htm', 'json',
]);

const IMG_EXTS = new Set([
  'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff', 'tif', 'heic',
]);

export type FileKind = 'document' | 'image' | 'other';

export function fileKindFor(file: File): FileKind {
  // 优先看 mime,再看扩展名(部分浏览器 file.type 是空字符串)
  const t = (file.type || '').toLowerCase();
  if (t.startsWith('image/')) return 'image';
  if (t === 'application/pdf' || t.startsWith('application/vnd.')
      || t.startsWith('text/') || t === 'application/json') {
    return 'document';
  }
  // fallback: 扩展名
  const name = file.name.toLowerCase();
  const m = /\.([a-z0-9]+)$/.exec(name);
  const ext = m?.[1];
  if (ext) {
    if (IMG_EXTS.has(ext)) return 'image';
    if (DOC_EXTS.has(ext)) return 'document';
  }
  return 'other';
}

export interface SplitResult {
  documents: File[];
  images: File[];
  other: File[];
}

export function splitByMime(files: File[] | FileList): SplitResult {
  const arr = Array.from(files);
  const out: SplitResult = { documents: [], images: [], other: [] };
  for (const f of arr) {
    const k = fileKindFor(f);
    if (k === 'document') out.documents.push(f);
    else if (k === 'image') out.images.push(f);
    else out.other.push(f);
  }
  return out;
}
