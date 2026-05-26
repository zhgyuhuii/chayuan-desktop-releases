/**
 * ChatComposer 内部的录音面板 — 录音中显示,挡在 textarea 上方。
 *
 * 跟 NoteEditor 用的 RecorderBar 的区别:多一个**「发送」按钮** — 它的 onClick
 * 不是简单 stop,而是触发 caller 提供的 onSubmitRecording(stop + transcribe + send)。
 * 转写中(transcribing=true)按钮藏起来,显示"转写中…"loading。
 */
import * as React from 'react';
import { Loader2, Mic, Pause, Play, Send, Square, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { VoiceVisualizer } from './VoiceVisualizer';

export interface ChatRecorderPanelProps {
  recording: boolean;
  paused: boolean;
  elapsedSec: number;
  stream: MediaStream | null;
  transcribing?: boolean;
  onPause: () => void;
  onResume: () => void;
  /** 停止 = 停录音但**不发送**,blob 仍交给 onRecorded(caller 决定怎么处理);UI 实际上不暴露这个,
   *  因为 chat 场景下"停止"等于"取消"或"发送",没中间态。但 props 留着以便复用。 */
  onStop?: () => void;
  /** 取消:丢弃录音不转写 */
  onCancel: () => void;
  /** **发送**:停录 + 整段转写 + setDraft + submit chat。由 ChatComposer 实现。 */
  onSubmit: () => void;
}

const fmtTime = (sec: number) => {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

export const ChatRecorderPanel: React.FC<ChatRecorderPanelProps> = ({
  recording, paused, elapsedSec, stream, transcribing,
  onPause, onResume, onCancel, onSubmit, onStop,
}) => {
  if (!recording && !transcribing) return null;
  return (
    <div
      role="region"
      aria-label="录音面板"
      className="mb-2 flex items-center gap-2 rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/80 px-3 py-2 backdrop-blur"
    >
      <div className="flex shrink-0 items-center gap-1.5 text-xs">
        <Mic
          className={cn(
            'h-3.5 w-3.5',
            transcribing ? 'animate-pulse text-blue-600'
              : paused ? 'text-amber-600'
              : 'animate-pulse text-red-600',
          )}
        />
        <span className="font-mono tabular-nums text-[var(--cy-text-secondary)]">
          {fmtTime(elapsedSec)}
        </span>
      </div>

      <div className="flex-1 min-w-0">
        <VoiceVisualizer stream={stream} active={recording && !paused} height={28} barCount={20} />
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {transcribing ? (
          <span className="inline-flex items-center gap-1 px-2 text-xs text-[var(--cy-text-tertiary)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 转写中…
          </span>
        ) : (
          <>
            {paused ? (
              <button
                type="button"
                onClick={onResume}
                className="inline-flex h-7 items-center gap-1 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs hover:bg-[var(--cy-surface-2)]"
                title="继续录音"
              >
                <Play className="h-3 w-3" /> 继续
              </button>
            ) : (
              <button
                type="button"
                onClick={onPause}
                className="inline-flex h-7 items-center gap-1 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs hover:bg-[var(--cy-surface-2)]"
                title="暂停录音"
              >
                <Pause className="h-3 w-3" /> 暂停
              </button>
            )}
            {onStop && (
              <button
                type="button"
                onClick={onStop}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
                title="停止录音(保留 blob,暂不发送 — 由 caller 处理)"
              >
                <Square className="h-3 w-3" />
              </button>
            )}
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
              title="取消(丢弃录音)"
              aria-label="取消"
            >
              <X className="h-3.5 w-3.5" />
            </button>
            <Button
              size="sm"
              onClick={onSubmit}
              className="ml-1 h-7 bg-[var(--cy-brand-600)] text-xs text-white hover:bg-[var(--cy-brand-700)]"
              title="发送 — 停止 + 整段转写 + 提交到对话"
            >
              <Send className="h-3 w-3" /> 发送
            </Button>
          </>
        )}
      </div>
    </div>
  );
};
