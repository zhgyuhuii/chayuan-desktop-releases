/**
 * 模型 capability 的紧凑标签 — 给模型选择菜单的"分类筛选行"和"每条模型前的色块"用。
 *
 * 真源:``@chayuan/transport`` 的 ``classifyModalityCapability`` 把模型 id 推
 * 出 capability;color / label 跟 ``composerMode.ts`` 保持一套调色板,这样
 * "在选择器里看到的色"和"选中后输入框上方 pill 的色"是同一种。
 *
 * 排序:对话 → 视觉 → 文生图 → 图像编辑 → 文生视频 → 语音合成 → 语音识别 →
 * OCR → 嵌入 → 重排。这个顺序也是 capability filter chip 的固定排列。
 *
 * 选择器把"看不到自己 capability"的模型都吞掉(filter return false),所以
 * 即便未来加新 capability 而这里忘补,只是不显示而已,不会爆栈或乱色。
 */

import * as React from 'react';
import { cn } from '@chayuan/ui';
import { classifyModalityCapability, type ModalityCapability } from '@chayuan/transport';
import type { RawModelItem } from '@chayuan/api';

export interface CapabilityChipMeta {
  cap: ModalityCapability;
  /** 给筛选 chip 用的短标签 — "对话"、"文生图" */
  label: string;
  /** 给筛选 chip 用的长标签(tooltip) — "对话模型"、"文生图模型" */
  longLabel: string;
  /** Tailwind 配色 — bg + text + border;active 时换更深的色 */
  classes: string;
  classesActive: string;
}

// 顺序敏感:这是 chip 排列顺序。把"对话 / 视觉对话"放最前 — 用户最常用。
const CAP_META: ReadonlyArray<CapabilityChipMeta> = [
  {
    cap: 'chat',
    label: '对话',
    longLabel: '对话模型',
    classes:
      'bg-blue-50 text-blue-700 border border-blue-200 ' +
      'dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800/60',
    classesActive: 'bg-blue-600 text-white border-blue-600',
  },
  {
    cap: 'vl',
    label: '视觉',
    longLabel: '视觉对话(图文)',
    classes:
      'bg-violet-50 text-violet-700 border border-violet-200 ' +
      'dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-800/60',
    classesActive: 'bg-violet-600 text-white border-violet-600',
  },
  {
    cap: 't2i',
    label: '文生图',
    longLabel: '文生图模型',
    classes:
      'bg-pink-50 text-pink-700 border border-pink-200 ' +
      'dark:bg-pink-950/40 dark:text-pink-300 dark:border-pink-800/60',
    classesActive: 'bg-pink-600 text-white border-pink-600',
  },
  {
    cap: 'image_edit',
    label: '图像编辑',
    longLabel: '图像编辑模型',
    classes:
      'bg-fuchsia-50 text-fuchsia-700 border border-fuchsia-200 ' +
      'dark:bg-fuchsia-950/40 dark:text-fuchsia-300 dark:border-fuchsia-800/60',
    classesActive: 'bg-fuchsia-600 text-white border-fuchsia-600',
  },
  {
    cap: 't2v',
    label: '文生视频',
    longLabel: '文生视频模型',
    classes:
      'bg-rose-50 text-rose-700 border border-rose-200 ' +
      'dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800/60',
    classesActive: 'bg-rose-600 text-white border-rose-600',
  },
  {
    cap: 'tts',
    label: '语音合成',
    longLabel: '语音合成模型',
    classes:
      'bg-emerald-50 text-emerald-700 border border-emerald-200 ' +
      'dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800/60',
    classesActive: 'bg-emerald-600 text-white border-emerald-600',
  },
  {
    cap: 'asr',
    label: '语音识别',
    longLabel: '语音识别模型',
    classes:
      'bg-cyan-50 text-cyan-700 border border-cyan-200 ' +
      'dark:bg-cyan-950/40 dark:text-cyan-300 dark:border-cyan-800/60',
    classesActive: 'bg-cyan-600 text-white border-cyan-600',
  },
  {
    cap: 'ocr',
    label: 'OCR',
    longLabel: '图像识字(OCR)',
    classes:
      'bg-amber-50 text-amber-700 border border-amber-200 ' +
      'dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/60',
    classesActive: 'bg-amber-600 text-white border-amber-600',
  },
  {
    cap: 'embedding',
    label: '嵌入',
    longLabel: '文本向量(嵌入)',
    classes:
      'bg-zinc-100 text-zinc-700 border border-zinc-300 ' +
      'dark:bg-zinc-900/40 dark:text-zinc-300 dark:border-zinc-700/60',
    classesActive: 'bg-zinc-700 text-white border-zinc-700',
  },
  {
    cap: 'rerank',
    label: '重排',
    longLabel: '重排模型',
    classes:
      'bg-zinc-100 text-zinc-700 border border-zinc-300 ' +
      'dark:bg-zinc-900/40 dark:text-zinc-300 dark:border-zinc-700/60',
    classesActive: 'bg-zinc-700 text-white border-zinc-700',
  },
];

