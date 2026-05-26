/**
 * 厂商卡片(参考图 模型广场.jpg 中的两列卡片)。
 *
 * 设计原则:
 *   - logo 永远彩色 —— 它代表品牌身份,不应被状态干扰
 *   - 状态信息用「定位明确、信息密度低、视觉权重小」的元素承载:
 *       a) 左侧 3px 状态色条:emerald(可用) / amber(未配置) / slate(已禁用)
 *       b) 右上角状态徽:仅在「未配置 / 已禁用」时出现,正常态隐身
 *       c) 底部 CTA 文案:未配置时换成「点击 ⚙️ 立即接入」
 *   - 不再 grayscale 整张卡;dimmed 仅作用在 ModelTagsRow 的 chip(不可点)
 *
 * 一卡 = 一个 PlatformConfig:
 *   左:logo + 平台名 + tags
 *   描述:一行
 *   底部:ModelTagsRow 或 CTA
 *   右下:⚙️ 设置(admin 可见)
 */

import { type ModelMetadata, type PlatformConfig, resolveLogoUrl } from '@chayuan/api';
import { cn } from '@chayuan/ui';
import { getPlatform } from '@chayuan/platform-shared';
import { Download as DownloadIcon, KeyRound, Settings as SettingsIcon } from 'lucide-react';
import * as React from 'react';
import { useTranslation } from '../../../i18n';
import {
  CAPABILITY_ANY,
  type CapabilityKey,
  type GroupedModels,
  groupModelsByType,
  modelTypeColors,
} from '../lib/modelTypes';
import { ModelTagsRow } from './ModelTagsRow';
import { getInstallGuide } from '../installGuides/data';
import { InstallGuideDialog } from './InstallGuideDialog';

export interface ProviderCardProps {
  platform: PlatformConfig;
  /** 当前用户全局选中的模型 id;若属于本平台则 chip 高亮 */
  activeModel?: string;
  onSelectModel(model: string): void;
  /** 点击 ⚙️;非 admin 可不传以隐藏入口 */
  onOpenSettings?(): void;
  /** 描述被父级 hero 等场景动态覆盖时用 */
  descriptionOverride?: string;
  /** catalog 模式下,优先用后端返回的 logo URL(/img/model_logos/<file>) */
  logoOverride?: string;
  /** catalog 模式下,UI 显示名(中文/友好名),与 platform_name 解耦 */
  displayName?: string;
  /** catalog tags(国内/国外/本地/聚合/推荐 等),用于卡片右上角徽 */
  tags?: string[];
  /** 卡片右下角"申请 API Key"链接(云厂商) */
  applyKeyUrl?: string;
  /** 本地服务"安装指南"链接(优先级高于 applyKeyUrl,只对本地厂商有意义) */
  installGuideUrl?: string;
  /**
   * 当前页面级 capability filter；卡片内部 chip 行据此决定展示哪类模型：
   *   - CAPABILITY_ANY：按类型分组紧凑展示（每类一行带图标）
   *   - 'llm' / 'embed' / ... ：只展示该类型的 chip
   */
  activeCapability?: CapabilityKey;
  /** 模型 id → 元信息 索引;chip hover 时显示 description / release_date / 上下文 / 性能 */
  modelMetadata?: Record<string, ModelMetadata>;
}

type Status = 'active' | 'unconfigured' | 'disabled';

function isUnconfigured(p: PlatformConfig): boolean {
  return !p.api_base_url && !p.api_key;
}

function statusOf(p: PlatformConfig): Status {
  if (isUnconfigured(p)) return 'unconfigured';
  if (!p.enabled) return 'disabled';
  return 'active';
}

/** 全部 8 类模型按类型分组（已扣除 disabled_models 黑名单） */
function buildGroups(p: PlatformConfig): GroupedModels[] {
  return groupModelsByType(p, { excludeDisabled: true, onlyNonEmpty: true });
}

const STATUS_BAR: Record<Status, string> = {
  active: 'bg-emerald-400',
  unconfigured: 'bg-amber-400',
  disabled: 'bg-slate-300',
};

