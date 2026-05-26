/**
 * 单个工具的配置弹窗。
 *
 * 行为:
 *  - 顶部:title + summary + 启用开关 + 描述覆盖输入
 *  - 中部:catalog.fields 用 FieldRenderer 平铺渲染 + 受控本地草稿
 *  - 底部:[文档] [取消] [保存]
 *
 * 草稿模型:
 *   draft 从 props.card 拷一份,用户改动只动 draft;[保存] 才把变化的字段
 *   diff 出来 PUT 后端;diff 计算用 ``card.fields[i].value`` 作 baseline,
 *   未改动的字段不会出现在 update.values 里(后端按"只更新传过来的"语义,
 *   避免把默认值刷回 yaml 顶掉用户私改)。
 *
 * 复用:
 *   - 字段渲染走共享的 FieldRenderer(text/textarea/number/switch/select/...)
 *   - update mutation 走共享的 useUpdateToolConfig(invalidate catalog)
 */
import * as React from 'react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Switch,
  Textarea,
  cn,
} from '@chayuan/ui';
import { ExternalLink, Save } from 'lucide-react';
import type { ToolCard } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { notifyInfo, notifySuccess, reportError } from '../../../store/errorDialog';
import { useUpdateToolConfig } from '../hooks';
import { FieldRenderer } from './FieldRenderer';

export interface ToolConfigDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  card: ToolCard | null;
}

export const ToolConfigDialog: React.FC<ToolConfigDialogProps> = ({ open, onOpenChange, card }) => {
  const updateMut = useUpdateToolConfig();

  // 弹窗每次打开把当前后端值快照到草稿;关闭后扔
  const [draftEnabled, setDraftEnabled] = React.useState(false);
  const [draftDesc, setDraftDesc] = React.useState('');
  const [draftValues, setDraftValues] = React.useState<Record<string, unknown>>({});

  React.useEffect(() => {
    if (!open || !card) return;
    setDraftEnabled(Boolean(card.enabled));
    setDraftDesc(card.description_override ?? '');
    const init: Record<string, unknown> = {};
    for (const f of card.fields) init[f.path] = f.value ?? f.default ?? null;
    setDraftValues(init);
  }, [open, card]);

  if (!card) return null;

  const setOne = (path: string, value: unknown) =>
    setDraftValues((prev) => ({ ...prev, [path]: value }));

  const onSave = async () => {
    if (!card) return;
    const original: Record<string, unknown> = {};
    for (const f of card.fields) original[f.path] = f.value ?? f.default ?? null;

    const dirty: Record<string, unknown> = {};
    for (const f of card.fields) {
      if (!_eq(draftValues[f.path], original[f.path])) {
        dirty[f.path] = draftValues[f.path];
      }
    }

    const update = {
      enabled: draftEnabled !== card.enabled ? draftEnabled : undefined,
      description_override:
        (draftDesc.trim() || null) !== (card.description_override ?? null)
          ? (draftDesc.trim() || null)
          : undefined,
      values: dirty,
    } as const;

    if (
      update.enabled === undefined &&
      update.description_override === undefined &&
      Object.keys(dirty).length === 0
    ) {
      notifyInfo('没有改动', '当前所有字段都与已保存值一致');
      onOpenChange(false);
      return;
    }

    try {
      await updateMut.mutateAsync({ key: card.key, update });
      notifySuccess('已保存', `${card.title} 的配置已立即生效`);
      onOpenChange(false);
    } catch (e) {
      reportError(e, `${card.title} 配置保存失败`);
    }
  };

  const onOpenDocs = () => {
    if (!card.docs_url) return;
    void getPlatform().shell.openExternal(card.docs_url);
  };

  const desc = card.description_override?.trim() || card.description;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-[min(720px,92vw)] flex-col overflow-hidden p-0">
        {/* 顶部:title + summary */}
        <header className="border-b border-[var(--cy-border-subtle)] px-5 py-4">
          <DialogTitle className="text-base font-semibold text-[var(--cy-text-primary)]">
            {card.title}
            <code className="ml-2 rounded bg-[var(--cy-surface-2)] px-1.5 py-0.5 text-[10px] font-mono font-normal text-[var(--cy-text-tertiary)]">
              {card.key}
            </code>
          </DialogTitle>
          <DialogDescription className="mt-1 line-clamp-2 text-xs text-[var(--cy-text-secondary)]">
            {card.summary || desc || '此工具暂无简介'}
          </DialogDescription>
          {card.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {card.tags.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center rounded-full bg-[var(--cy-surface-1)] px-2 py-0.5 text-[10px] text-[var(--cy-text-tertiary)] ring-1 ring-[var(--cy-border-subtle)]"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </header>

        {/* 中部:启用开关 + 描述覆盖 + 字段表单 */}
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="flex items-center gap-3 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
            <Switch
              checked={draftEnabled}
              onCheckedChange={(v) => setDraftEnabled(Boolean(v))}
            />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-[var(--cy-text-primary)]">启用此工具</p>
              <p className="text-[10px] text-[var(--cy-text-tertiary)]">
                启用后,LLM 在对话时会把它列入可调用工具集(use=true,写入 tool_settings.yaml)
              </p>
            </div>
          </div>

          <details className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium text-[var(--cy-text-primary)]">
              覆盖工具描述(给 LLM 看的提示词)
            </summary>
            <p className="mt-1 text-[10px] text-[var(--cy-text-tertiary)]">
              留空 → 用 catalog 自带描述。改了之后 LLM 选工具时会按你的版本读。
            </p>
            <Textarea
              value={draftDesc}
              onChange={(e) => setDraftDesc(e.target.value)}
              placeholder={card.description}
              rows={3}
              className="mt-2 text-xs leading-relaxed"
            />
          </details>

          {card.fields.length === 0 ? (
            <p className="rounded-md border border-dashed border-[var(--cy-border-subtle)] py-6 text-center text-xs text-[var(--cy-text-tertiary)]">
              此工具没有可配置字段(开启即用)
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {card.fields.map((f) => (
                <div
                  key={f.path}
                  className={cn(
                    f.widget === 'textarea' || f.widget === 'select' ? 'sm:col-span-2' : '',
                  )}
                >
                  <FieldRenderer
                    field={f}
                    value={draftValues[f.path]}
                    onChange={(v) => setOne(f.path, v)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 底部:文档链接 / 取消 / 保存 */}
        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-5 py-3">
          {card.docs_url ? (
            <Button size="sm" variant="ghost" onClick={onOpenDocs} className="text-xs">
              <ExternalLink className="h-3.5 w-3.5" /> 查看文档
            </Button>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button size="sm" onClick={() => void onSave()} disabled={updateMut.isPending}>
              <Save className="h-3.5 w-3.5" />
              {updateMut.isPending ? '保存中…' : '保存(立即生效)'}
            </Button>
          </div>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

/** 浅相等 — 工具配置值都是标量 / 字符串 / 简单 list,JSON 序列化够用 */
function _eq(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null) return a === b;
  if (typeof a !== typeof b) return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}
