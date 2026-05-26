/**
 * 本地 LLM runtime 状态色块:小尺寸 pill,显示运行状态 + 颜色,hover 显示 tooltip。
 *
 * 状态色:
 *   ready       → 绿
 *   starting    → 蓝(脉冲)
 *   restarting  → 蓝(脉冲)
 *   stopped     → 灰
 *   failed      → 红
 */

import * as React from 'react';
import { cn } from '@chayuan/ui';
import type { LocalRuntimeState, LocalRuntimeStatus } from '@chayuan/api';

const STATE_LABEL: Record<LocalRuntimeState, string> = {
  ready: '运行中',
  starting: '启动中',
  restarting: '重启中',
  stopped: '已停止',
  failed: '失败',
};

const STATE_COLOR: Record<LocalRuntimeState, string> = {
  ready: 'bg-emerald-500/15 text-emerald-700 border-emerald-500/30',
  starting: 'bg-sky-500/15 text-sky-700 border-sky-500/30 animate-pulse',
  restarting: 'bg-sky-500/15 text-sky-700 border-sky-500/30 animate-pulse',
  stopped: 'bg-zinc-500/15 text-zinc-600 border-zinc-500/30',
  failed: 'bg-rose-500/15 text-rose-700 border-rose-500/30',
};

export interface LocalRuntimeStatusBadgeProps {
  status: LocalRuntimeStatus | null;
  className?: string;
}

export const LocalRuntimeStatusBadge: React.FC<LocalRuntimeStatusBadgeProps> = ({
  status,
  className,
}) => {
  const state = status?.state ?? 'stopped';
  const label = STATE_LABEL[state];
  const tooltip =
    status?.last_error && state === 'failed'
      ? status.last_error
      : status?.endpoint && state === 'ready'
        ? status.endpoint
        : label;

  return (
    <span
      title={tooltip}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium',
        STATE_COLOR[state],
        className,
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          state === 'ready' && 'bg-emerald-500',
          (state === 'starting' || state === 'restarting') && 'bg-sky-500',
          state === 'stopped' && 'bg-zinc-400',
          state === 'failed' && 'bg-rose-500',
        )}
      />
      {label}
    </span>
  );
};
