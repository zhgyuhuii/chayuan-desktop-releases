/**
 * 模型类型中心注册表 —— marketplace 全模块共享的单一来源。
 *
 * 设计原则：
 *   - 后端 PlatformConfig 已用 8 字段表达模型类型；前端凡是"按模型类型显示/过滤/编辑"
 *     的代码都经此注册表派生，杜绝"一处改、它处忘"的漂移。
 *   - 每个 ModelType 关联：字段名（与 PlatformConfig 对齐） / 显示文案 / 图标 /
 *     主色相（OKLCH H 值）/ 是否在卡片 chip 行默认可见 / 在 accordion 默认是否展开。
 *   - 新增 ModelType（如未来 video2text）：在此追加一项，所有消费方零改动。
 */

import type { PlatformConfig } from '@chayuan/api';
import {
  Eye,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  ListOrdered,
  type LucideIcon,
  MessageSquare,
  Mic,
  Network,
  Speaker,
} from 'lucide-react';

/** 8 种模型类型 —— 与后端 PlatformConfig 字段一一对应 */
export type ModelType =
  | 'llm'
  | 'embed'
  | 'rerank'
  | 'text2image'
  | 'image2text'
  | 'image2image'
  | 'tts'
  | 'asr';

/** 与 PlatformConfig 上数组字段名映射 */
export type ModelField =
  | 'llm_models'
  | 'embed_models'
  | 'rerank_models'
  | 'text2image_models'
  | 'image2text_models'
  | 'image2image_models'
  | 'text2speech_models'
  | 'speech2text_models';

export interface ModelTypeMeta {
  /** 稳定 key，用于 URL / state / 持久化 */
  key: ModelType;
  /** PlatformConfig 上的数组字段名 */
  field: ModelField;
  /** i18n key 前缀；最终文案 = t(`${labelKey}.label` / `.long` / `.hint`） */
  i18nKey: string;
  /** Lucide 图标 */
  icon: LucideIcon;
  /** OKLCH 色相 —— chip 主色 / 类型徽底色统一从这里派生 */
  hue: number;
  /** 是否常用 —— 卡片 chip 行默认显示；不常用的塞进"更多" */
  prominent: boolean;
}

export const MODEL_TYPES: ReadonlyArray<ModelTypeMeta> = [
  {
    key: 'llm',
    field: 'llm_models',
    i18nKey: 'modelMarket.capability.llm',
    icon: MessageSquare,
    hue: 240,
    prominent: true,
  },
  {
    key: 'embed',
    field: 'embed_models',
    i18nKey: 'modelMarket.capability.embed',
    icon: Network,
    hue: 152,
    prominent: true,
  },
  {
    key: 'rerank',
    field: 'rerank_models',
    i18nKey: 'modelMarket.capability.rerank',
    icon: ListOrdered,
    hue: 200,
    prominent: false,
  },
  {
    key: 'text2image',
    field: 'text2image_models',
    i18nKey: 'modelMarket.capability.text2image',
    icon: ImageIcon,
    hue: 36,
    prominent: true,
  },
  {
    key: 'image2text',
    field: 'image2text_models',
    i18nKey: 'modelMarket.capability.image2text',
    icon: Eye,
    hue: 280,
    prominent: true,
  },
  {
    key: 'image2image',
    field: 'image2image_models',
    i18nKey: 'modelMarket.capability.image2image',
    icon: ImagePlus,
    hue: 350,
    prominent: false,
  },
  {
    key: 'tts',
    field: 'text2speech_models',
    i18nKey: 'modelMarket.capability.tts',
    icon: Speaker,
    hue: 60,
    prominent: false,
  },
  {
    key: 'asr',
    field: 'speech2text_models',
    i18nKey: 'modelMarket.capability.asr',
    icon: Mic,
    hue: 12,
    prominent: false,
  },
] as const;

/** 注册表的 O(1) 查询 */
export const MODEL_TYPE_BY_KEY: Readonly<Record<ModelType, ModelTypeMeta>> = Object.freeze(
  MODEL_TYPES.reduce(
    (acc, m) => {
      acc[m.key] = m;
      return acc;
    },
    {} as Record<ModelType, ModelTypeMeta>,
  ),
);

/** "全能力"哨兵 —— 表示不按类型过滤 */
export const CAPABILITY_ANY = '__any__' as const;
export type CapabilityKey = typeof CAPABILITY_ANY | ModelType;

export const CAPABILITY_ANY_META = {
  key: CAPABILITY_ANY,
  i18nKey: 'modelMarket.capability.any',
  icon: Layers,
} as const;

/** 取一个平台某类模型清单（可空数组） */
export function getModelsOfType(
  p: Pick<
    PlatformConfig,
    | 'llm_models'
    | 'embed_models'
    | 'rerank_models'
    | 'text2image_models'
    | 'image2text_models'
    | 'image2image_models'
    | 'text2speech_models'
    | 'speech2text_models'
    | 'disabled_models'
  >,
  type: ModelType,
  options: { excludeDisabled?: boolean } = {},
): string[] {
  const meta = MODEL_TYPE_BY_KEY[type];
  const arr = (p[meta.field] as string[] | undefined) ?? [];
  if (!options.excludeDisabled) return arr;
  const blacklist = new Set(p.disabled_models ?? []);
  return arr.filter((m) => !blacklist.has(m));
}

/** 平台是否声明支持某能力（数组非空） */
export function platformHasCapability(
  p: Pick<
    PlatformConfig,
    | 'llm_models'
    | 'embed_models'
    | 'rerank_models'
    | 'text2image_models'
    | 'image2text_models'
    | 'image2image_models'
    | 'text2speech_models'
    | 'speech2text_models'
  >,
  cap: CapabilityKey,
): boolean {
  if (cap === CAPABILITY_ANY) return true;
  return getModelsOfType(p as PlatformConfig, cap).length > 0;
}

/** 平台所有"按类型分组"的模型，供 ProviderCard "全能力"模式或 accordion 消费 */
export interface GroupedModels {
  type: ModelType;
  meta: ModelTypeMeta;
  models: string[];
}

export function groupModelsByType(
  p: PlatformConfig,
  options: { excludeDisabled?: boolean; onlyNonEmpty?: boolean } = {},
): GroupedModels[] {
  const out: GroupedModels[] = [];
  for (const meta of MODEL_TYPES) {
    const models = getModelsOfType(p, meta.key, options);
    if (options.onlyNonEmpty && models.length === 0) continue;
    out.push({ type: meta.key, meta, models });
  }
  return out;
}

/** 派生类型 chip / 徽 的 OKLCH 颜色 —— 全模块统一 */
export function modelTypeColors(hue: number): { bg: string; fg: string; border: string } {
  return {
    bg: `oklch(96% 0.04 ${hue})`,
    fg: `oklch(50% 0.18 ${hue})`,
    border: `oklch(78% 0.12 ${hue})`,
  };
}
