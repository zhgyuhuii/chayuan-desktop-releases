/**
 * PDF 预览:直接 iframe 到带 token 的 URL。
 *
 * 选型:
 *   - Chromium 内置 PDF Viewer 已经很完善(分页/搜索/缩放/打印)
 *   - 不引入 pdfjs-dist(2 MB+)是为了首屏轻
 *   - 后端 preview_doc 端点必须返回 Content-Type: application/pdf
 */

import type * as React from 'react';
import type { RendererProps } from '../types';

export const PdfRenderer: React.FC<RendererProps> = ({ url, onReady, onError, file }) => {
  return (
    <iframe
      title={file.fileName}
      src={url}
      className="h-full w-full border-0 bg-[var(--cy-surface-base)]"
      onLoad={() => onReady?.()}
      onError={() => onError?.(new Error('PDF 加载失败'))}
    />
  );
};
