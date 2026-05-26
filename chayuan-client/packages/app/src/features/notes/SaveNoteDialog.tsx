/**
 * 保存笔记弹窗:列出用户所有 doc KB,允许选择目标库;顶部按钮跳"新建知识库"。
 *
 * Props:
 *   open / onClose / onSaved(kbName) — 保存成功后回调
 *   title / content — 待保存的 Tiptap doc
 */
import * as React from 'react';
import { Library, Loader2, Plus, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { knowledgeUniverse, type KuItem } from '@chayuan/api';
import { useQuery } from '@tanstack/react-query';
import { saveNoteToKB } from './saveNoteToKB';
import { notifySuccess, reportError } from '../../store/errorDialog';

export interface SaveNoteDialogProps {
  open: boolean;
  title: string;
  content: any;
  /** AI 摘要 markdown(可选)— 传了就嵌进保存的 .md 头部,KB 检索时可见 */
  summary?: string;
  onClose(): void;
  onSaved(kbName: string): void;
  /** 默认选中(回填上次的库名;localStorage 持久化) */
  defaultKbName?: string;
}

export const SaveNoteDialog: React.FC<SaveNoteDialogProps> = ({
  open, title, content, summary, onClose, onSaved, defaultKbName,
}) => {
  const [selected, setSelected] = React.useState<string>(defaultKbName ?? '');
  const [saving, setSaving] = React.useState(false);
  const { data: kbs, isLoading } = useQuery({
    queryKey: ['kbs', 'document', 'for-save-note'],
    queryFn: () => knowledgeUniverse.list('document'),
    enabled: open,
  });
  const docKbs = (kbs ?? []).filter((k) => k.kind === 'document');

  React.useEffect(() => {
    if (!open) return;
    if (!selected && docKbs.length > 0) setSelected(docKbs[0]!.ku_id);
  }, [open, docKbs, selected]);

  const doSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      // ku_id 形如 'doc:my-kb';保存调 upload_docs 用 raw kbName 即 ku_id 去掉 'doc:'
      const kbName = selected.startsWith('doc:') ? selected.slice(4) : selected;
      const r = await saveNoteToKB({ title, content, kbName, summary });
      if (r.saved_files.length === 0 && Object.keys(r.failed).length > 0) {
        throw new Error(Object.values(r.failed)[0] ?? '保存失败');
      }
      try { localStorage.setItem('chayuan:notes:last-kb', selected); } catch {/* ignore */}
      notifySuccess(`已保存到「${kbName}」`);
      onSaved(kbName);
    } catch (e) {
      reportError(e, '保存笔记失败');
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        className="w-full max-w-md rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold">保存到知识中心</h3>
          <button onClick={onClose} className="text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-xs text-[var(--cy-text-tertiary)]">
          选择目标文档知识库;笔记会作为 markdown 文件入库,可被检索。
        </p>
        <div className="mb-4 max-h-72 overflow-auto rounded-xl border border-[var(--cy-border-subtle)]">
          {isLoading ? (
            <div className="flex items-center justify-center p-8 text-xs text-[var(--cy-text-tertiary)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载知识库列表…
            </div>
          ) : docKbs.length === 0 ? (
            <div className="p-6 text-center text-xs text-[var(--cy-text-tertiary)]">
              暂无文档知识库,请先创建一个。
              <div className="mt-3">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    onClose();
                    window.location.hash = '#/kb';
                  }}
                >
                  <Plus className="mr-1 h-3 w-3" /> 去新建
                </Button>
              </div>
            </div>
          ) : (
            <ul>
              {docKbs.map((k: KuItem) => (
                <li key={k.ku_id}>
                  <button
                    type="button"
                    onClick={() => setSelected(k.ku_id)}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition',
                      selected === k.ku_id
                        ? 'bg-[var(--cy-brand-500)]/10 text-[var(--cy-brand-700)]'
                        : 'hover:bg-[var(--cy-surface-1)]',
                    )}
                  >
                    <Library className="h-4 w-4 shrink-0 text-[var(--cy-text-tertiary)]" />
                    <span className="flex-1 truncate">{k.display_name ?? k.name ?? k.ku_id}</span>
                    {selected === k.ku_id && <span className="text-xs">✓</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" onClick={doSave} disabled={!selected || saving}>
            {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            保存
          </Button>
        </div>
      </div>
    </div>
  );
};
