/**
 * 文件预览模块 — 类型契约。
 *
 * 中心思想:Renderer 之间的差异通过 `kind` 路由,业务侧只构造 `PreviewFile`,
 * 不需要关心格式。新增格式只要新增 kind + renderer 注册项。
 */

/** 预览渲染分类。每一类对应一个 lazy 加载的 renderer */
export type PreviewKind =
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'markdown'
  | 'text'
  | 'docx'
  | 'xlsx'
  | 'pptx'
  | 'unsupported';

/** 面板的工作模式 */
export type PanelMode = 'dock' | 'float' | 'window';

/**
 * 文件来源。当前只支持知识库文档(kb-doc),后续可加 chat-attachment / drive 等。
 *
 * 不直接传 URL,因为:
 *   - URL 里要塞 access token,token 会过期 → 需要每次构建时重新拼
 *   - 独立窗口要从 URL query 反序列化,字段比较稳定的更好
 */
export interface KbDocPreviewFile {
  source: 'kb-doc';
  /** 知识库 name(后端 knowledge_base_name) */
  kbName: string;
  /** 文件名(含扩展) */
  fileName: string;
  /** 可选:展示用大小,字节 */
  fileSize?: number;
  /** 可选:文件 mime,detectKind 用得上 */
  mime?: string;
}

/** 用 absolute URL 直接预览(适用于 image-source URL、外部链接等) */
export interface DirectPreviewFile {
  source: 'direct';
  /** 已经是可 GET 的 URL(可能含 token) */
  url: string;
  /** 文件名,显示用;同时驱动 detectKind */
  fileName: string;
  fileSize?: number;
  mime?: string;
}

export type PreviewFile = KbDocPreviewFile | DirectPreviewFile;

/** 用 PreviewFile 计算出"稳定 key",用作 react-query 缓存 / blob LRU 键 */
export function previewFileKey(f: PreviewFile): string {
  switch (f.source) {
    case 'kb-doc':
      return `kb:${f.kbName}/${f.fileName}`;
    case 'direct':
      return `url:${f.url}`;
  }
}

/** Renderer 通用 props */
export interface RendererProps {
  file: PreviewFile;
  /** 已带 token 的可直接 GET 的 URL(blob/iframe/img/video/audio 都吃这个) */
  url: string;
  /** 缓存版 blob 拉取(供 docx/xlsx 这种需要 ArrayBuffer 的 renderer) */
  fetchBlob: (signal?: AbortSignal) => Promise<Blob>;
  /** Renderer 准备好(不再 loading)时调用,Toolbar 可以收掉旋转 */
  onReady?: () => void;
  /** 让外层接管错误展示,而不是 renderer 自渲染 */
  onError?: (err: Error) => void;
}

/** Toolbar 根据 mode 决定渲染哪些按钮 */
export interface PanelStateSnapshot {
  current: PreviewFile | null;
  mode: PanelMode;
  collapsed: boolean;
  width: number;
}
