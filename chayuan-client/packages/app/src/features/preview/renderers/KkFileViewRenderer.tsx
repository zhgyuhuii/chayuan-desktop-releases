/**
 * kkFileView 旁车预览 — 当后端配置了 KKFILEVIEW_URL 时全格式接管。
 *
 * 协议:`<kkBase>/onlinePreview?url=<base64UrlEncoded(fileUrl)>`
 *   - kkFileView 用 base64 url 而不是普通 urlencode,见
 *     https://github.com/kekingcn/kk-FileView 文档示例
 *   - 我们用浏览器原生 btoa(unescape(encodeURIComponent(...))) 兼容中文
 *
 * 注意:
 *   - kkFileView 容器要能访问 fileUrl(如 host.docker.internal 转发),
 *     这是部署侧的事,前端只负责把 url 拼对
 *   - 我们的 fileUrl 已经带 ?token=...,kkFileView 会原样请求,后端 query-token
 *     白名单已经把 /knowledge_base/preview_doc 放行,token 解析正常
 */

import * as React from 'react';
import { ExternalLink } from 'lucide-react';
import { Button } from '@chayuan/ui';
import type { RendererProps } from '../types';
import { LoadingRenderer } from './LoadingRenderer';

interface Props extends RendererProps {
  /** 后端返回的 kkFileView 基地址(已 trim,绝对 URL) */
  kkBase: string;
}

/** 把 file URL 安全地 base64-url 编码成 kkFileView 能吃的格式 */
function encodeFileUrl(url: string): string {
  // 浏览器侧必须经 unescape(encodeURIComponent(...)) 才能正确处理中文 + 特殊字符
  const utf8 = unescape(encodeURIComponent(url));
  return btoa(utf8);
}

export const KkFileViewRenderer: React.FC<Props> = ({ url, kkBase, file, onReady }) => {
  const [loading, setLoading] = React.useState(true);
  const [errored, setErrored] = React.useState(false);

  const previewSrc = React.useMemo(() => {
    const base = kkBase.replace(/\/+$/, '');
    return `${base}/onlinePreview?url=${encodeURIComponent(encodeFileUrl(url))}`;
  }, [kkBase, url]);

  const handleLoad = () => {
    setLoading(false);
    onReady?.();
  };
  const handleError = () => {
    setLoading(false);
    setErrored(true);
  };

  if (errored) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm font-medium text-[var(--cy-text-primary)]">
          kkFileView 预览失败
        </p>
        <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">
          可能原因:① 配置的 kkFileView 地址 <code className="rounded bg-[var(--cy-surface-1)] px-1 py-0.5">{kkBase}</code> 浏览器无法访问;
          ② kkFileView 容器无法回拉文件 URL(同机部署时通常需把 host 改成 <code className="rounded bg-[var(--cy-surface-1)] px-1 py-0.5">host.docker.internal</code>);
          ③ 该格式 kkFileView 也未支持。
        </p>
        <div className="flex items-center gap-2">
          <Button asChild size="sm" variant="outline">
            <a href={previewSrc} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-1 h-3.5 w-3.5" /> 在新标签页打开
            </a>
          </Button>
          <Button asChild size="sm" variant="ghost">
            <a href={url} target="_blank" rel="noreferrer">
              直接下载 {file.fileName}
            </a>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full bg-[var(--cy-surface-1)]">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--cy-surface-base)]/80">
          <LoadingRenderer label="kkFileView 加载中…" />
        </div>
      )}
      <iframe
        src={previewSrc}
        title={file.fileName}
        className="h-full w-full border-0"
        onLoad={handleLoad}
        onError={handleError}
        // sandbox 不开:kkFileView 内部多种 viewer 需要 same-origin storage / cookies 才能跑
        // referrerPolicy 不主动改 — 让默认 strict-origin-when-cross-origin 生效
        allow="fullscreen"
      />
    </div>
  );
};
