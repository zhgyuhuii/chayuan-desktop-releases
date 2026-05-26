/**
 * McpCard —— MCP 连接卡片(对齐 KbCard 设计语言)。
 *
 * 三行布局:
 *   ① 状态点 + server_name(粗)
 *   ② 描述 line-clamp-3
 *   ③ transport chip(stdio/sse)+ args 数 + 启用/禁用 toggle
 *
 * 行为:
 *   - 卡片主体点击:打开详情 / 切换启用状态(由父级决定)
 *   - 右上角 toggle:启用/禁用,不冒泡到 onOpen
 */

import * as React from 'react';
import { Plug, Power, PowerOff } from 'lucide-react';
import { cn } from '@chayuan/ui';
import type { McpConnection } from '@chayuan/api';

const TRANSPORT_TINT: Record<string, string> = {
  stdio: 'bg-violet-50 text-violet-700',
  sse: 'bg-sky-50 text-sky-700',
  websocket: 'bg-emerald-50 text-emerald-700',
};

export interface McpCardProps {
  conn: McpConnection;
  onOpen(): void;
  onToggleEnabled(enabled: boolean): void;
  index?: number;
}

export const McpCard: React.FC<McpCardProps> = ({ conn, onOpen, onToggleEnabled, index = 0 }) => {
  const delay = Math.min(index, 15) * 25;
  const transport = (conn.transport || 'stdio').toLowerCase();
  const tint = TRANSPORT_TINT[transport] ?? TRANSPORT_TINT.stdio;
  return (
    <div
      style={{ animation: `mpCardIn .35s ease-out ${delay}ms backwards` }}
      className={cn(
        'group relative flex h-44 flex-col overflow-hidden rounded-xl border bg-[var(--cy-surface-base)] text-left transition-all duration-200',
        'hover:-translate-y-0.5 hover:shadow-[var(--cy-shadow-md)]',
        conn.enabled
          ? 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)]'
          : 'border-[var(--cy-border-subtle)] opacity-70 hover:opacity-90',
      )}
    >
      {/* 启用 toggle(右上,独立点击区) */}
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggleEnabled(!conn.enabled); }}
        title={conn.enabled ? '禁用' : '启用'}
        aria-label={conn.enabled ? '禁用' : '启用'}
        className={cn(
          'absolute right-2 top-2 z-10 inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors',
          conn.enabled
            ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
            : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
        )}
      >
        {conn.enabled ? <Power className="h-3.5 w-3.5" /> : <PowerOff className="h-3.5 w-3.5" />}
      </button>

      <button type="button" onClick={onOpen} className="flex h-full w-full flex-col px-4 pt-3.5 pb-3 text-left">
        {/* 第 1 行:icon + 标题 + 状态点 */}
        <div className="flex items-start gap-2">
          <span className="mt-0.5 inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-violet-50 text-violet-600">
            <Plug className="h-3.5 w-3.5" />
          </span>
          <p className="min-w-0 flex-1 truncate pr-7 text-sm font-medium text-[var(--cy-text-primary)]">
            {conn.server_name}
          </p>
          <span
            className={cn(
              'mt-1 inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full',
              conn.enabled ? 'bg-emerald-500' : 'bg-[var(--cy-text-tertiary)]',
            )}
            aria-label={conn.enabled ? '已启用' : '已禁用'}
          />
        </div>

        {/* 第 2 行:描述 / args 摘要 */}
        <p className="mt-2 line-clamp-3 flex-1 text-[12px] leading-relaxed text-[var(--cy-text-secondary)]">
          {conn.description ||
            (conn.args && conn.args.length > 0
              ? conn.args.join(' ').slice(0, 120)
              : '—')}
        </p>

        {/* 第 3 行:transport + args 数 + 创建日期 */}
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-[var(--cy-text-tertiary)]">
          <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider', tint)}>
            {transport}
          </span>
          <span className="inline-flex items-center gap-2 opacity-80">
            {(conn.args?.length ?? 0) > 0 && <span>{conn.args.length} args</span>}
            {conn.timeout > 0 && <span>{conn.timeout}s</span>}
            {conn.create_time && <span>{formatDate(conn.create_time)}</span>}
          </span>
        </div>
      </button>
    </div>
  );
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
}
