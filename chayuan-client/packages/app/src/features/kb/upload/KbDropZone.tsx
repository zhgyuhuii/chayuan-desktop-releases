/**
 * 卡片包裹 — 让某 KB 卡支持拖拽文件 / 文件夹上传。
 *
 * 行为:
 *   - 仅 doc / image kind 真正接受 drop;non-owner / 不支持 kind → 拒绝 toast
 *   - 文件 + 文件夹混拖支持(走 expandDataTransfer 递归 webkitGetAsEntry)
 *   - hover 时浮层"拖入到 [name]" + 文件夹图标
 *   - 静态 hint(`hint='persistent'`)在右下角显示「📂 支持拖拽」小贴士
 *
 * 技术:
 *   - dragenter/leave 用计数器避免子元素切换抖动(经典 trick)
 *   - 图像 KB 校验 mime;doc KB 不限制(后端可处理任意类型)
 *   - 文件夹相对路径用 file.webkitRelativePath 透传,后端 KnowledgeFile 自动落
 *     到嵌套子目录
 */

import * as React from 'react';
import { FolderUp, Upload } from 'lucide-react';
import { cn } from '@chayuan/ui';
import type { KuItem } from '@chayuan/api';
import { useKbUpload } from './useKbUpload';
import { selectKuSummary, useKbUploadStore } from './kbUploadStore';
import { notifyInfo } from '../../../store/errorDialog';
import { expandDataTransfer } from './dropWalker';

export interface KbDropZoneProps {
  kb: KuItem;
  className?: string;
  children: React.ReactNode;
  /**
   * 静态 hint 形态:
   *   - 'none'(默认):仅在拖拽 hover 时显示浮层
   *   - 'corner':右下角常驻一行"📂 支持拖拽"小贴士,不打扰主体
   *   - 'panel':在主体下方留出一条带边框的"拖到这里上传"区(详情页用)
   */
  hint?: 'none' | 'corner' | 'panel';
  /** owner 才允许上传;false 时 drop 直接 toast 拒绝 */
  canUpload?: boolean;
}

/** 解码 ku_id "src:42" → 42 */
function srcIdFromKuId(kuId: string): number | null {
  if (!kuId.startsWith('src:')) return null;
  const n = Number(kuId.slice(4));
  return Number.isFinite(n) ? n : null;
}

