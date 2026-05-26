/**
 * Markdown 预览:fetch 文本 → marked → DOMPurify(已装的库)。
 *
 * 安全:不允许 raw HTML,避免知识库污染攻击向量。
 * 排版:用 prose-like CSS 类(沿用 design-tokens 颜色变量)。
 */

import DOMPurify from 'dompurify';
import { marked } from 'marked';
import * as React from 'react';
import type { RendererProps } from '../types';
import { ErrorRenderer } from './ErrorRenderer';
import { LoadingRenderer } from './LoadingRenderer';

export const MarkdownRenderer: React.FC<RendererProps> = ({ fetchBlob, onReady, onError }) => {
  const [html, setHtml] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<Error | null>(null);

  React.useEffect(() => {
    let live = true;
    const ac = new AbortController();
    setHtml(null);
    setErr(null);
    void (async () => {
      try {
        const blob = await fetchBlob(ac.signal);
        const text = await blob.text();
        if (!live) return;
        const raw = await marked.parse(text, { async: true, breaks: true, gfm: true });
        const safe = DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
        if (!live) return;
        setHtml(safe);
        onReady?.();
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
  }, [fetchBlob, onReady, onError]);

  if (err) return <ErrorRenderer error={err} />;
  if (html == null) return <LoadingRenderer />;

  return (
    <div className="h-full overflow-auto bg-[var(--cy-surface-base)] px-6 py-5">
      <div
        className="cy-md-prose mx-auto max-w-3xl text-sm leading-relaxed text-[var(--cy-text-primary)]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <style>{cyMdCss}</style>
    </div>
  );
};

// 极简 markdown 样式(避免引入 typography 插件)
const cyMdCss = `
.cy-md-prose h1{font-size:1.6em;font-weight:700;margin:.6em 0 .4em;border-bottom:1px solid var(--cy-border-subtle);padding-bottom:.3em}
.cy-md-prose h2{font-size:1.35em;font-weight:700;margin:.6em 0 .3em}
.cy-md-prose h3{font-size:1.15em;font-weight:600;margin:.5em 0 .25em}
.cy-md-prose p{margin:.5em 0}
.cy-md-prose ul,.cy-md-prose ol{padding-left:1.5em;margin:.5em 0}
.cy-md-prose li{margin:.2em 0}
.cy-md-prose code{background:var(--cy-surface-1);padding:1px 5px;border-radius:4px;font-size:.92em}
.cy-md-prose pre{background:var(--cy-surface-1);padding:.8em 1em;border-radius:6px;overflow-x:auto;margin:.6em 0;font-size:.9em}
.cy-md-prose pre code{background:transparent;padding:0;border-radius:0}
.cy-md-prose blockquote{border-left:3px solid var(--cy-brand-400);padding-left:.8em;color:var(--cy-text-secondary);margin:.6em 0}
.cy-md-prose a{color:var(--cy-brand-500);text-decoration:underline}
.cy-md-prose img{max-width:100%;border-radius:6px}
.cy-md-prose table{border-collapse:collapse;margin:.6em 0;font-size:.92em}
.cy-md-prose th,.cy-md-prose td{border:1px solid var(--cy-border-subtle);padding:.35em .6em}
.cy-md-prose th{background:var(--cy-surface-1);text-align:left}
.cy-md-prose hr{border:0;border-top:1px solid var(--cy-border-subtle);margin:1em 0}
`;
