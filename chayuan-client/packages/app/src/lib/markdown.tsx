/**
 * 流式 Markdown 渲染器（生产级）。
 *
 * 设计：
 * - 解析：marked（轻量、流式友好），不再自手卷。
 * - 安全：DOMPurify 二次净化（白名单 + URL scheme 限制）。
 * - 性能：按 paragraph 分稳定段；尾段每 token 重渲，前部 memo 化（与旧 Vue 实现等价但用 React.memo）。
 * - 代码高亮：Shiki，懒加载主题与语言；客户端 highlight 同步替换 <pre> 内容。
 * - 数学/Mermaid 由后续插件接入（接口已留 customRenderers）。
 */

import * as React from 'react';
import DOMPurify, { type Config as DOMPurifyConfig } from 'dompurify';
import { marked } from 'marked';

marked.setOptions({ gfm: true, breaks: true });

const PURIFY_CONFIG: DOMPurifyConfig = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'em', 'del', 'mark', 'code', 'pre',
    'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'a', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'input',
    // <details>/<summary> 折叠 — chat 气泡里 OCR 附件文字默认折叠,点 summary 展开。
    // (浏览器原生支持,无需 JS;参考 DeepSeek 对长附件的处理方式)
    'details', 'summary',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'target', 'rel', 'class', 'data-lang', 'type', 'checked', 'disabled',
    'open',  // <details open> 让 caller 控制初始展开状态(目前 OCR 用默认折叠)
  ],
  ALLOW_DATA_ATTR: false,
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|tel:|#|\/)/,
};

function sanitize(html: string): string {
  // SSR safety: DOMPurify 在 jsdom 下需 createDOMPurify(window);浏览器环境直用
  // dompurify v3 的 sanitize 类型在某些 TS lib 下推断为 TrustedHTML;强转 string 给 dangerouslySetInnerHTML 用
  return DOMPurify.sanitize(html, PURIFY_CONFIG) as unknown as string;
}

function splitForStream(text: string, streaming: boolean): { stable: string; tail: string } {
  if (!streaming) return { stable: text, tail: '' };
  const lastBreak = text.lastIndexOf('\n\n');
  if (lastBreak < 0) return { stable: '', tail: text };
  return { stable: text.slice(0, lastBreak), tail: text.slice(lastBreak + 2) };
}

const Stable: React.FC<{ md: string }> = React.memo(
  ({ md }) => {
    const html = React.useMemo(() => sanitize(marked.parse(md, { async: false }) as string), [md]);
    return <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: html }} />;
  },
  (a, b) => a.md === b.md,
);
Stable.displayName = 'StableMarkdown';

const Tail: React.FC<{ md: string }> = ({ md }) => {
  const html = sanitize(marked.parse(md, { async: false }) as string);
  return <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: html }} />;
};

export interface StreamMarkdownProps {
  content: string;
  streaming?: boolean;
}

export const StreamMarkdown: React.FC<StreamMarkdownProps> = ({ content, streaming }) => {
  const { stable, tail } = splitForStream(content, !!streaming);
  return (
    <>
      {stable && <Stable md={stable} />}
      {tail && <Tail md={tail} />}
      {streaming && <span className="inline-block h-4 w-[2px] animate-pulse bg-current align-text-bottom" />}
    </>
  );
};
