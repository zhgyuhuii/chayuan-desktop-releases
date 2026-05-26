/**
 * 语音合成模式参数条。
 *
 * 仅在 ``composerMode.paramKind === 'tts_voice'`` 时显示。
 *
 * 字段(共用 composer store 的 ``modalityParams``):
 *   - voice:OpenAI 6 标准音色(alloy/echo/fable/onyx/nova/shimmer)
 *           中文模型(qwen-tts / cosyvoice)的厂商音色后续厂商专属 Connector
 *           接入时另行扩充;现在统一走 OpenAI 兼容协议。
 *   - speed:0.75 / 1.0 / 1.25 / 1.5 chips(后端 clamp 到 [0.25, 4.0])
 *   - format(高级,折叠):mp3 / wav / opus,默认 mp3
 */

import * as React from 'react';
import { ChevronDown, Gauge, Music } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { classifyModalityVendor } from '@chayuan/transport';
import { useComposerS } from '../../store/composer';

interface VoiceOption {
  value: string;
  label: string;
  hint?: string;
}

// OpenAI 6 标准音色 — 用中性描述,不强调男女(部分音色实际偏向但官方不分类)
const OPENAI_VOICES: VoiceOption[] = [
  { value: 'alloy',   label: 'Alloy',   hint: '中性 · 默认' },
  { value: 'echo',    label: 'Echo',    hint: '清晰 · 中性' },
  { value: 'fable',   label: 'Fable',   hint: '柔和 · 叙事' },
  { value: 'onyx',    label: 'Onyx',    hint: '低沉 · 厚重' },
  { value: 'nova',    label: 'Nova',    hint: '明亮 · 活泼' },
  { value: 'shimmer', label: 'Shimmer', hint: '温暖 · 清亮' },
];

// DashScope qwen-tts / qwen3-tts 中文优先音色集 — 含方言版,Cherry 是官方默认
const DASHSCOPE_VOICES: VoiceOption[] = [
  { value: 'Cherry',   label: 'Cherry',   hint: '默认 · 通用' },
  { value: 'Serena',   label: 'Serena',   hint: '柔和 · 女声' },
  { value: 'Ethan',    label: 'Ethan',    hint: '清晰 · 男声' },
  { value: 'Chelsie',  label: 'Chelsie',  hint: '活泼 · 女声' },
  { value: 'Dylan',    label: 'Dylan',    hint: '京片儿 · 男声' },
  { value: 'Jada',     label: 'Jada',     hint: '上海话 · 女声' },
  { value: 'Sunny',    label: 'Sunny',    hint: '四川话 · 女声' },
  { value: 'Jennifer', label: 'Jennifer', hint: '叙事 · 女声' },
  { value: 'Ryan',     label: 'Ryan',     hint: '深沉 · 男声' },
  { value: 'Katerina', label: 'Katerina', hint: '亲切 · 女声' },
  { value: 'Eric',     label: 'Eric',     hint: '稳健 · 男声' },
  { value: 'Kiki',     label: 'Kiki',     hint: '甜美 · 女声' },
];

const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5] as const;
const FORMAT_OPTIONS = ['mp3', 'wav', 'opus'] as const;

export const TtsVoiceParamBar: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const params = useComposerS((s) => s.modalityParams);
  const setParams = useComposerS((s) => s.setModalityParams);
  const modelId = useComposerS((s) => s.modelId);

  // 按模型 vendor 选音色列表 — DashScope 用 Cherry 系,OpenAI 用 alloy 系。
  // 默认按 vendor 第一个;用户切了模型/未配置时也兜底有个能跑的值。
  const vendor = classifyModalityVendor(modelId);
  const voiceList = vendor === 'dashscope' ? DASHSCOPE_VOICES : OPENAI_VOICES;
  const voice = (() => {
    const stored = typeof params.voice === 'string' ? params.voice : '';
    // 存的 voice 不在当前 vendor 列表里 → 回退到列表第一个(避免选 alloy 撞 DashScope)
    if (stored && voiceList.some((v) => v.value === stored)) return stored;
    return voiceList[0]!.value;
  })();
  // model 切换导致 voice 不匹配时,自动 patch 一次,让后端拿到正确默认
  React.useEffect(() => {
    if (typeof params.voice === 'string' && voiceList.some((v) => v.value === params.voice)) return;
    setParams({ voice: voiceList[0]!.value });
  }, [vendor]);  // eslint-disable-line react-hooks/exhaustive-deps

  const speed = typeof params.speed === 'number' ? params.speed : 1.0;
  const fmt = typeof params.response_format === 'string' ? params.response_format : 'mp3';

  const [advOpen, setAdvOpen] = React.useState(false);

  return (
    <div
      role="toolbar"
      aria-label="TTS 参数"
      className={cn(
        'flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--cy-text-secondary)]',
        compact && 'gap-1',
      )}
    >
      {/* 音色 — select 比 chip 更省空间(6 个) */}
      <span className="opacity-60">音色</span>
      <select
        value={voice}
        onChange={(e) => setParams({ voice: e.target.value })}
        className={cn(
          'h-6 rounded-md border border-[var(--cy-border-subtle)]',
          'bg-[var(--cy-surface-1)] px-1.5 text-[10px] text-[var(--cy-text-primary)]',
          'focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none',
        )}
      >
        {voiceList.map((v) => (
          <option key={v.value} value={v.value} title={v.hint}>
            {v.label}
          </option>
        ))}
      </select>

      <span className="mx-1 opacity-30">·</span>

      {/* 语速 chips */}
      <span className="opacity-60">语速</span>
      <div className="flex items-center gap-0.5">
        {SPEED_OPTIONS.map((value) => {
          const active = Math.abs(speed - value) < 1e-3;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setParams({ speed: value })}
              aria-pressed={active}
              title={`${value.toFixed(2)}×`}
              className={cn(
                'inline-flex h-6 items-center gap-1 rounded-md px-1.5 transition-colors',
                active
                  ? 'bg-[var(--cy-ink-700)] text-white'
                  : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
              )}
            >
              <Gauge className="h-3 w-3" />
              <span className="text-[10px]">{value.toFixed(2).replace(/\.?0+$/, '')}×</span>
            </button>
          );
        })}
      </div>

      <span className="mx-1 opacity-30">·</span>

      {/* 格式 — 折叠在按钮里,大多数用户不需要改 */}
      <button
        type="button"
        onClick={() => setAdvOpen((v) => !v)}
        aria-expanded={advOpen}
        className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
        title="高级:输出格式"
      >
        <Music className="h-3 w-3" />
        <span className="text-[10px]">{fmt}</span>
        <ChevronDown className={cn('h-3 w-3 transition-transform', advOpen && 'rotate-180')} />
      </button>
      {advOpen && (
        <div className="flex items-center gap-0.5">
          {FORMAT_OPTIONS.map((f) => {
            const active = fmt === f;
            return (
              <button
                key={f}
                type="button"
                onClick={() => setParams({ response_format: f })}
                aria-pressed={active}
                className={cn(
                  'inline-flex h-6 items-center rounded-md px-1.5 text-[10px] transition-colors',
                  active
                    ? 'bg-[var(--cy-ink-700)] text-white'
                    : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]',
                )}
              >
                {f}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
