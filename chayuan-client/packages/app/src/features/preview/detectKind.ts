/**
 * 文件 → PreviewKind 路由。
 *
 * 优先级:扩展名 > mime;两者都拿不到 → 'unsupported'。
 *
 * 命中规则尽量保守:不确定就走 fallback,而不是错误尝试解析后 crash。
 */

import type { PreviewFile, PreviewKind } from './types';

const EXT_MAP: Record<string, PreviewKind> = {
  // image
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  webp: 'image',
  bmp: 'image',
  avif: 'image',
  svg: 'image',
  ico: 'image',
  tiff: 'image',
  tif: 'image',
  // video
  mp4: 'video',
  webm: 'video',
  mov: 'video',
  mkv: 'video',
  m4v: 'video',
  ogv: 'video',
  // audio
  mp3: 'audio',
  wav: 'audio',
  flac: 'audio',
  ogg: 'audio',
  m4a: 'audio',
  aac: 'audio',
  // pdf
  pdf: 'pdf',
  // markdown
  md: 'markdown',
  markdown: 'markdown',
  mdx: 'markdown',
  // text-ish
  txt: 'text',
  log: 'text',
  json: 'text',
  yaml: 'text',
  yml: 'text',
  xml: 'text',
  csv: 'text',
  tsv: 'text',
  ini: 'text',
  conf: 'text',
  toml: 'text',
  py: 'text',
  js: 'text',
  ts: 'text',
  tsx: 'text',
  jsx: 'text',
  sh: 'text',
  go: 'text',
  rs: 'text',
  java: 'text',
  kt: 'text',
  swift: 'text',
  php: 'text',
  c: 'text',
  h: 'text',
  cpp: 'text',
  hpp: 'text',
  cs: 'text',
  sql: 'text',
  html: 'text',
  htm: 'text',
  css: 'text',
  scss: 'text',
  less: 'text',
  // office
  docx: 'docx',
  xlsx: 'xlsx',
  xls: 'xlsx',
  pptx: 'pptx',
};

const MIME_MAP: Array<[RegExp, PreviewKind]> = [
  [/^image\//i, 'image'],
  [/^video\//i, 'video'],
  [/^audio\//i, 'audio'],
  [/^application\/pdf$/i, 'pdf'],
  [/^text\/markdown$/i, 'markdown'],
  [/^text\//i, 'text'],
  [/openxmlformats-officedocument\.wordprocessingml/i, 'docx'],
  [/openxmlformats-officedocument\.spreadsheetml/i, 'xlsx'],
  [/openxmlformats-officedocument\.presentationml/i, 'pptx'],
];

export function getExt(name: string): string {
  const m = /\.([A-Za-z0-9]+)$/.exec(name);
  return m ? m[1]!.toLowerCase() : '';
}

export function detectKind(file: PreviewFile): PreviewKind {
  const ext = getExt(file.fileName);
  if (ext && EXT_MAP[ext]) return EXT_MAP[ext]!;
  if (file.mime) {
    for (const [re, kind] of MIME_MAP) {
      if (re.test(file.mime)) return kind;
    }
  }
  return 'unsupported';
}

/** UI 上展示的扩展 chip 文案 */
export function extLabel(file: PreviewFile): string {
  const ext = getExt(file.fileName);
  return ext ? ext.toUpperCase() : '?';
}

/** 字节数 → 人类可读 */
export function humanSize(bytes?: number): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
