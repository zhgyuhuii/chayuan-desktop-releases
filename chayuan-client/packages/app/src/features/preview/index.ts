/**
 * 预览模块公共 API。
 *
 * 业务侧只需要:
 *   import { usePreviewStore } from '@chayuan/app/.../preview';
 *   usePreviewStore.getState().open({ source: 'kb-doc', kbName, fileName });
 *
 * Shell 挂 <PreviewMount /> 一次,即可全局生效。
 */

export { PreviewMount } from './PreviewMount';
export { PreviewPanel } from './PreviewPanel';
export { usePreviewStore } from './previewStore';
export { PreviewStandaloneRoute } from './standalone/PreviewStandaloneRoute';
export type { PreviewFile, PreviewKind, PanelMode } from './types';
export { detectKind, extLabel } from './detectKind';
