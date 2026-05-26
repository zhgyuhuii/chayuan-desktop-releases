/**
 * 顶部 segmented:推荐 / 各 platform_name / 添加厂商 / 管理。
 *
 * 重写而不复用 SegmentedTabs 的原因:
 *   - 设计稿要求溢出时有 ›(下拉)入口
 *   - "添加 / 管理" 不是普通 tab,是工具按钮(右侧),需要分两段
 */

import { cn } from '@chayuan/ui';
import { ChevronRight, Plus, Settings as SettingsIcon } from 'lucide-react';
import * as React from 'react';

export interface VendorTabItem {
  value: string;
  label: string;
  /** 仅 dimmed 厂商在 tab 上轻微下沉色 */
  dim?: boolean;
}

export interface VendorTabsProps {
  items: VendorTabItem[];
  active: string;
  onChange(value: string): void;
  /** 「+ 添加厂商」按钮(admin 才传) */
  onCreate?(): void;
  /** 「管理」按钮(预留:总览所有平台 / 跳到全列表)*/
  onManage?(): void;
  /** 推荐位 tab 的固定 value */
  recommendedValue?: string;
  recommendedLabel?: string;
}

/** 可视 tabs 数;超出走 「›」 dropdown */
const VISIBLE_BEFORE_OVERFLOW = 7;

export const VendorTabs: React.FC<VendorTabsProps> = ({
  items,
  active,
  onChange,
  onCreate,
  onManage,
  recommendedValue = '__recommended__',
  recommendedLabel = '推荐',
}) => {
  const visible = items.slice(0, VISIBLE_BEFORE_OVERFLOW);
  const overflow = items.slice(VISIBLE_BEFORE_OVERFLOW);
  const [overflowOpen, setOverflowOpen] = React.useState(false);

  // 当前 active 不在 visible 时,把它顶上去
  const reorderedVisible = React.useMemo(() => {
    if (visible.some((v) => v.value === active)) return visible;
    const inOverflow = overflow.find((v) => v.value === active);
    if (!inOverflow) return visible;
    return [inOverflow, ...visible.slice(0, VISIBLE_BEFORE_OVERFLOW - 1)];
  }, [visible, overflow, active]);
  const finalOverflow = React.useMemo(() => {
    return overflow.filter((v) => !reorderedVisible.some((rv) => rv.value === v.value));
  }, [overflow, reorderedVisible]);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* 推荐 */}
      <TabBtn
        active={active === recommendedValue}
        onClick={() => onChange(recommendedValue)}
        emphasized
      >
        {recommendedLabel}
      </TabBtn>

      {reorderedVisible.map((tab) => (
        <TabBtn
          key={tab.value}
          active={active === tab.value}
          dim={tab.dim}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </TabBtn>
      ))}

      {/* 溢出 dropdown */}
      {finalOverflow.length > 0 && (
        <div className="relative">
          <button
            type="button"
            onClick={() => setOverflowOpen((v) => !v)}
            aria-label="更多厂商"
            className={cn(
              'inline-flex h-8 w-8 items-center justify-center rounded-full text-[var(--cy-text-secondary)] transition-colors',
              'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
              overflowOpen && 'bg-[var(--cy-surface-2)] text-[var(--cy-text-primary)]',
            )}
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          {overflowOpen && (
            <div
              className="absolute right-0 top-full z-30 mt-1 w-44 overflow-hidden rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] shadow-[var(--cy-shadow-md)]"
              onMouseLeave={() => setOverflowOpen(false)}
            >
              {finalOverflow.map((tab) => (
                <button
                  key={tab.value}
                  type="button"
                  onClick={() => {
                    onChange(tab.value);
                    setOverflowOpen(false);
                  }}
                  className={cn(
                    'flex w-full items-center px-3 py-1.5 text-left text-xs',
                    active === tab.value
                      ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                      : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-1)] hover:text-[var(--cy-text-primary)]',
                    tab.dim && 'opacity-60',
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 右侧工具按钮组 */}
      <div className="ml-auto flex items-center gap-1.5">
        {onCreate && (
          <TabBtn outline onClick={onCreate}>
            <Plus className="h-3.5 w-3.5" />
            添加厂商
          </TabBtn>
        )}
        {onManage && (
          <TabBtn outline onClick={onManage}>
            <SettingsIcon className="h-3.5 w-3.5" />
            管理
          </TabBtn>
        )}
      </div>
    </div>
  );
};

const TabBtn: React.FC<{
  active?: boolean;
  emphasized?: boolean;
  outline?: boolean;
  dim?: boolean;
  onClick(): void;
  children: React.ReactNode;
}> = ({ active, emphasized, outline, dim, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      'inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-xs transition-colors',
      active && emphasized && 'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)]',
      active && !emphasized && 'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)]',
      !active &&
        outline &&
        'border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      !active &&
        !outline &&
        'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      dim && !active && 'opacity-60',
    )}
  >
    {children}
  </button>
);
