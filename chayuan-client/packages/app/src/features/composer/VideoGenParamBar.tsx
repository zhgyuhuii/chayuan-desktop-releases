/**
 * 文生视频模式参数条。
 *
 * 仅在 ``composerMode.paramKind === 'video_size_dur'`` 时显示。
 *
 * 字段(共用 composer store 的 ``modalityParams``):
 *   - size:1280*720 / 720*1280 / 960*960(横竖方;具体能跑啥取决于模型,
 *     后端 ``_normalize_size`` 不识别时回退默认)
 *   - duration:2 / 5 / 10 秒(wanx 大部分版本支持 5s,部分长版本到 10s)
 *
 * Seed 高级项暂不暴露 — 大部分 t2v 模型 seed 价值小,留 PR-6 真要时再补。
 */

import * as React from 'react';
import { RectangleHorizontal, RectangleVertical, Square, Timer } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { useComposerS } from '../../store/composer';

interface SizePreset {
  value: string;
  ratio: string;
  Icon: React.FC<{ className?: string }>;
}

const SIZE_PRESETS: SizePreset[] = [
  { value: '1280*720', ratio: '16:9', Icon: RectangleHorizontal },
  { value: '720*1280', ratio: '9:16', Icon: RectangleVertical },
  { value: '960*960',  ratio: '1:1',  Icon: Square },
];

const DURATION_OPTIONS = [2, 5, 10] as const;

export const VideoGenParamBar: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const params = useComposerS((s) => s.modalityParams);
  const setParams = useComposerS((s) => s.setModalityParams);

  const size = typeof params.size === 'string' ? params.size : SIZE_PRESETS[0]!.value;
  const duration = typeof params.duration === 'number' ? params.duration : 5;

  return (
    <div
      role="toolbar"
      aria-label="文生视频参数"
      className={cn(
        'flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--cy-text-secondary)]',
        compact && 'gap-1',
      )}
    >
      <span className="opacity-60">尺寸</span>
      <div className="flex items-center gap-0.5">
        {SIZE_PRESETS.map((p) => {
          const active = size === p.value;
          const Icon = p.Icon;
          return (
            <button
              key={p.value}
              type="button"
              onClick={() => setParams({ size: p.value })}
              title={`${p.ratio} · ${p.value.replace('*', '×')}`}
              aria-pressed={active}
              className={cn(
                'inline-flex h-6 items-center gap-1 rounded-md px-1.5 transition-colors',
                active
                  ? 'bg-[var(--cy-ink-700)] text-white'
                  : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
              )}
            >
              <Icon className="h-3 w-3" />
              {!compact && <span className="text-[10px]">{p.ratio}</span>}
            </button>
          );
        })}
      </div>

      <span className="mx-1 opacity-30">·</span>

      <span className="opacity-60">时长</span>
      <div className="flex items-center gap-0.5">
        {DURATION_OPTIONS.map((value) => {
          const active = duration === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setParams({ duration: value })}
              aria-pressed={active}
              title={`${value} 秒`}
              className={cn(
                'inline-flex h-6 items-center gap-1 rounded-md px-1.5 transition-colors',
                active
                  ? 'bg-[var(--cy-ink-700)] text-white'
                  : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
              )}
            >
              <Timer className="h-3 w-3" />
              <span className="text-[10px]">{value}s</span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
