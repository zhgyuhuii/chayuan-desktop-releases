/**
 * ModelTypeAccordion —— 按 ModelType 分组的可折叠模型清单编辑器。
 *
 * 用在：
 *   - PlatformSettingsDialog 替代单一 llm_models textarea
 *   - 未来若 ProviderCard 进入"详情"模式也可复用
 *
 * 行为：
 *   - 8 段 ModelType 顺序与 MODEL_TYPES 一致（对话先于嵌入先于…）
 *   - 默认状态：有数据的段默认展开；空段折叠
 *   - 点击段标题切换展开折叠（CSS 动画 + chevron 旋转）
 *   - "新检测到 N 个" 角标：当本次 detected 含此类型且模型数 > 之前时高亮
 *   - 类型徽用 modelTypeColors 派生 OKLCH，与卡片 chip 配色统一
 */

import { cn } from '@chayuan/ui';
import { ChevronDown, Plus, Sparkles } from 'lucide-react';
import * as React from 'react';
import { useTranslation } from '../../../i18n';
import {
  MODEL_TYPES,
  type ModelType,
  type ModelTypeMeta,
  modelTypeColors,
} from '../lib/modelTypes';
import { ModelLineEditor } from './ModelLineEditor';

/** 8 类模型清单 —— accordion 的受控 value */
export type ModelsByType = Record<ModelType, string[]>;

export const EMPTY_MODELS_BY_TYPE: ModelsByType = {
  llm: [],
  embed: [],
  rerank: [],
  text2image: [],
  image2text: [],
  image2image: [],
  tts: [],
  asr: [],
};

export interface ModelTypeAccordionProps {
  value: ModelsByType;
  onChange(next: ModelsByType): void;
  /** 本轮 detected：用于在分组标题上展示"新检测到 N 个"标签 */
  detected?: Partial<Record<ModelType, number>>;
  /** 默认全部折叠（false）/ 有数据自动展开（true，默认） */
  autoExpandNonEmpty?: boolean;
  /**
   * 默认隐藏 0 模型 + 0 detected 的类型分组,避免空分类把弹窗撑长;
   * 底部"+ 显示其他类型(N)"按钮可展开。默认 true。
   */
  hideEmpty?: boolean;
  /** 内置(catalog 默认)模型 id 集合,按 ModelType 分类;命中行 ModelLineEditor 不渲染删除按钮 */
  builtin?: Partial<Record<ModelType, ReadonlySet<string>>>;
  className?: string;
}

export const ModelTypeAccordion: React.FC<ModelTypeAccordionProps> = ({
  value,
  onChange,
  detected,
  autoExpandNonEmpty = true,
  hideEmpty = true,
  builtin,
  className,
}) => {
  // 展开状态：受控但允许用户手动覆盖；初始按 autoExpandNonEmpty + 数据决定
  const [expanded, setExpanded] = React.useState<Set<ModelType>>(() => {
    const s = new Set<ModelType>();
    for (const m of MODEL_TYPES) {
      if (autoExpandNonEmpty && (value[m.key]?.length ?? 0) > 0) s.add(m.key);
    }
    // 至少展开 LLM（最常用）
    if (s.size === 0) s.add('llm');
    return s;
  });

  // 当外部新塞入 detected（例如刚做完连通测试），自动展开新检测到的段
  React.useEffect(() => {
    if (!detected) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const k in detected) {
        if ((detected[k as ModelType] ?? 0) > 0 && !next.has(k as ModelType)) {
          next.add(k as ModelType);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [detected]);

  const toggle = (k: ModelType) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  };

  // 用户手动"显示其他类型"展开后,这些 key 也加入可见集
  const [forceShown, setForceShown] = React.useState<Set<ModelType>>(() => new Set());

  // 该类型是否进入"非空"判定:有模型 / 本轮 detected / 用户已展开 / 用户已 forceShown
  const isNonEmpty = (key: ModelType): boolean => {
    if (!hideEmpty) return true;
    if ((value[key]?.length ?? 0) > 0) return true;
    if ((detected?.[key] ?? 0) > 0) return true;
    if (forceShown.has(key)) return true;
    return false;
  };

  const visibleTypes = MODEL_TYPES.filter((m) => isNonEmpty(m.key));
  const hiddenTypes = MODEL_TYPES.filter((m) => !isNonEmpty(m.key));

  // 全部都空时(新厂商首次配置)→ 至少展示 LLM,让用户有地方添加模型
  const finalVisible =
    visibleTypes.length === 0
      ? MODEL_TYPES.filter((m) => m.key === 'llm')
      : visibleTypes;
  const finalHidden = MODEL_TYPES.filter(
    (m) => !finalVisible.some((v) => v.key === m.key),
  );

  // 跨类型搬运 — ModelLineEditor 内部"移到其他类型"按钮触发的回调。
  // 本组件持有完整 ModelsByType,在这里统一做 from→to 的搬运,Section / Editor 都不用感知 from。
  // 使用 latest value via setState callback 避免闭包陈旧(用户连续移多个时一次 commit 才不会丢)。
  const onMoveModel = React.useCallback(
    (modelId: string, from: ModelType, to: ModelType) => {
      if (from === to) return;
      // 注:ModelLineEditor 已经在自己的 onChange 里把这一行从 from 数组移除并通知父级;
      // 这里只负责把 modelId 加到 to 数组(去重)。
      const targetArr = value[to] ?? [];
      if (targetArr.includes(modelId)) return;
      onChange({ ...value, [to]: [...targetArr, modelId] });
    },
    [value, onChange],
  );

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {finalVisible.map((meta) => (
        <Section
          key={meta.key}
          meta={meta}
          models={value[meta.key] ?? []}
          detectedCount={detected?.[meta.key]}
          isOpen={expanded.has(meta.key)}
          onToggle={() => toggle(meta.key)}
          onModelsChange={(next) => onChange({ ...value, [meta.key]: next })}
          builtinSet={builtin?.[meta.key]}
          onMoveToType={(modelId, target) => onMoveModel(modelId, meta.key, target)}
        />
      ))}

      {finalHidden.length > 0 && (
        <HiddenTypesPicker
          hidden={finalHidden}
          onPick={(k) => {
            setForceShown((prev) => {
              const next = new Set(prev);
              next.add(k);
              return next;
            });
            // picked 类型自动展开,让用户立刻能添加
            setExpanded((prev) => {
              const next = new Set(prev);
              next.add(k);
              return next;
            });
          }}
        />
      )}
    </div>
  );
};

