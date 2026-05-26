import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../lib/cn';

/**
 * Pill —— 黑/白圆角胶囊按钮,用于 Tab 选中态、分类高亮、标签页等。
 *
 * 设计稿对应:
 *   - 选中:bg-cy-ink-700 文字白(#FFF)
 *   - 未选:透明底,文字 cy-text-secondary,hover 背景 cy-surface-2
 */

const pillVariants = cva(
  'inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-full text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cy-brand-500)] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      tone: {
        /** 黑色实心(选中态) */
        ink: 'bg-[var(--cy-ink-700)] text-white hover:bg-[var(--cy-ink-800)]',
        /** 蓝色实心(主 CTA / 推荐) */
        brand: 'bg-[var(--cy-brand-500)] text-white hover:bg-[var(--cy-brand-600)]',
        /** 浅蓝(次级选中,Sidebar 默认) */
        soft: 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)] hover:bg-[var(--cy-brand-100)]',
        /** 透明(未选中) */
        ghost:
          'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
        /** 白底带描边(参考图模型广场"管理 / 历史" 按钮) */
        outline:
          'border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] text-[var(--cy-text-primary)] hover:bg-[var(--cy-surface-2)]',
      },
      size: {
        sm: 'h-7 px-3 text-xs',
        md: 'h-9 px-4',
        lg: 'h-10 px-5',
      },
    },
    defaultVariants: { tone: 'ghost', size: 'md' },
  },
);

export interface PillProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof pillVariants> {
  active?: boolean;
}

export const Pill = React.forwardRef<HTMLButtonElement, PillProps>(
  ({ className, tone, size, active, ...props }, ref) => {
    // active 是语义糖:激活时强制走 ink tone(参考图分类选中)
    const effectiveTone = active ? 'ink' : tone;
    return (
      <button
        type="button"
        ref={ref}
        className={cn(pillVariants({ tone: effectiveTone, size }), className)}
        data-active={active ? 'true' : undefined}
        {...props}
      />
    );
  },
);
Pill.displayName = 'Pill';

export { pillVariants };
