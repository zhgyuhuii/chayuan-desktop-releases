/**
 * 外层上传目标选择对话框:
 *   - 列出指定 kind 的所有 KB
 *   - 选完后调 onPick(kuId, displayName) → 父组件负责跳转/上传
 *   - 顶部"+ 新建 X 库"按钮:关闭本对话框,通知父组件触发 CreateKbDialog
 */
import * as React from 'react';
import { Library, Loader2, Plus, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { Button, cn } from '@chayuan/ui';
import { knowledgeUniverse, type KuItem, type KuKind } from '@chayuan/api';

export interface UploadTargetDialogProps {
  open: boolean;
  kind: KuKind;
  title?: string;
  onClose(): void;
  /** 选中已有库:回调真实 ku_id(形如 'doc:xx' / 'src:42')+ 显示名 */
  onPick(kuId: string, displayName: string): void;
  /** 用户点 "+ 新建" 时,父组件应触发对应 kind 的 CreateKbDialog */
  onRequestCreate(kind: KuKind): void;
}

const KIND_LABEL: Record<KuKind, string> = {
  document: '文档知识库',
  image: '图像知识库',
  structured: '结构化数据源',
  vector: '外部向量库',
};

export const UploadTargetDialog: React.FC<UploadTargetDialogProps> = ({
  open, kind, title, onClose, onPick, onRequestCreate,
}) => {
  const { data: kbs, isLoading } = useQuery({
    queryKey: ['kbs', kind, 'for-upload-target'],
    queryFn: () => knowledgeUniverse.list(kind),
    enabled: open,
  });
  const list = (kbs ?? []).filter((k) => k.kind === kind);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        className="w-full max-w-md rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold">
            {title ?? `选择${KIND_LABEL[kind]}`}
          </h3>
          <button onClick={onClose} className="text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-xs text-[var(--cy-text-tertiary)]">
          选择一个已有的{KIND_LABEL[kind]},或新建一个。
        </p>
        <div className="mb-3 max-h-64 overflow-auto rounded-xl border border-[var(--cy-border-subtle)]">
          {isLoading ? (
            <div className="flex items-center justify-center p-8 text-xs text-[var(--cy-text-tertiary)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载列表…
            </div>
          ) : list.length === 0 ? (
            <div className="p-6 text-center text-xs text-[var(--cy-text-tertiary)]">
              暂无{KIND_LABEL[kind]}
            </div>
          ) : (
            <ul>
              {list.map((k: KuItem) => (
                <li key={k.ku_id}>
                  <button
                    type="button"
                    onClick={() => onPick(k.ku_id, k.display_name || k.name || k.ku_id)}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition',
                      'hover:bg-[var(--cy-surface-1)]',
                    )}
                  >
                    <Library className="h-4 w-4 shrink-0 text-[var(--cy-text-tertiary)]" />
                    <span className="flex-1 truncate">
                      {k.display_name || k.name || k.ku_id}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex justify-between gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onRequestCreate(kind)}
          >
            <Plus className="mr-1 h-3 w-3" /> 新建{KIND_LABEL[kind]}
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
        </div>
      </div>
    </div>
  );
};
