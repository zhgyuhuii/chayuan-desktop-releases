/**
 * HelpPage — 帮助中心页。
 *
 * 加载 helper/ 目录下的 markdown 文档,走主对话页同款的 StreamMarkdown 渲染。
 * 文档以 Vite 的 `?raw` 后缀作为字符串静态打包进 bundle,无需运行时 fetch,
 * 在完全离线场景下也能正常打开。
 *
 * 后续如有多篇文档,加成左侧目录 + 右侧正文的两栏布局即可;v1 单篇足够。
 */

import type * as React from 'react';
import { StreamMarkdown } from '../../lib/markdown';
import gettingStartedMd from './helper/getting-started.md?raw';

export const HelpPage: React.FC = () => {
  return (
    <div className="h-full overflow-auto bg-[var(--cy-surface-base)]">
      <div className="mx-auto max-w-3xl px-6 py-8 text-sm leading-relaxed text-[var(--cy-text-primary)]">
        <article className="cy-help-doc prose prose-sm max-w-none dark:prose-invert">
          <StreamMarkdown content={gettingStartedMd} streaming={false} />
        </article>
      </div>
      <style>{`
        .cy-help-doc h1 { font-size: 1.65rem; font-weight: 700; margin: 1.2rem 0 0.8rem; }
        .cy-help-doc h2 { font-size: 1.3rem; font-weight: 700; margin: 1.6rem 0 0.6rem; padding-bottom: 0.3rem; border-bottom: 1px solid var(--cy-border-subtle); }
        .cy-help-doc h3 { font-size: 1.05rem; font-weight: 600; margin: 1.1rem 0 0.4rem; }
        .cy-help-doc h4 { font-size: 0.95rem; font-weight: 600; margin: 0.8rem 0 0.3rem; }
        .cy-help-doc p  { margin: 0.5rem 0; }
        .cy-help-doc ul, .cy-help-doc ol { margin: 0.4rem 0 0.6rem 1.4rem; }
        .cy-help-doc li { margin: 0.15rem 0; }
        .cy-help-doc blockquote { margin: 0.6rem 0; padding: 0.4rem 0.8rem; border-left: 3px solid var(--cy-border-default); background: var(--cy-surface-1); color: var(--cy-text-secondary); border-radius: 0 4px 4px 0; }
        .cy-help-doc code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85em; padding: 0.1em 0.35em; border-radius: 3px; background: var(--cy-surface-2); }
        .cy-help-doc pre { padding: 0.7rem 0.9rem; border-radius: 6px; background: var(--cy-surface-2); overflow-x: auto; margin: 0.6rem 0; }
        .cy-help-doc pre code { padding: 0; background: transparent; font-size: 0.82em; }
        .cy-help-doc table { width: 100%; border-collapse: collapse; margin: 0.6rem 0; font-size: 0.88em; }
        .cy-help-doc th, .cy-help-doc td { border: 1px solid var(--cy-border-subtle); padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }
        .cy-help-doc th { background: var(--cy-surface-1); font-weight: 600; }
        .cy-help-doc hr { border: 0; border-top: 1px solid var(--cy-border-subtle); margin: 1.4rem 0; }
        .cy-help-doc a { color: var(--cy-primary, #6366f1); text-decoration: underline; }
      `}</style>
    </div>
  );
};
