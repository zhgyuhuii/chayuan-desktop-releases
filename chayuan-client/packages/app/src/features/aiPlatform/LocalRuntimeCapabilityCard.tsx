/**
 * 单个 capability 的状态 + 启停按钮 card。
 *
 * LocalRuntimePanel 复用 3 次:chat / embedding / rerank。
 */

import * as React from 'react';
import { Play, Square, RotateCw } from 'lucide-react';
import { Button } from '@chayuan/ui';
import type { LocalRuntimeCapability, LocalRuntimeStatus } from '@chayuan/api';
import { LocalRuntimeStatusBadge } from './LocalRuntimeStatusBadge';

const CAPABILITY_LABEL: Record<LocalRuntimeCapability, string> = {
  chat: '聊天',
  embedding: '文本嵌入',
  rerank: '重排',
  asr: '语音识别',
  'image-embedding': '图像嵌入',
};

export interface LocalRuntimeCapabilityCardProps {
  capability: LocalRuntimeCapability;
  status: LocalRuntimeStatus | null;
  pending: 'start' | 'stop' | 'restart' | null;
  onStart(): void;
  onStop(): void;
  onRestart(): void;
}

export const LocalRuntimeCapabilityCard: React.FC<LocalRuntimeCapabilityCardProps> = ({
  capability,
  status,
  pending,
  onStart,
  onStop,
  onRestart,
}) => {
  const isPending = pending !== null;
  const isReady = status?.state === 'ready';
  const isStopped = !status || status.state === 'stopped';

  return (
    <div className="rounded-md border border-[var(--cy-border-subtle)] p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[var(--cy-text-primary)]">
          {CAPABILITY_LABEL[capability]}
        </span>
        <LocalRuntimeStatusBadge status={status} />
        {status?.endpoint && (
          <code className="text-xs text-[var(--cy-text-secondary)]">{status.endpoint}</code>
        )}
        {status?.pid != null && (
          <span className="text-xs text-[var(--cy-text-tertiary)]">pid {status.pid}</span>
        )}
      </div>
      {status?.model_id && (
        <div className="text-xs text-[var(--cy-text-secondary)]">
          模型:<code>{status.model_id}</code>
        </div>
      )}
      {status?.state === 'failed' && status.last_error && (
        <div className="rounded-sm border border-rose-500/30 bg-rose-50 p-2 text-xs text-rose-800 dark:bg-rose-950/30 dark:text-rose-200 whitespace-pre-wrap break-all">
          {status.last_error}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={onStart}
          disabled={isPending || status?.state === 'starting' || isReady}
        >
          <Play className={'mr-1 h-3.5 w-3.5' + (pending === 'start' ? ' animate-pulse' : '')} />
          启动
        </Button>
        <Button size="sm" variant="outline" onClick={onStop} disabled={isPending || isStopped}>
          <Square className="mr-1 h-3.5 w-3.5" />
          停止
        </Button>
        <Button size="sm" variant="outline" onClick={onRestart} disabled={isPending}>
          <RotateCw
            className={'mr-1 h-3.5 w-3.5' + (pending === 'restart' ? ' animate-spin' : '')}
          />
          重启
        </Button>
      </div>
    </div>
  );
};

export default LocalRuntimeCapabilityCard;