export const ProviderCard: React.FC<ProviderCardProps> = React.memo(
  ({
    platform,
    activeModel,
    onSelectModel,
    onOpenSettings,
    descriptionOverride,
    logoOverride,
    displayName,
    tags,
    applyKeyUrl,
    installGuideUrl,
    activeCapability = CAPABILITY_ANY,
    modelMetadata,
  }) => {
    const { t } = useTranslation();
    const status = statusOf(platform);
    const interactive = status === 'active';
    const groups = React.useMemo(() => buildGroups(platform), [platform]);
    // logo 用第一个 LLM 优先，没有就用任意第一个有数据的类型的第一个模型
    const firstModelForLogo = React.useMemo(() => {
      const llm = platform.llm_models?.[0];
      if (llm) return llm;
      for (const g of groups) {
        if (g.models[0]) return g.models[0];
      }
      return undefined;
    }, [platform, groups]);
    const logoUrl = React.useMemo(
      () => logoOverride ?? resolveLogoUrl(platform.platform_name, firstModelForLogo),
      [logoOverride, platform.platform_name, firstModelForLogo],
    );
    const showName = displayName ?? platform.platform_name;
    // 当前 capability 过滤后该卡的可见模型集合（用于决定底部展示什么）
    const filtered: GroupedModels[] = React.useMemo(() => {
      if (activeCapability === CAPABILITY_ANY) return groups;
      return groups.filter((g) => g.type === activeCapability);
    }, [groups, activeCapability]);

    // 本地厂商「安装指南」按钮:有内置 InstallGuide 数据的厂商弹离线 dialog,
    // 否则回退到 openExternal 跳官网。data 在 installGuides/data.ts 维护。
    const installGuide = React.useMemo(
      () => getInstallGuide(platform.platform_name),
      [platform.platform_name],
    );
    const [guideOpen, setGuideOpen] = React.useState(false);

    return (
      <div
        className={cn(
          'group relative flex h-[190px] flex-col gap-2 overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-4 pl-5 transition-all',
          'hover:-translate-y-0.5 hover:border-[var(--cy-brand-300)] hover:shadow-[var(--cy-shadow-md)]',
        )}
      >
        {/* 左侧 3px 状态色条 */}
        <span
          aria-hidden
          className={cn('absolute left-0 top-0 h-full w-[3px]', STATUS_BAR[status])}
        />

        <div className="flex h-[66px] shrink-0 items-start gap-3">
          {/* logo 永远彩色,任何状态都不 dim */}
          {logoUrl ? (
            <img
              src={logoUrl}
              alt={showName}
              className="h-9 w-9 flex-shrink-0 rounded-md object-contain"
            />
          ) : (
            <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md bg-[var(--cy-surface-2)] text-sm font-semibold text-[var(--cy-text-secondary)]">
              {showName.slice(0, 1).toUpperCase()}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <p className="min-w-0 truncate text-sm font-semibold text-[var(--cy-text-primary)]">
                {showName}
              </p>
              {/* 状态徽 — 三态都显式标记,让用户一眼看出"已启用 / 未配置 / 已禁用" */}
              {status === 'active' && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-medium text-emerald-700 ring-1 ring-emerald-200/70"
                  title={t('modelMarket.card.titleEnabled', { n: groups.reduce((s, g) => s + g.models.length, 0) })}
                >
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  {t('modelMarket.card.enabled')}
                </span>
              )}
              {status === 'unconfigured' && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 ring-1 ring-amber-200/70"
                  title={t('modelMarket.card.titleUnconfigured')}
                >
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
                  {t('modelMarket.card.unconfigured')}
                </span>
              )}
              {status === 'disabled' && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-600 ring-1 ring-slate-200"
                  title={t('modelMarket.card.titleDisabled')}
                >
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400" />
                  {t('modelMarket.card.disabled')}
                </span>
              )}
              {/* tag 徽:推荐/国内/国外/本地/聚合 */}
              {tags && tags.length > 0 && (
                <span className="ml-auto flex shrink-0 flex-wrap items-center gap-1">
                  {tags.slice(0, 2).map((tg) => (
                    <span
                      key={tg}
                      className={cn(
                        'rounded-full px-1.5 py-0.5 text-[9px]',
                        tg === '推荐'
                          ? 'bg-amber-100 text-amber-700'
                          : tg === '国内'
                            ? 'bg-rose-50 text-rose-700'
                            : tg === '国外'
                              ? 'bg-sky-50 text-sky-700'
                              : tg === '本地'
                                ? 'bg-emerald-50 text-emerald-700'
                                : tg === '聚合'
                                  ? 'bg-violet-50 text-violet-700'
                                  : 'bg-[var(--cy-surface-2)] text-[var(--cy-text-tertiary)]',
                      )}
                    >
                      {tg}
                    </span>
                  ))}
                </span>
              )}
            </div>
            <p className="line-clamp-2 mt-0.5 text-[11px] text-[var(--cy-text-tertiary)]">
              {descriptionOverride || platform.description || platform.platform_type}
            </p>
          </div>
        </div>

        {/* 底部：按 capability 显示模型；不可用状态给 CTA 文案
            pr-16 为右下浮层按钮(申请 Key / 下载 / ⚙️)预留 ~64px 安全区,
            避免 chip 行的"更多 ▼"被绝对定位的按钮压住,导致点击事件命中浮层。
            两个按钮 (28+28) + gap (2) + right-1.5 (6) ≈ 64px,留富裕空间避免边缘碰边缘 */}
        <div className="min-h-0 flex-1 pr-16 pt-2">
          {interactive && filtered.length > 0 ? (
            activeCapability === CAPABILITY_ANY ? (
              // 全能力：每类一行，前置类型徽，行内 ModelTagsRow
              <div className="flex max-h-[78px] flex-col gap-1.5 overflow-hidden">
                {filtered.slice(0, 3).map((g) => (
                  <TypeRow
                    key={g.type}
                    group={g}
                    activeModel={activeModel}
                    onSelectModel={onSelectModel}
                    metadata={modelMetadata}
                  />
                ))}
                {filtered.length > 3 && (
                  <p className="text-[10px] text-[var(--cy-text-tertiary)]">
                    {t('modelMarket.card.moreTypes', { n: filtered.length - 3 })}
                  </p>
                )}
              </div>
            ) : (
              // 单一类型：直接 ModelTagsRow
              <ModelTagsRow
                models={filtered[0]!.models}
                activeModel={activeModel}
                onSelect={onSelectModel}
                metadata={modelMetadata}
              />
            )
          ) : status === 'unconfigured' ? (
            <CtaLine
              tone="amber"
              text={
                onOpenSettings
                  ? t('modelMarket.card.ctaConfigure')
                  : t('modelMarket.card.unconfigured')
              }
            />
          ) : status === 'disabled' ? (
            <CtaLine
              tone="slate"
              text={
                onOpenSettings ? t('modelMarket.card.ctaReenable') : t('modelMarket.card.disabled')
              }
            />
          ) : interactive && filtered.length === 0 && activeCapability !== CAPABILITY_ANY ? (
            // 当前能力 filter 下该厂商无对应模型（理论上 page 已 filter 不该到这里，兜底）
            <CtaLine tone="slate" text={t('modelMarket.card.noModelsForCapability')} />
          ) : (
            // active 但 0 模型（罕见，刚 create 未填）
            <CtaLine
              tone="slate"
              text={
                onOpenSettings
                  ? t('modelMarket.card.ctaAddModel')
                  : t('modelMarket.card.noModelsAvailable')
              }
            />
          )}
        </div>

        {/* 右下行动条:本地厂商优先「安装指南」,否则「申请 Key」;再后面是 ⚙️ 设置(admin) */}
        <div className="absolute right-1.5 bottom-1.5 flex items-center gap-0.5">
          {(installGuide || installGuideUrl || applyKeyUrl) && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                // 本地厂商有内置 InstallGuide 数据 → 弹离线 dialog,无需联网
                if (installGuide) {
                  setGuideOpen(true);
                  return;
                }
                // 否则:云厂商「申请 Key」/ 没有内置指南的本地厂商「安装指南外链」
                // 走 openExternal 唤起系统浏览器(Tauri webview 下 a target=_blank 不响应)
                const url = installGuideUrl || applyKeyUrl;
                if (!url) return;
                void getPlatform().shell.openExternal(url);
              }}
              aria-label={
                installGuide || installGuideUrl
                  ? `${platform.platform_name} 安装指南`
                  : `${platform.platform_name} 申请 API Key`
              }
              title={installGuide || installGuideUrl ? '安装指南' : '申请 API Key'}
              className={cn(
                'inline-flex h-7 w-7 items-center justify-center rounded-full text-[var(--cy-text-tertiary)] transition-all',
                'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
                // 未配置时一直露 + 边框,提示用户可以去申请 / 安装
                status === 'unconfigured'
                  ? 'opacity-100 ring-1 ring-amber-200/70 text-amber-700 hover:bg-amber-50'
                  : 'opacity-0 group-hover:opacity-100',
              )}
            >
              {installGuide || installGuideUrl ? (
                <DownloadIcon className="h-3.5 w-3.5" />
              ) : (
                <KeyRound className="h-3.5 w-3.5" />
              )}
            </button>
          )}
          {/* 本地厂商离线安装指南 dialog;guideOpen=false 时不渲染内容,无开销 */}
          {installGuide && (
            <InstallGuideDialog
              guide={guideOpen ? installGuide : null}
              onOpenChange={(o) => setGuideOpen(o)}
            />
          )}
          {onOpenSettings && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpenSettings();
              }}
              aria-label={`配置 ${platform.platform_name}`}
              title="配置"
              className={cn(
                'inline-flex h-7 w-7 items-center justify-center rounded-full text-[var(--cy-text-tertiary)] transition-all',
                'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
                // active 时只在 hover 显;非 active 时一直显,提示「这里能配」
                interactive
                  ? 'opacity-0 group-hover:opacity-100'
                  : 'opacity-70 group-hover:opacity-100',
              )}
            >
              <SettingsIcon className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  },
);
ProviderCard.displayName = 'ProviderCard';

