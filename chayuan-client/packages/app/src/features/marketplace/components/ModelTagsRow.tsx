/**
 * 模型标签行 — 一个厂商卡片下方的模型 chip 列表。
 *
 * 设计:
 *   - 默认展示前 maxVisible(默认 3)个;超出走「更多 ▼」DropdownMenu
 *   - 选中态实心:brand-100 底 + brand-700 文字
 *   - 在 dropdown 内选中后,自动把该 chip 顶到 visible 段并高亮(通过排序参数控制)
 *   - 父级控件可传 disabled 让禁用厂商的标签也禁用(不可点)
 */

import type { ModelMetadata } from '@chayuan/api';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  cn,
} from '@chayuan/ui';
import { ChevronDown } from 'lucide-react';
import * as React from 'react';

export interface ModelTagsRowProps {
  /** 全部模型 id(已按推荐 / 字典序排好) */
  models: string[];
  /** 当前选中的模型 id(全局共享) */
  activeModel?: string;
  /** 点击 chip 触发 */
  onSelect(model: string): void;
  maxVisible?: number;
  disabled?: boolean;
  className?: string;
  /**
   * 该厂商模型 id → 元信息 的索引(catalog 接口顺带返回);
   * chip hover 时展示 description / release_date / context_length / performance_note。
   */
  metadata?: Record<string, ModelMetadata>;
}

export const ModelTagsRow: React.FC<ModelTagsRowProps> = React.memo(
  ({ models, activeModel, onSelect, maxVisible = 3, disabled = false, className, metadata }) => {
    // 选中模型若在溢出区,把它顶到 visible 段(让用户始终看见自己选的)
    const ordered = React.useMemo(() => {
      if (!activeModel || !models.includes(activeModel)) return models;
      if (models.indexOf(activeModel) < maxVisible) return models;
      const rest = models.filter((m) => m !== activeModel);
      return [activeModel, ...rest];
    }, [models, activeModel, maxVisible]);

    if (ordered.length === 0) return null;

    const visible = ordered.slice(0, maxVisible);
    const overflow = ordered.slice(maxVisible);

    return (
      <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
        {visible.map((m) => (
          <ModelChip
            key={m}
            model={m}
            active={m === activeModel}
            disabled={disabled}
            onClick={() => onSelect(m)}
            meta={metadata?.[m]}
          />
        ))}
        {overflow.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild disabled={disabled}>
              <button
                type="button"
                disabled={disabled}
                className={cn(
                  'inline-flex h-6 items-center gap-0.5 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2 text-[11px] text-[var(--cy-text-secondary)] transition-colors',
                  'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)] disabled:opacity-50',
                )}
              >
                <span>更多</span>
                <ChevronDown className="h-3 w-3" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-72 w-56 overflow-y-auto">
              {overflow.map((m) => (
                <DropdownMenuItem
                  key={m}
                  onClick={() => onSelect(m)}
                  className={cn(
                    m === activeModel && 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]',
                  )}
                >
                  <span className="truncate">{m}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    );
  },
);
ModelTagsRow.displayName = 'ModelTagsRow';

const ModelChip: React.FC<{
  model: string;
  active: boolean;
  disabled: boolean;
  onClick(): void;
  meta?: ModelMetadata;
}> = ({ model, active, disabled, onClick, meta }) => {
  // 鼠标悬停文案 —— 用 native title 简单可靠,不引入额外 popover 开销;
  // 字段空时省略,避免出现一堆"未知"。
  const tooltip = React.useMemo(() => {
    const parts: string[] = [model];
    if (meta?.description) parts.push(meta.description);
    const sub: string[] = [];
    if (meta?.release_date) sub.push(`发布:${meta.release_date}`);
    if (meta?.context_length) sub.push(`上下文:${meta.context_length.toLocaleString()} tokens`);
    if (meta?.performance_note) sub.push(meta.performance_note);
    if (sub.length) parts.push(sub.join(' · '));
    return parts.join('\n');
  }, [model, meta]);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={tooltip}
      className={cn(
        'inline-flex h-6 max-w-[140px] items-center rounded-full border px-2 text-[11px] transition-colors disabled:opacity-50',
        active
          ? 'border-[var(--cy-brand-300)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)] font-medium'
          : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      )}
    >
      <span className="truncate">{model}</span>
    </button>
  );
};