export const KbDropZone: React.FC<KbDropZoneProps> = ({
  kb, className, children, hint = 'none', canUpload = true,
}) => {
  const { submit } = useKbUpload();
  const uploadSummary = useKbUploadStore(selectKuSummary(kb.ku_id));
  const [submitting, setSubmitting] = React.useState(false);
  const counterRef = React.useRef(0);
  const [hover, setHover] = React.useState(false);
  const [hasFolder, setHasFolder] = React.useState(false);
  const supportsKind = kb.kind === 'document' || kb.kind === 'image';
  const uploadLocked = submitting || uploadSummary.active;
  const allowed = supportsKind && canUpload && !uploadLocked;

  const onDragEnter = (e: React.DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    counterRef.current += 1;
    setHover(true);
    // dataTransfer.items 在 dragover 阶段就能侦测有没有目录(不读到 file 内容)
    let folder = false;
    const items = e.dataTransfer.items;
    if (items) {
      for (let i = 0; i < items.length; i++) {
        const it = items[i] as DataTransferItem & { webkitGetAsEntry?(): { isDirectory?: boolean } | null };
        if (it.kind !== 'file') continue;
        const entry = it.webkitGetAsEntry?.();
        if (entry?.isDirectory) { folder = true; break; }
      }
    }
    setHasFolder(folder);
  };
  const onDragLeave = (e: React.DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    counterRef.current -= 1;
    if (counterRef.current <= 0) {
      counterRef.current = 0;
      setHover(false);
      setHasFolder(false);
    }
  };
  const onDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = allowed ? 'copy' : 'none';
  };
  const onDrop = async (e: React.DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    e.stopPropagation();
    counterRef.current = 0;
    setHover(false);
    setHasFolder(false);

    if (!supportsKind) {
      notifyInfo('此知识库不支持文件上传', `「${kb.display_name}」是 ${labelForKind(kb.kind)} 类型,不接受文件拖拽`);
      return;
    }
    if (!canUpload) {
      notifyInfo('无上传权限', '只有知识库的所有者或管理员可以上传文件');
      return;
    }
    if (uploadLocked) {
      notifyInfo('正在上传', '当前知识库已有文件上传中，请等待完成后再继续');
      return;
    }

    // 走 expandDataTransfer 拿真正全部 files(含文件夹递归);
    // 同步路径下用 await 是为了拿到 entry → file 的 callback 链结果
    const files = await expandDataTransfer(e.dataTransfer);
    if (files.length === 0) {
      notifyInfo('未拖到文件', '可能是空文件夹或浏览器不支持');
      return;
    }

    if (kb.kind === 'image') {
      const accepted = files.filter((f) => f.type.startsWith('image/'));
      const rejected = files.length - accepted.length;
      if (accepted.length === 0) {
        notifyInfo('请拖入图片', '当前是图像知识库,只接受 image/* 类型');
        return;
      }
      if (rejected > 0) {
        notifyInfo(`已忽略 ${rejected} 个非图片文件`, `本次仅上传 ${accepted.length} 张图片`);
      }
      const sourceId = srcIdFromKuId(kb.ku_id);
      if (sourceId == null) return;
      setSubmitting(true);
      try {
        await submit({ kuId: kb.ku_id, kind: 'image', sourceId, files: accepted });
      } finally {
        setSubmitting(false);
      }
    } else {
      // doc:任意类型;有 webkitRelativePath 的会保留嵌套路径
      setSubmitting(true);
      try {
        await submit({ kuId: kb.ku_id, kind: 'document', kbName: kb.name, files });
      } finally {
        setSubmitting(false);
      }
    }
  };

  return (
    <div
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={cn('relative', className)}
    >
      {children}

      {/* 静态 hint:右下角小贴士 */}
      {hint === 'corner' && allowed && !hover && (
        <span
          className="pointer-events-none absolute bottom-1.5 left-2 inline-flex items-center gap-1 rounded-full bg-white/85 px-1.5 py-0.5 text-[9px] font-medium text-[var(--cy-text-tertiary)] opacity-0 shadow-sm backdrop-blur-sm transition-opacity duration-200 group-hover:opacity-100"
          aria-hidden
        >
          <FolderUp className="h-2.5 w-2.5" />
          可拖入文件 / 文件夹
        </span>
      )}

      {/* 静态 hint:面板形态(详情页用) — 顶部一条窄横幅 */}
      {hint === 'panel' && allowed && !hover && (
        <div
          className="pointer-events-none absolute left-0 right-0 top-0 z-10 flex items-center justify-center gap-1.5 border-b border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/80 px-3 py-1 text-[10px] text-[var(--cy-text-tertiary)] backdrop-blur-sm"
          aria-hidden
        >
          <FolderUp className="h-3 w-3" />
          把文件或文件夹拖进这个区域即可上传(保留子目录结构)
        </div>
      )}

      {/* 拖拽 hover 浮层 */}
      {hover && (
        <div
          className={cn(
            'pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-xl border-2 border-dashed transition-all duration-150',
            allowed
              ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)]/85 backdrop-blur-[1px]'
              : 'border-slate-300 bg-slate-100/80 backdrop-blur-[1px]',
          )}
        >
          <div className="flex flex-col items-center gap-1 text-center">
            {hasFolder ? (
              <FolderUp className={cn('h-6 w-6', allowed ? 'text-[var(--cy-brand-700)] animate-bounce' : 'text-slate-500')} />
            ) : (
              <Upload className={cn('h-5 w-5', allowed ? 'text-[var(--cy-brand-700)]' : 'text-slate-500')} />
            )}
            <p className={cn(
              'text-xs font-medium',
              allowed ? 'text-[var(--cy-brand-700)]' : 'text-slate-600',
            )}>
              {!supportsKind
                ? `${labelForKind(kb.kind)} 不支持文件上传`
                : !canUpload
                  ? '只有所有者可以上传'
                  : uploadLocked
                    ? '当前知识库正在上传'
                  : hasFolder
                    ? `释放整个文件夹到 ${kb.display_name}`
                    : `释放上传到 ${kb.display_name}`}
            </p>
            {allowed && hasFolder && (
              <p className="text-[10px] text-[var(--cy-brand-700)]/80">
                子目录结构会保留
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

function labelForKind(k: KuItem['kind']): string {
  switch (k) {
    case 'document': return '文档';
    case 'image': return '图像';
    case 'structured': return '数据库';
    case 'vector': return '向量';
    default: return '未知';
  }
}
