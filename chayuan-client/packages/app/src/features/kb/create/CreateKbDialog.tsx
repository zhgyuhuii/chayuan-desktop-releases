/**
 * 创建知识库容器 — 第一屏 kind picker;选定后跳到对应表单。
 *
 * 4 类:
 *   document  → DocumentKbForm
 *   image     → ImageKbForm
 *   structured → 走旧的 AddStructuredSourceDialog(由父级控制其 open)
 *   vector    → VectorKbForm
 *
 * 用法:
 *   <CreateKbDialog open={open} initialKind="document" onOpenChange={setOpen} onCreated={...} />
 */

import * as React from 'react';
import { ArrowLeft, Boxes, Database, FileText, Images } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  cn,
} from '@chayuan/ui';
import type { KuKind } from '@chayuan/api';
import { DocumentKbForm } from './DocumentKbForm';
import { ImageKbForm } from './ImageKbForm';
import { VectorKbForm } from './VectorKbForm';

export interface CreateKbDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 默认选中的 kind;不传时显示 picker */
  initialKind?: KuKind;
  /** 创建成功;父级可关闭 + 高亮新卡 */
  onCreated?(kuId: string, kind: KuKind): void;
  /** "结构化" 由父级单独处理(复用旧 AddStructuredSourceDialog),触发即开 */
  onPickStructured?(): void;
}

interface KindOption {
  value: KuKind;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  desc: string;
  gradient: string;
}

const OPTIONS: KindOption[] = [
  {
    value: 'document',
    icon: FileText,
    label: '文档库',
    desc: '上传 PDF / Word / Markdown / 网页等;自动切分 + 向量化',
    gradient: 'from-sky-500 to-indigo-600',
  },
  // 图像库已隐藏 —— 整套图像向量化(CLIP / image-embedding)硬依赖 PyTorch +
  // torchvision + transformers,体积/复杂度大且本机推理常出问题。文档库已能
  // 自动对图片做 OCR → 文本嵌入,搜文字即可命中图片,够覆盖大多数图像检索
  // 需求;以图搜图等纯视觉检索本版本暂未启用。
  {
    value: 'structured',
    icon: Database,
    label: '数据库',
    desc: '连接 SQL / Mongo / ES;支持自然语言提问自动生成 SQL',
    gradient: 'from-emerald-500 to-teal-600',
  },
  {
    value: 'vector',
    icon: Boxes,
    label: '外部向量库',
    desc: '挂接已有的 Milvus / pgvector / ES vec / Chroma 等;复用现有向量数据',
    gradient: 'from-amber-500 to-orange-600',
  },
];

export const CreateKbDialog: React.FC<CreateKbDialogProps> = ({
  open,
  onOpenChange,
  initialKind,
  onCreated,
  onPickStructured,
}) => {
  const [kind, setKind] = React.useState<KuKind | null>(initialKind ?? null);

  React.useEffect(() => {
    if (open) setKind(initialKind ?? null);
  }, [open, initialKind]);

  const onSubmitted = (kuId: string, k: KuKind) => {
    onCreated?.(kuId, k);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] w-[92vw] max-w-2xl flex-col gap-0 overflow-hidden p-0" hideClose>
        <header className="flex items-center gap-3 border-b border-[var(--cy-border-subtle)] px-5 py-3">
          {kind && (
            <button
              type="button"
              onClick={() => setKind(null)}
              aria-label="返回"
              className="-ml-1 inline-flex h-7 w-7 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <DialogTitle asChild>
              <h2 className="text-base font-semibold text-[var(--cy-text-primary)]">
                {kind ? `创建 ${OPTIONS.find((o) => o.value === kind)?.label}` : '新建知识库'}
              </h2>
            </DialogTitle>
            <DialogDescription className="text-[11px] text-[var(--cy-text-tertiary)]">
              {kind ? '填写下面的字段;创建后可直接拖文件入卡片自动上传' : '选一种类型开始'}
            </DialogDescription>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {!kind && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {OPTIONS.map((opt) => (
                <KindCard
                  key={opt.value}
                  opt={opt}
                  onClick={() => {
                    if (opt.value === 'structured') {
                      onOpenChange(false);
                      onPickStructured?.();
                      return;
                    }
                    setKind(opt.value);
                  }}
                />
              ))}
            </div>
          )}
          {kind === 'document' && (
            <DocumentKbForm onSubmitted={(name) => onSubmitted(`doc:${name}`, 'document')} />
          )}
          {kind === 'image' && (
            <ImageKbForm onSubmitted={(srcId) => onSubmitted(`src:${srcId}`, 'image')} />
          )}
          {kind === 'vector' && (
            <VectorKbForm onSubmitted={(srcId) => onSubmitted(`src:${srcId}`, 'vector')} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

const KindCard: React.FC<{ opt: KindOption; onClick(): void }> = ({ opt, onClick }) => {
  const Icon = opt.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group relative flex flex-col gap-2 overflow-hidden rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-4 text-left transition-all',
        'hover:-translate-y-0.5 hover:border-[var(--cy-brand-300)] hover:shadow-[var(--cy-shadow-md)]',
      )}
    >
      <span className={cn('inline-flex h-9 w-9 items-center justify-center rounded-xl text-white bg-gradient-to-br', opt.gradient)}>
        <Icon className="h-4 w-4" />
      </span>
      <p className="text-sm font-semibold text-[var(--cy-text-primary)]">{opt.label}</p>
      <p className="text-[11px] leading-relaxed text-[var(--cy-text-tertiary)]">{opt.desc}</p>
    </button>
  );
};