const CAP_INDEX: Map<ModalityCapability, CapabilityChipMeta> = new Map(
  CAP_META.map((m) => [m.cap, m]),
);

export function getCapabilityMeta(cap: ModalityCapability): CapabilityChipMeta {
  return CAP_INDEX.get(cap) ?? CAP_INDEX.get('chat')!;
}

/**
 * 在 ChatComposer / ConversationView 这种"用户直接对话"的入口里,什么 capability
 * 是可以让用户在输入框里发起一次调用的。
 *
 * 排除原则:embedding / rerank 是后台索引能力,不能接受文本输入直接出结果;
 * 它们的配置入口在「设置 → AI 平台 → 文本嵌入 / 重排」,不该出现在对话框
 * 的模型下拉里(出现也只能被禁用,徒增噪音)。
 *
 * vl / chat 都允许 — vl 走 chat 链路的多模态 content,chat 是默认。
 */
export const INTERACTIVE_CAPABILITIES: ReadonlySet<ModalityCapability> = new Set([
  'chat', 'vl', 't2i', 'image_edit', 't2v', 'tts', 'asr', 'ocr',
]);

export function isInteractiveCapability(cap: ModalityCapability): boolean {
  return INTERACTIVE_CAPABILITIES.has(cap);
}

/**
 * 对话类能力 —— 知识问答 / KB 检索链路**必须走 LLM 生成**,只有 chat(纯文本
 * 对话)和 vl(视觉语言,走 chat 链路的多模态 content)能用。asr / tts / t2i /
 * t2v / image_edit / ocr 虽然也"可交互",但接进知识问答会在生成阶段才报
 * ModelNotConfigured / 404。绑定了知识库时,模型选择器据此把范围收窄到这里。
 */
export const CHAT_CAPABILITIES: ReadonlySet<ModalityCapability> = new Set([
  'chat', 'vl',
]);

export function isChatCapability(cap: ModalityCapability): boolean {
  return CHAT_CAPABILITIES.has(cap);
}

/**
 * 推断模型的 capability — 先用 ``classifyModalityCapability``(模型名前缀),
 * 命中不到再回退看后端 ``model_type``(老版本字段)。
 *
 * 后端 ``model_type`` 取值:'llm' / 'embed' / 'rerank' / 'image2text' / 'text2image' / ...
 */
export function inferCapability(m: RawModelItem): ModalityCapability {
  const byName = classifyModalityCapability(m.id);
  if (byName !== 'chat') return byName; // 命中专有 capability 直接用
  // 名字判不出 → 看后端类型字段
  const t = (m.model_type ?? '').toLowerCase().trim();
  if (!t) return 'chat'; // 老后端无字段 — 默认对话
  if (t === 'embed' || t === 'embedding') return 'embedding';
  if (t === 'rerank' || t === 'reranker') return 'rerank';
  if (t === 'text2image' || t === 'image_gen') return 't2i';
  if (t === 'image_edit') return 'image_edit';
  if (t === 'text2video' || t === 'video_gen' || t === 't2v') return 't2v';
  if (t === 'tts' || t === 'text2audio' || t === 'text2speech') return 'tts';
  if (t === 'asr' || t === 'audio2text' || t === 'speech2text') return 'asr';
  if (t === 'vl' || t === 'image2text' || t === 'vision') return 'vl';
  if (t === 'ocr') return 'ocr';
  return 'chat';
}

/** 返回按 CAP_META 顺序排好的"实际存在"的 capability 清单。 */
export function listExistingCapabilities(
  models: ReadonlyArray<RawModelItem>,
): CapabilityChipMeta[] {
  const present = new Set<ModalityCapability>();
  for (const m of models) present.add(inferCapability(m));
  return CAP_META.filter((meta) => present.has(meta.cap));
}

// ──────────────────────────────────────────────────────────────
// UI 组件
// ──────────────────────────────────────────────────────────────

export interface CapabilityChipProps {
  cap: ModalityCapability;
  /** 紧凑模式:用 short label;给"每条模型前的色块"用 */
  compact?: boolean;
  /** 是否处于激活态(给筛选 chip 用) */
  active?: boolean;
  onClick?: () => void;
  title?: string;
  className?: string;
}

/** 模型 capability 色块 / 筛选 chip 二合一组件。 */
export const CapabilityChip: React.FC<CapabilityChipProps> = ({
  cap, compact = false, active = false, onClick, title, className,
}) => {
  const meta = getCapabilityMeta(cap);
  const base =
    'inline-flex items-center rounded-full font-medium select-none whitespace-nowrap';
  const size = compact ? 'text-[9px] px-1.5 py-px h-4' : 'text-[11px] px-2 py-0.5 h-6';
  const color = active ? meta.classesActive : meta.classes;
  const interactive = onClick
    ? 'cursor-pointer transition-colors hover:opacity-90'
    : '';
  return (
    <span
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      title={title ?? meta.longLabel}
      className={cn(base, size, color, interactive, className)}
    >
      {compact ? meta.label : meta.label}
    </span>
  );
};
