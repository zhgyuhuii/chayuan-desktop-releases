/**
 * 文生图 / 图像编辑 模式的参数条。
 *
 * 仅在 `composerMode.paramKind === 'image_size_n'` 时出现,挂在
 * ComposerModeBadge 旁。
 *
 * 字段:
 *   - size:横/竖/方形预设(各厂商规范不一致,前端只暴露语义,实际 size string
 *     由 capability + 厂商共同决定)
 *   - n:图像张数(1-4),DALL-E 3 实际只跑 1 张,后端 clamp
 *   - seed:可选,默认 random(留空)
 *
 * 状态:写到 composer store 的 ``modalityParams``,提交时随 ``streamModality`` 一并发。
 *
 * 设计原则:
 *   - 只暴露**直觉化**选项,具体 W*H / WxH 字符串由 connector 层兼容(DashScope
 *     用 *,OpenAI 用 x,本组件统一用 *,connector 端 ``_normalize_size`` 自动转)
 *   - n 多于 1 时小提示:某些模型只跑 1 张
 *   - seed 是高级项,折叠在按钮里
 */

import * as React from 'react';
import {
  ChevronDown,
  Dices,
  Hash,
  RectangleHorizontal,
  RectangleVertical,
  Square,
} from 'lucide-react';
import { cn } from '@chayuan/ui';
import { useComposerS } from '../../store/composer';

interface SizePreset {
  /** size 字符串,统一用 ``*``;后端 connector 把 ``*`` 转 ``x``(OpenAI 系)。 */
  value: string;
  label: string;
  ratio: string;
  Icon: React.FC<{ className?: string }>;
}

const SIZE_PRESETS: SizePreset[] = [
  { value: '1024*1024', label: '正方形',  ratio: '1:1',  Icon: Square },
  { value: '1664*928',  label: '横版',     ratio: '16:9', Icon: RectangleHorizontal },
  { value: '928*1664',  label: '竖版',     ratio: '9:16', Icon: RectangleVertical },
  { value: '1472*1104', label: '横版 4:3', ratio: '4:3',  Icon: RectangleHorizontal },
  { value: '1104*1472', label: '竖版 3:4', ratio: '3:4',  Icon: RectangleVertical },
];

const N_OPTIONS = [1, 2, 4] as const;

export const ImageGenParamBar: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const params = useComposerS((s) => s.modalityParams);
  const setParams = useComposerS((s) => s.setModalityParams);

  const size = (typeof params.size === 'string' ? params.size : SIZE_PRESETS[0]!.value);
  const n = (typeof params.n === 'number' ? params.n : 1);
  const seed = (typeof params.seed === 'number' ? params.seed : null);

  const [seedOpen, setSeedOpen] = React.useState(false);
  const [seedInput, setSeedInput] = React.useState<string>(seed != null ? String(seed) : '');

  const onPickSize = (value: string) => setParams({ size: value });
  const onPickN = (value: number) => setParams({ n: value });
  const onCommitSeed = () => {
    const s = seedInput.trim();
    if (!s) {
      setParams({ seed: undefined });
      setSeedOpen(false);
      return;
    }
    const num = Number(s);
    if (Number.isFinite(num) && num >= 0) {
      setParams({ seed: Math.floor(num) });
    }
    setSeedOpen(false);
  };
  const onRandomSeed = () => {
    setParams({ seed: undefined });
    setSeedInput('');
  };

  return (
    <div
      role="toolbar"
      aria-label="文生图参数"
      className={cn(
        'flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--cy-text-secondary)]',
        compact && 'gap-1',
      )}
    >
      {/* 尺寸预设 */}
      <span className="opacity-60">尺寸</span>
      <div className="flex items-center gap-0.5">
        {SIZE_PRESETS.map((p) => {
          const active = size === p.value;
          const Icon = p.Icon;
          return (
            <button
              key={p.value}
              type="button"
              onClick={() => onPickSize(p.value)}
              title={`${p.label}(${p.ratio} · ${p.value.replace('*', '×')})`}
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

      {/* 张数 */}
      <span className="opacity-60">张数</span>
      <div className="flex items-center gap-0.5">
        {N_OPTIONS.map((value) => {
          const active = n === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onPickN(value)}
              aria-pressed={active}
              className={cn(
                'inline-flex h-6 w-6 items-center justify-center rounded-md text-[10px] font-medium transition-colors',
                active
                  ? 'bg-[var(--cy-ink-700)] text-white'
                  : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
              )}
            >
              {value}
            </button>
          );
        })}
      </div>

      <span className="mx-1 opacity-30">·</span>

      {/* Seed(可选高级)— 默认折叠;输入数字 → 提交;清空 → 随机 */}
      <button
        type="button"
        onClick={() => {
          setSeedInput(seed != null ? String(seed) : '');
          setSeedOpen((v) => !v);
        }}
        aria-expanded={seedOpen}
        className={cn(
          'inline-flex h-6 items-center gap-1 rounded-md px-1.5 transition-colors',
          seed != null
            ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200'
            : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
        )}
        title={seed != null ? `Seed: ${seed}(点击修改)` : '随机 Seed(点击设置固定 seed)'}
      >
        {seed != null ? <Hash className="h-3 w-3" /> : <Dices className="h-3 w-3" />}
        {!compact && <span className="text-[10px]">{seed != null ? seed : '随机'}</span>}
        <ChevronDown className={cn('h-3 w-3 transition-transform', seedOpen && 'rotate-180')} />
      </button>
      {seedOpen && (
        <div className="inline-flex items-center gap-1">
          <input
            type="number"
            min={0}
            step={1}
            value={seedInput}
            placeholder="留空 = 随机"
            onChange={(e) => setSeedInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                onCommitSeed();
              } else if (e.key === 'Escape') {
                setSeedOpen(false);
              }
            }}
            onBlur={onCommitSeed}
            className={cn(
              'h-6 w-24 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-1.5 text-[10px]',
              'focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none',
            )}
          />
          {seed != null && (
            <button
              type="button"
              onClick={onRandomSeed}
              title="清空,改回随机"
              className="text-[10px] text-[var(--cy-text-tertiary)] underline hover:text-[var(--cy-text-primary)]"
            >
              随机
            </button>
          )}
        </div>
      )}
    </div>
  );
};
