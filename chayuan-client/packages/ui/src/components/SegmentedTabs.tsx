import * as React from 'react';
import { cn } from '../lib/cn';
import { Pill } from './Pill';

/**
 * SegmentedTabs —— 一行胶囊式 Tab,用于"推荐 / 工作 / 学习" 等横向分类。
 *
 * 设计稿对应:模型广场厂商 Tab、AI Space 分类、AI 写作分类、KB 推荐/全部。
 *
 * 横向滚动:items 多时启用 overflow-x;不显示滚动条,触摸/滚轮可滑。
 */

export interface SegmentedItem {
  /** 稳定值;onChange 返回此值 */
  value: string;
  /** 显示文本(可以是 i18n key,由调用方 t() 后传入) */
  label: React.ReactNode;
  /** 可选 leading icon */
  icon?: React.ReactNode;
  /** 禁用 */
  disabled?: boolean;
}

export interface SegmentedTabsProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange'> {
  items: ReadonlyArray<SegmentedItem>;
  value: string;
  onChange(value: string): void;
  /** 末尾 trailing slot,通常放"管理"按钮 */
  trailing?: React.ReactNode;
  size?: 'sm' | 'md';
}

export const SegmentedTabs = React.forwardRef<HTMLDivElement, SegmentedTabsProps>(
  ({ items, value, onChange, trailing, size = 'md', className, ...rest }, ref) => {
    return (
      <div
        ref={ref}
        role="tablist"
        className={cn(
          'flex items-center gap-2 overflow-x-auto whitespace-nowrap',
          '[scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden',
          className,
        )}
        {...rest}
      >
        {items.map((it) => (
          <Pill
            key={it.value}
            role="tab"
            aria-selected={it.value === value}
            disabled={it.disabled}
            active={it.value === value}
            tone={it.value === value ? 'ink' : 'ghost'}
            size={size}
            onClick={() => onChange(it.value)}
          >
            {it.icon}
            {it.label}
          </Pill>
        ))}
        {trailing ? <div className="ml-auto flex-shrink-0">{trailing}</div> : null}
      </div>
    );
  },
);
SegmentedTabs.displayName = 'SegmentedTabs';