/** 底部"+ 显示其他类型"按钮:点开后浮一个 chip 列表,点其中一个把它加进可见集。
 *  设计上不用完整 Dropdown 组件,信息密度低、视觉权重轻,贴合"次要操作"。 */
const HiddenTypesPicker: React.FC<{
  hidden: readonly ModelTypeMeta[];
  onPick(k: ModelType): void;
}> = ({ hidden, onPick }) => {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] transition-colors',
          'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-1)] hover:text-[var(--cy-text-secondary)]',
        )}
      >
        <Plus className="h-3 w-3" />
        显示其他类型({hidden.length})
      </button>
      {open && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {hidden.map((meta) => {
            const Icon = meta.icon;
            const colors = modelTypeColors(meta.hue);
            return (
              <button
                key={meta.key}
                type="button"
                onClick={() => {
                  onPick(meta.key);
                  setOpen(false);
                }}
                className="inline-flex h-7 items-center gap-1 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-[11px] text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
                style={{ borderColor: colors.border }}
              >
                <Icon className="h-3 w-3" style={{ color: colors.fg }} />
                {t(`${meta.i18nKey}.label`)}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

const Section: React.FC<{
  meta: ModelTypeMeta;
  models: string[];
  detectedCount?: number;
  isOpen: boolean;
  onToggle(): void;
  onModelsChange(next: string[]): void;
  /** 该 type 下内置(catalog 默认)模型 id 集合;命中行隐藏删除按钮 */
  builtinSet?: ReadonlySet<string>;
  /** 跨类型搬运:用户在某行按"移到"选了别的类型 */
  onMoveToType?(modelId: string, target: ModelType): void;
}> = ({ meta, models, detectedCount, isOpen, onToggle, onModelsChange, builtinSet, onMoveToType }) => {
  const { t } = useTranslation();
  const Icon = meta.icon;
  const colors = modelTypeColors(meta.hue);
  const hasNew = (detectedCount ?? 0) > 0;
  const longLabel = t(`${meta.i18nKey}.long`);
  const hint = t(`${meta.i18nKey}.hint`);
  const placeholder = samplePlaceholder(meta.key).split('\n')[0] ?? '';

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className={cn(
          'flex w-full items-center gap-2 px-3 py-2 text-left transition-colors',
          'hover:bg-[var(--cy-surface-1)]',
        )}
      >
        <span
          className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md"
          style={{ backgroundColor: colors.bg, color: colors.fg }}
          aria-hidden
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="text-sm font-medium text-[var(--cy-text-primary)]">{longLabel}</span>
            <span
              className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full px-1.5 text-[10px] font-medium tabular-nums"
              style={{
                backgroundColor: colors.bg,
                color: colors.fg,
              }}
            >
              {models.length}
            </span>
            {hasNew && (
              <span
                className="inline-flex items-center gap-0.5 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 ring-1 ring-amber-200"
                title={t('modelMarket.accordion.newDetectedTitle')}
              >
                <Sparkles className="h-2.5 w-2.5" />
                {t('modelMarket.accordion.newDetected', { n: detectedCount ?? 0 })}
              </span>
            )}
          </span>
          <span className="block truncate text-[10px] text-[var(--cy-text-tertiary)]">{hint}</span>
        </span>
        <ChevronDown
          className={cn(
            'h-4 w-4 flex-shrink-0 text-[var(--cy-text-tertiary)] transition-transform',
            isOpen && 'rotate-180',
          )}
          aria-hidden
        />
      </button>

      {isOpen && (
        <div className="border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
          <ModelLineEditor
            value={models}
            onChange={onModelsChange}
            placeholder={placeholder}
            lockedIds={builtinSet}
            moveTargets={
              onMoveToType
                ? MODEL_TYPES
                    .filter((m) => m.key !== meta.key)
                    .map((m) => ({ key: m.key, label: t(`${m.i18nKey}.label`) }))
                : undefined
            }
            onMoveToType={
              onMoveToType
                ? (modelId, target) => onMoveToType(modelId, target as ModelType)
                : undefined
            }
          />
        </div>
      )}
    </div>
  );
};

function samplePlaceholder(t: ModelType): string {
  switch (t) {
    case 'llm':
      return 'deepseek-chat\ngpt-4o-mini';
    case 'embed':
      return 'text-embedding-3-small\nbge-m3';
    case 'rerank':
      return 'bge-reranker-v2-m3';
    case 'text2image':
      return 'dall-e-3\nstable-diffusion-3.5-large';
    case 'image2text':
      return 'gpt-4o\nqwen-vl-max';
    case 'image2image':
      return 'sdxl-refiner';
    case 'tts':
      return 'tts-1\ncosyvoice-v1';
    case 'asr':
      return 'whisper-1\nsensevoice-small';
  }
}
