/**
 * 纯文本/代码预览:fetch 文本 → shiki 高亮(按扩展名探测语言)。
 *
 * 性能:
 *   - 大文件(>500 KB)不走 shiki,直接 <pre>(高亮代价高)
 *   - shiki getHighlighter 走 dynamic import,首屏不引入 wasm
 */

import * as React from 'react';
import { getExt } from '../detectKind';
import type { RendererProps } from '../types';
import { ErrorRenderer } from './ErrorRenderer';
import { LoadingRenderer } from './LoadingRenderer';

const LANG_MAP: Record<string, string> = {
  js: 'javascript',
  jsx: 'jsx',
  ts: 'typescript',
  tsx: 'tsx',
  py: 'python',
  rb: 'ruby',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  swift: 'swift',
  php: 'php',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  hpp: 'cpp',
  cs: 'csharp',
  sql: 'sql',
  sh: 'bash',
  html: 'html',
  htm: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  xml: 'xml',
  toml: 'toml',
  ini: 'ini',
  conf: 'ini',
  md: 'markdown',
  mdx: 'mdx',
  csv: 'csv',
  tsv: 'tsv',
};

const HUGE_BYTES = 500 * 1024;

export const TextRenderer: React.FC<RendererProps> = ({ file, fetchBlob, onReady, onError }) => {
  const [text, setText] = React.useState<string | null>(null);
  const [html, setHtml] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<Error | null>(null);

  React.useEffect(() => {
    let live = true;
    const ac = new AbortController();
    setText(null);
    setHtml(null);
    setErr(null);
    void (async () => {
      try {
        const blob = await fetchBlob(ac.signal);
        const t = await blob.text();
        if (!live) return;
        setText(t);
        // 大文件跳过高亮
        if (blob.size > HUGE_BYTES) {
          onReady?.();
          return;
        }
        const lang = LANG_MAP[getExt(file.fileName)] ?? 'text';
        try {
          const shiki = await import('shiki');
          const highlighter = await shiki.getSingletonHighlighter({
            themes: ['github-light', 'github-dark'],
            langs: [lang],
          });
          if (!live) return;
          const out = highlighter.codeToHtml(t, {
            lang: highlighter.getLoadedLanguages().includes(lang as never)
              ? (lang as never)
              : ('text' as never),
            themes: { light: 'github-light', dark: 'github-dark' },
            defaultColor: false,
          });
          setHtml(out);
          onReady?.();
        } catch {
          // shiki 失败 → 回退纯文本
          onReady?.();
        }
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        const err = e as Error;
        setErr(err);
        onError?.(err);
      }
    })();
    return () => {
      live = false;
      ac.abort();
    };
  }, [file, fetchBlob, onReady, onError]);

  if (err) return <ErrorRenderer error={err} />;
  if (text == null) return <LoadingRenderer />;

  return (
    <div className="h-full overflow-auto bg-[var(--cy-surface-base)] p-4">
      {html ? (
        <div
          className="text-xs leading-relaxed [&_pre]:!bg-transparent [&_pre]:m-0 [&_pre]:p-0"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-[var(--cy-text-primary)]">
          {text}
        </pre>
      )}
    </div>
  );
};
