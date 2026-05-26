/**
 * .docx 预览:用 mammoth 在浏览器里把 docx → HTML。
 *
 * - mammoth 走动态 import,首屏不引入 ~400 KB
 * - 转完用 DOMPurify 过一遍,防止 docx 里的恶意 SVG / iframe
 * - .doc 走不到这里(detectKind 把 .doc 路由到 fallback,因为 mammoth 不支持)
 */

import DOMPurify from 'dompurify';
import * as React from 'react';
import type { RendererProps } from '../types';
import { ErrorRenderer } from './ErrorRenderer';
import { LoadingRenderer } from './LoadingRenderer';

export const DocxRenderer: React.FC<RendererProps> = ({ fetchBlob, onReady, onError }) => {
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
        const buf = await blob.arrayBuffer();
        // mammoth 已在 packages/app/package.json declared；走静态字符串 import 让
        // Vite 在 dev 期 pre-bundle，浏览器运行时直接命中。
        // 老版用 modName 变量 + @vite-ignore 绕静态分析，反作用让 dev 端解析失败。
        // mammoth 历史版本不带 .d.ts，这里手动 assert 形状。
        const mammoth = (await import('mammoth')) as unknown as {
          convertToHtml: (input: { arrayBuffer: ArrayBuffer }) => Promise<{ value: string }>;
        };
        if (!live) return;
        const result = await mammoth.convertToHtml({ arrayBuffer: buf });
        if (!live) return;
        const safe = DOMPurify.sanitize(result.value, { USE_PROFILES: { html: true } });
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
  if (html == null) return <LoadingRenderer label="解析 Word 文档…" />;
  return (
    <div className="h-full overflow-auto bg-[var(--cy-surface-base)] px-8 py-6">
      <div
        className="cy-md-prose mx-auto max-w-3xl text-sm leading-relaxed text-[var(--cy-text-primary)]"
        // biome-ignore lint/security/noDangerouslySetInnerHtml: mammoth HTML is sanitized by DOMPurify above.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
};
