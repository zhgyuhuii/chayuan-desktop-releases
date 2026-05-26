/**
 * 模态路由异步任务的进度条渲染。
 *
 * 数据源:v5 ``data-task-progress`` 事件,被 useModalitySubmit 落到
 * ``ChatMessage.taskProgress`` 字段。
 *
 * 视觉:
 *   - 顶行:消息 + ETA(若有)
 *   - 中间:线性 progress bar(emerald → blue 渐变);完成时变全绿
 *   - 底行小字:task_id(给排查用,鼠标悬停才显示完整 ID)
 *
 * 行为:
 *   - percent ≥ 100 时不再展示(消息已有最终产物);MessageBubble 控制
 *   - error 状态由消息层另外渲染,本组件只表达"还在跑"
 */

import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@chayuan/ui';

export interface TaskProgressPartProps {
  percent: number;
  message?: string;
  eta_s?: number;
  task_id?: string;
  className?: string;
}

export const TaskProgressPart: React.FC<TaskProgressPartProps> = ({
  percent,
  message,
  eta_s,
  task_id,
  className,
}) => {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={message ?? '正在生成'}
      className={cn(
        'flex flex-col gap-1 rounded-md border border-[var(--cy-border-subtle)]',
        'bg-[var(--cy-surface-1)] p-2 text-[11px]',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 text-[var(--cy-text-secondary)]">
          {clamped < 100 && <Loader2 className="h-3 w-3 flex-shrink-0 animate-spin" />}
          <span className="truncate">{message ?? (clamped < 100 ? '生成中…' : '完成')}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-[var(--cy-text-tertiary)]">
          {typeof eta_s === 'number' && eta_s > 0 && clamped < 100 && (
            <span>剩余 ~{eta_s}s</span>
          )}
          <span className="font-mono">{clamped}%</span>
        </div>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--cy-surface-2)]">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500 ease-out',
            clamped >= 100
              ? 'bg-emerald-500'
              : 'bg-gradient-to-r from-emerald-400 to-blue-500',
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
      {task_id && (
        <div className="self-end text-[10px] opacity-60" title={`task_id: ${task_id}`}>
          ⛓ {task_id.slice(0, 12)}…
        </div>
      )}
    </div>
  );
};