/** "全能力"模式下的一行：左侧类型徽 + 右侧 ModelTagsRow（chip 数限制 2 行内紧凑） */
const TypeRow: React.FC<{
  group: GroupedModels;
  activeModel?: string;
  onSelectModel(m: string): void;
  metadata?: Record<string, ModelMetadata>;
}> = ({ group, activeModel, onSelectModel, metadata }) => {
  const { t } = useTranslation();
  const colors = modelTypeColors(group.meta.hue);
  const Icon = group.meta.icon;
  return (
    <div className="flex items-center gap-1.5">
      <span
        className="inline-flex h-5 flex-shrink-0 items-center gap-0.5 rounded-full px-1.5 text-[10px] font-medium"
        style={{ backgroundColor: colors.bg, color: colors.fg }}
        title={t(`${group.meta.i18nKey}.long`)}
      >
        <Icon className="h-2.5 w-2.5" />
        {t(`${group.meta.i18nKey}.label`)}
      </span>
      <div className="min-w-0 flex-1">
        <ModelTagsRow
          models={group.models}
          activeModel={activeModel}
          onSelect={onSelectModel}
          maxVisible={2}
          metadata={metadata}
        />
      </div>
    </div>
  );
};

/** 信息密度低的状态文案:小圆点 + 单行字,不抢戏 */
const CtaLine: React.FC<{ tone: 'amber' | 'slate'; text: string }> = ({ tone, text }) => (
  <p
    className={cn(
      'inline-flex items-center gap-1.5 text-[11px]',
      tone === 'amber' ? 'text-amber-700' : 'text-[var(--cy-text-tertiary)]',
    )}
  >
    <span
      aria-hidden
      className={cn('h-1 w-1 rounded-full', tone === 'amber' ? 'bg-amber-400' : 'bg-slate-300')}
    />
    {text}
  </p>
);
