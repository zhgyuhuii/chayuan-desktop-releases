/**
 * KbAskComposer — 详情页内的"试问"输入条,被三个详情面板复用。
 *
 * 设计:
 *  - 单行输入框 + 发送按钮(Enter 发送 / Shift+Enter 换行 / Esc 取消)
 *  - pending 时按钮变为"取消"图标,文案区显示当前 stage("生成 SQL…")
 *  - 聚焦渐变光环(brand 色),hover 微抬升
 *  - 极薄一层(56px),不抢主内容区高度
 */

import * as React from 'react';
import { Loader2, Send, Sparkles, Square } from 'lucide-react';
import { cn } from '@chayuan/ui';

export interface KbAskComposerProps {
  placeholder?: string;
  /** 当前 stage 的中文文案(为空就显示 placeholder) */
  stageLabel?: string;
  /** 受控 value(可选) */
  value?: string;
  onChange?(v: string): void;
  /** 提交回调,接收 trim 后的字符串 */
  onSubmit(query: string): void | Promise<void>;
  /** pending 时点击 = 取消;否则不渲染该按钮 */
  onCancel?(): void;
  isPending?: boolean;
  disabled?: boolean;
  /** 提示性建议词,鼠标点击填充输入框(常用于结构化:示例 SQL,向量:集合 name) */
  suggestions?: string[];
  /** 自定义类名(外层容器) */
  className?: string;
}

export const KbAskComposer: React.FC<KbAskComposerProps> = ({
  placeholder = '换个问题问问看…',
  stageLabel = '',
  value,
  onChange,
  onSubmit,
  onCancel,
  isPending = false,
  disabled = false,
  suggestions = [],
  className,
}) => {
  const [internal, setInternal] = React.useState('');
  const isControlled = value != null;
  const v = isControlled ? value : internal;
  const setV = (s: string) => {
    if (!isControlled) setInternal(s);
    onChange?.(s);
  };
  const inputRef = React.useRef<HTMLInputElement>(null);

  const submit = () => {
    const q = (v ?? '').trim();
    if (!q || disabled) return;
    void onSubmit(q);
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    } else if (e.key === 'Escape' && isPending) {
      e.preventDefault();
      onCancel?.();
    }
  };

  return (
    <div className={cn('flex w-full flex-col gap-2', className)}>
      <div
        className={cn(
          'group relative flex h-11 items-center gap-2 rounded-full border bg-[var(--cy-surface-base)] pl-4 pr-1.5 transition-all',
          isPending
            ? 'border-[var(--cy-brand-400)] shadow-[0_0_0_3px_var(--cy-brand-50)]'
            : 'border-[var(--cy-border-subtle)] focus-within:border-[var(--cy-brand-400)] focus-within:shadow-[0_0_0_3px_var(--cy-brand-50)]',
          disabled && 'opacity-60',
        )}
      >
        <Sparkles
          className={cn(
            'h-4 w-4 flex-shrink-0 transition-colors',
            isPending ? 'text-[var(--cy-brand-500)]' : 'text-[var(--cy-text-tertiary)] group-focus-within:text-[var(--cy-brand-500)]',
          )}
        />
        <input
          ref={inputRef}
          type="text"
          value={v}
          disabled={disabled}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={onKey}
          placeholder={isPending && stageLabel ? stageLabel : placeholder}
          className="h-full min-w-0 flex-1 bg-transparent text-sm text-[var(--cy-text-primary)] placeholder:text-[var(--cy-text-tertiary)] focus-visible:outline-none"
        />
        {isPending ? (
          <button
            type="button"
            onClick={onCancel}
            aria-label="取消"
            title="取消(Esc)"
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)] transition-colors hover:bg-[var(--cy-brand-100)]"
          >
            <Square className="h-3.5 w-3.5 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={disabled || !(v ?? '').trim()}
            aria-label="发送"
            title="发送(Enter)"
            className={cn(
              'flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full transition-all',
              (v ?? '').trim() && !disabled
                ? 'bg-[var(--cy-ink-700)] text-white hover:bg-[var(--cy-ink-800)] hover:scale-105'
                : 'bg-[var(--cy-surface-2)] text-[var(--cy-text-tertiary)]',
            )}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* 进度条:pending 时渐变扫描 */}
      {isPending && (
        <div className="relative h-0.5 w-full overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
          <div className="absolute inset-y-0 w-1/3 animate-[kbAskScan_1.4s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-[var(--cy-brand-500)] to-transparent" />
        </div>
      )}
      {isPending && stageLabel && (
        <div className="flex items-center gap-1.5 text-[11px] text-[var(--cy-brand-700)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          <span>{stageLabel}</span>
        </div>
      )}

      {/* 建议词 chips */}
      {!isPending && suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.slice(0, 4).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setV(s);
                requestAnimationFrame(() => inputRef.current?.focus());
              }}
              className="inline-flex h-6 items-center rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2.5 text-[11px] text-[var(--cy-text-secondary)] transition-colors hover:border-[var(--cy-brand-300)] hover:bg-[var(--cy-brand-50)] hover:text-[var(--cy-brand-700)]"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* 全局 keyframes 由 design-tokens 提供;若没有走 inline 样式 */}
      <style>{`@keyframes kbAskScan{0%{transform:translateX(-100%)}100%{transform:translateX(400%)}}`}</style>
    </div>
  );
};
