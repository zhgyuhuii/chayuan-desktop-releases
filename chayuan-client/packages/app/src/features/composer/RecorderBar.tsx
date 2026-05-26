/**
 * 编辑器底部录音工具栏 — 频谱条 + 时长 + 暂停 + 停止 + 取消。
 *
 * 录音中常驻显示;不录音时不渲染。停止后由 NoteEditor 切到"转写中"覆盖态。
 *
 * 设计:
 *   - 居中弹性布局,左侧状态标签,中间频谱条占主区,右侧三个按钮
 *   - 频谱条颜色 / 按钮颜色随状态变化(录音红 / 暂停黄)
 *   - 暂停状态:频谱条静止(stream 还活着但用户感受是"停了"),按钮变"继续"
 *   - 停止后回调 onStop 走 useMicSimpleRecorder.stop() — 触发整段转写
 */
import * as React from 'react';
import { Mic, Pause, Play, Square, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { VoiceVisualizer } from './VoiceVisualizer';

export interface RecorderBarProps {
  recording: boolean;
  paused: boolean;
  /** 已录秒数(暂停期间不增长) */
  elapsedSec: number;
  /** 当前 MediaStream — 喂给 VoiceVisualizer */
  stream: MediaStream | null;
  /** 是否处于"停止 → 整段转写中"状态(由 NoteEditor 控制),覆盖按钮成不可点 */
  transcribing?: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  /** 取消:丢弃录音,不触发转写 */
  onCancel: () => void;
}

const fmtTime = (sec: number) => {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

export const RecorderBar: React.FC<RecorderBarProps> = ({
  recording, paused, elapsedSec, stream, transcribing,
  onPause, onResume, onStop, onCancel,
}) => {
  if (!recording && !transcribing) return null;

  return (
    <div
      role="region"
      aria-label="录音工具栏"
      className="flex items-center gap-3 border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/80 px-4 py-2 backdrop-blur"
    >
      {/* 左:状态 + 时长 */}
      <div className="flex shrink-0 items-center gap-2 text-xs">
        <Mic
          className={cn(
            'h-4 w-4',
            transcribing ? 'animate-pulse text-blue-600'
              : paused ? 'text-amber-600'
              : 'animate-pulse text-red-600',
          )}
        />
        <span className="font-mono tabular-nums text-[var(--cy-text-secondary)]">
          {fmtTime(elapsedSec)}
        </span>
        <span className="text-[10px] text-[var(--cy-text-tertiary)]">
          {transcribing ? '整段转写中…(模型分析录音)'
            : paused ? '已暂停 — 点继续录'
            : '录音中'}
        </span>
      </div>

      {/* 中:声纹可视化 — 暂停时 stream 还活着但用户感受暂停,VoiceVisualizer 仍跑没坏处 */}
      <div className="flex-1 min-w-0">
        <VoiceVisualizer stream={stream} active={recording && !paused} height={36} />
      </div>

      {/* 右:操作按钮 */}
      <div className="flex shrink-0 items-center gap-1.5">
        {!transcribing && (
          <>
            {paused ? (
              <Button
                size="sm"
                variant="outline"
                onClick={onResume}
                className="h-8 text-xs"
                title="继续录音"
              >
                <Play className="h-3.5 w-3.5" /> 继续
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={onPause}
                className="h-8 text-xs"
                title="暂停录音 — 还可继续"
              >
                <Pause className="h-3.5 w-3.5" /> 暂停
              </Button>
            )}
            <Button
              size="sm"
              onClick={onStop}
              className="h-8 bg-red-600 text-xs text-white hover:bg-red-700"
              title="停止录音 — 整段送模型转写"
            >
              <Square className="h-3.5 w-3.5" /> 停止
            </Button>
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
              title="取消录音 — 丢弃本次录音,不转写"
              aria-label="取消录音"
            >
              <X className="h-4 w-4" />
            </button>
          </>
        )}
        {transcribing && (
          <span className="text-xs text-[var(--cy-text-tertiary)]">
            模型处理中,请稍候…
          </span>
        )}
      </div>
    </div>
  );
};
