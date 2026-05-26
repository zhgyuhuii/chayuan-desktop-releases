/**
 * ToolCard —— 工具卡片(对齐 KbCard / McpCard 设计语言)。
 *
 * P2 升级:从只读 ``ToolInfo`` 切到带字段 schema 的 ``ToolCard``(后端 catalog),
 * 卡片右上角加 [⚙️ 配置] 浮按钮:hover 时显出,点击打开 ToolConfigDialog。
 *
 * 三行:
 *   ① 工具图标 + key(等宽小标签)+ 选中标
 *   ② title / description line-clamp-3
 *   ③ category 色块 + 字段数 + (启用)状态点
 */

import * as React from 'react';
import { CheckCheck, Settings, Wrench } from 'lucide-react';
import { cn } from '@chayuan/ui';
import type { ToolCard as ToolCardData } from '@chayuan/api';

export interface ToolCardProps {
  tool: ToolCardData;
  selected: boolean;
  index?: number;
  onToggle(): void;
  onOpenConfig(): void;
}

/** 由 catalog.category 推一个色块。所有未识别都走 slate(中性). */
const CATEGORY_TINT: Record<string, string> = {
  rag: 'bg-emerald-50 text-emerald-700',
  web: 'bg-cyan-50 text-cyan-700',
  math: 'bg-violet-50 text-violet-700',
  media: 'bg-rose-50 text-rose-700',
  data: 'bg-amber-50 text-amber-700',
  location: 'bg-sky-50 text-sky-700',
  code: 'bg-indigo-50 text-indigo-700',
  message: 'bg-pink-50 text-pink-700',
  integration: 'bg-teal-50 text-teal-700',
  system: 'bg-slate-100 text-slate-700',
  // P3:用户自定义 HTTP 工具(custom_tools.yaml)— 与内置分类区分
  custom: 'bg-fuchsia-50 text-fuchsia-700',
};

const CATEGORY_LABEL: Record<string, string> = {
  rag: 'RAG',
  web: 'WEB',
  math: 'MATH',
  media: 'MEDIA',
  data: 'DATA',
  location: 'GEO',
  code: 'CODE',
  message: 'MSG',
  integration: 'APP',
  system: 'SYS',
  custom: 'CUSTOM',
};

export const ToolCard: React.FC<ToolCardProps> = ({ tool, selected, index = 0, onToggle, onOpenConfig }) => {
  const tint = CATEGORY_TINT[tool.category] ?? CATEGORY_TINT.system;
  const catLabel = CATEGORY_LABEL[tool.category] ?? tool.category.toUpperCase();
  const delay = Math.min(index, 15) * 25;
  const desc = tool.description_override?.trim() || tool.description || tool.summary || '—';
  return (
    <div
      style={{ animation: `mpCardIn .35s ease-out ${delay}ms backwards` }}
      className={cn(
        'group relative flex h-44 flex-col overflow-hidden rounded-xl border bg-[var(--cy-surface-base)] text-left transition-all duration-200',
        'hover:-translate-y-0.5 hover:shadow-[var(--cy-shadow-md)]',
        selected
          ? 'border-[var(--cy-brand-500)] shadow-[var(--cy-shadow-md)] ring-1 ring-[var(--cy-brand-300)]'
          : 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)]',
      )}
    >
      {/* 主点击区:整张卡触发"加入/移出"选中态(对话页 composer 用) */}
      <button
        type="button"
        onClick={onToggle}
        className="flex h-full w-full flex-col px-4 pt-3.5 pb-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cy-brand-500)]"
      >
        {/* 第 1 行:icon + key + 选中角标 */}
        <div className="flex items-start gap-2">
          <span className={cn('mt-0.5 inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md', tint)}>
            <Wrench className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate pr-14 font-mono text-[12px] font-medium text-[var(--cy-text-primary)]">
              {tool.key}
            </p>
            {tool.title && tool.title !== tool.key && (
              <p className="truncate pr-14 text-[11px] text-[var(--cy-text-tertiary)]">{tool.title}</p>
            )}
          </div>
        </div>

        {/* 第 2 行:description */}
        <p className="mt-2 line-clamp-3 flex-1 text-[12px] leading-relaxed text-[var(--cy-text-secondary)]">
          {desc}
        </p>

        {/* 第 3 行:category + 字段数 + 启用点 */}
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-[var(--cy-text-tertiary)]">
          <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold', tint)}>
            {catLabel}
          </span>
          <span className="flex items-center gap-2">
            {tool.fields.length > 0 && <span>{tool.fields.length} 字段</span>}
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                tool.enabled
                  ? 'bg-emerald-50 text-emerald-700'
                  : 'bg-[var(--cy-surface-2)] text-[var(--cy-text-tertiary)]',
              )}
              title={tool.enabled ? '已启用' : '未启用'}
            >
              <span
                aria-hidden
                className={cn('h-1.5 w-1.5 rounded-full', tool.enabled ? 'bg-emerald-500' : 'bg-[var(--cy-text-tertiary)]/60')}
              />
              {tool.enabled ? '启用' : '停用'}
            </span>
          </span>
        </div>
      </button>

      {/* 角标:选中态 + 配置浮按钮(右上角并排) */}
      <div className="pointer-events-none absolute right-2 top-2 flex items-center gap-1.5">
        {selected && (
          <span
            aria-hidden
            className="inline-flex h-5 w-5 items-center justify-center rounded bg-[var(--cy-brand-600)] text-white"
          >
            <CheckCheck className="h-3 w-3" />
          </span>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpenConfig();
          }}
          aria-label="配置工具"
          title="配置(启用 / 字段值 / 描述覆盖)"
          className={cn(
            'pointer-events-auto inline-flex h-6 w-6 items-center justify-center rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] text-[var(--cy-text-tertiary)] shadow-sm transition-all',
            'opacity-0 group-hover:opacity-100 hover:border-[var(--cy-brand-300)] hover:text-[var(--cy-brand-600)]',
          )}
        >
          <Settings className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
};
