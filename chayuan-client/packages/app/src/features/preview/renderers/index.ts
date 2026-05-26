/**
 * Renderer 注册表。
 *
 * 每个 entry 是 React.lazy,确保大依赖(mammoth / xlsx / shiki)按需加载。
 * 新增格式:
 *   1. 在 detectKind.ts 加 ext / mime 映射
 *   2. 在这里加 lazy 注册项
 *   3. 写一个 renderer
 */

import * as React from 'react';
import type { ComponentType } from 'react';
import type { PreviewKind, RendererProps } from '../types';

type LazyRenderer = ReturnType<typeof React.lazy<ComponentType<RendererProps>>>;

export const RENDERER_REGISTRY: Record<PreviewKind, LazyRenderer> = {
  image: React.lazy(() => import('./ImageRenderer').then((m) => ({ default: m.ImageRenderer }))),
  video: React.lazy(() => import('./VideoRenderer').then((m) => ({ default: m.VideoRenderer }))),
  audio: React.lazy(() => import('./AudioRenderer').then((m) => ({ default: m.AudioRenderer }))),
  pdf: React.lazy(() => import('./PdfRenderer').then((m) => ({ default: m.PdfRenderer }))),
  markdown: React.lazy(() =>
    import('./MarkdownRenderer').then((m) => ({ default: m.MarkdownRenderer })),
  ),
  text: React.lazy(() => import('./TextRenderer').then((m) => ({ default: m.TextRenderer }))),
  docx: React.lazy(() => import('./DocxRenderer').then((m) => ({ default: m.DocxRenderer }))),
  xlsx: React.lazy(() => import('./XlsxRenderer').then((m) => ({ default: m.XlsxRenderer }))),
  pptx: React.lazy(() => import('./PptxRenderer').then((m) => ({ default: m.PptxRenderer }))),
  unsupported: React.lazy(() =>
    import('./FallbackRenderer').then((m) => ({ default: m.FallbackRenderer })),
  ),
};
