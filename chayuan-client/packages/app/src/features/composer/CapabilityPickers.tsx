/**
 * CapabilityPickers —— Composer 内置的工具 / MCP 选择器。
 *
 * 设计:
 *   - 两枚 Pill(工具 / MCP)挂在 ChatComposer 底栏右侧;
 *   - 已选数量直接显示在 pill 角标(借用 outline tone + 角标变 ink 表选中);
 *   - 点击展开 Popover:搜索框 + 单列 list,多选 + 高亮;
 *   - 知识库不在此处出现 —— KB 是另一种对话模式,通过 Sidebar / `/kb` 入口进入,
 *     避免和"通用对话能力"概念混淆(参考 ChatGPT/Claude 设计语义)。
 *
 * 状态来源:useComposerStore(selectedTools / selectedMcps);
 * 数据来源:useTools / useMcps(60s TTL,见 useCatalog)。
 */

import * as React from 'react';
import { Check, Plug, Search, Wrench, X } from 'lucide-react';
import {
  cn,
  Pill,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@chayuan/ui';
import { useTools, useMcps, type McpCardItem, type ToolCardItem } from '../catalog/useCatalog';
import { useComposerStore, useComposerS } from '../../store/composer';
import { useTranslation } from '../../i18n';

type Item = (ToolCardItem | McpCardItem) & { description: string };

interface PickerProps<T extends Item> {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  emptyKey: string;
  items: T[];
  loading: boolean;
  selected: string[];
  onChange(next: string[]): void;
}

function GenericPicker<T extends Item>({
  label,
  icon: Icon,
  emptyKey,
  items,
  loading,
  selected,
  onChange,
}: PickerProps<T>) {
  const { t } = useTranslation();
  const [q, setQ] = React.useState('');
  const filtered = React.useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return items;
    return items.filter(
      (i) =>
        i.title.toLowerCase().includes(kw) ||
        i.description.toLowerCase().includes(kw) ||
        i.id.toLowerCase().includes(kw),
    );
  }, [items, q]);

  const count = selected.length;
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Pill
          tone={count > 0 ? 'soft' : 'outline'}
          size="sm"
          aria-label={label}
          aria-pressed={count > 0}
        >
          <Icon className="h-3.5 w-3.5" />
          <span>{label}</span>
          {count > 0 && (
            <span className="ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--cy-brand-600)] px-1 text-[10px] font-semibold text-white">
              {count}
            </span>
          )}
        </Pill>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0" sideOffset={6}>
        <div className="flex items-center gap-2 border-b border-[var(--cy-border-subtle)] px-3 py-2">
          <Search className="h-3.5 w-3.5 text-[var(--cy-text-tertiary)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('common.search')}
            className="flex-1 bg-transparent text-xs text-[var(--cy-text-primary)] placeholder:text-[var(--cy-text-tertiary)] focus-visible:outline-none"
          />
          {q && (
            <button
              type="button"
              onClick={() => setQ('')}
              className="text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]"
              aria-label={t('common.clear')}
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>

        <div className="max-h-72 overflow-y-auto py-1">
          {loading && items.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">
              {t('common.loading')}
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">
              {t(emptyKey)}
            </div>
          )}
          {filtered.map((it) => {
            const active = selected.includes(it.id);
            return (
              <button
                key={it.id}
                type="button"
                onClick={() => toggle(it.id)}
                className={cn(
                  'group flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors',
                  'hover:bg-[var(--cy-surface-2)]',
                  active && 'bg-[var(--cy-brand-50)]',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border',
                    active
                      ? 'border-[var(--cy-brand-600)] bg-[var(--cy-brand-600)] text-white'
                      : 'border-[var(--cy-border-default)] bg-[var(--cy-surface-base)]',
                  )}
                >
                  {active && <Check className="h-3 w-3" />}
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-xs font-medium text-[var(--cy-text-primary)]">
                    {it.title}
                  </span>
                  {it.description && (
                    <span className="truncate text-[11px] text-[var(--cy-text-tertiary)]">
                      {it.description}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>

        {count > 0 && (
          <div className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] px-3 py-1.5 text-[11px] text-[var(--cy-text-tertiary)]">
            <span>
              {t('common.selectedCount', { count })}
            </span>
            <button
              type="button"
              onClick={() => onChange([])}
              className="hover:text-[var(--cy-text-primary)]"
            >
              {t('common.clear')}
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

export const ToolsPickerPill: React.FC = () => {
  const { t } = useTranslation();
  const tools = useTools();
  const selected = useComposerS((s) => s.selectedTools);
  const setTools = useComposerS((s) => s.setTools);
  return (
    <GenericPicker<ToolCardItem>
      label={t('chat.toolsPicker')}
      icon={Wrench}
      emptyKey="chat.noToolAvailable"
      items={tools.data ?? []}
      loading={tools.isLoading}
      selected={selected}
      onChange={setTools}
    />
  );
};

export const McpPickerPill: React.FC = () => {
  const { t } = useTranslation();
  const mcps = useMcps();
  const selected = useComposerS((s) => s.selectedMcps);
  const setMcps = useComposerS((s) => s.setMcps);
  return (
    <GenericPicker<McpCardItem>
      label={t('chat.mcpPicker')}
      icon={Plug}
      emptyKey="chat.noMcpAvailable"
      items={mcps.data ?? []}
      loading={mcps.isLoading}
      selected={selected}
      onChange={setMcps}
    />
  );
};
