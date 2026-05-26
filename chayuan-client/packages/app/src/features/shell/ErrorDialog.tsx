/**
 * 全局错误弹窗。挂在 Chrome 末尾,任意层 reportError() / useErrorDialog.show() 触发。
 *
 * - 单实例;并发错误进 queue
 * - 可展开查看原始 stack / response body
 * - autoDismissMs 时自动关
 */

import * as React from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, Info } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Button,
} from '@chayuan/ui';
import { useErrorDialog, type EntryKind } from '../../store/errorDialog';
import { useTranslation } from '../../i18n';

const KIND_STYLES: Record<EntryKind, { icon: React.ComponentType<{ className?: string }>; bg: string; fg: string }> = {
  error: {
    icon: AlertCircle,
    bg: 'bg-[var(--cy-danger-50,#fef2f2)]',
    fg: 'text-[var(--cy-danger-600,#dc2626)]',
  },
  info: {
    icon: Info,
    bg: 'bg-[var(--cy-brand-50)]',
    fg: 'text-[var(--cy-brand-600)]',
  },
  success: {
    icon: CheckCircle2,
    bg: 'bg-emerald-50',
    fg: 'text-emerald-600',
  },
};

export const ErrorDialogRoot: React.FC = () => {
  const { t } = useTranslation();
  const current = useErrorDialog((s) => s.current);
  const dismiss = useErrorDialog((s) => s.dismiss);
  const [showRaw, setShowRaw] = React.useState(false);

  React.useEffect(() => {
    setShowRaw(false);
    if (!current?.autoDismissMs) return;
    const t = window.setTimeout(dismiss, current.autoDismissMs);
    return () => window.clearTimeout(t);
  }, [current, dismiss]);

  if (!current) return null;

  const style = KIND_STYLES[current.kind] ?? KIND_STYLES.error;
  const Icon = style.icon;

  return (
    <Dialog open onOpenChange={(o) => !o && dismiss()}>
      <DialogContent className="max-w-md">
        <div className="flex items-start gap-3">
          <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full ${style.bg} ${style.fg}`}>
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <DialogTitle>{current.title}</DialogTitle>
            {/* 始终渲染 DialogDescription 以满足 Radix a11y(aria-describedby)要求;
                无 detail 时给一个 sr-only fallback,不影响视觉。 */}
            {current.detail ? (
              <DialogDescription className="mt-1 break-words">
                {current.detail}
              </DialogDescription>
            ) : (
              <DialogDescription className="sr-only">{current.title}</DialogDescription>
            )}
            {current.raw && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setShowRaw((v) => !v)}
                  className="inline-flex items-center gap-1 text-xs text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]"
                >
                  <ChevronDown
                    className={`h-3 w-3 transition-transform ${showRaw ? 'rotate-180' : ''}`}
                  />
                  {showRaw ? '收起详情' : '查看详情'}
                </button>
                {showRaw && (
                  <pre className="mt-2 max-h-48 overflow-auto rounded border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2 text-[11px] text-[var(--cy-text-secondary)]">
                    {current.raw}
                  </pre>
                )}
              </div>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button onClick={dismiss}>{t('common.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
