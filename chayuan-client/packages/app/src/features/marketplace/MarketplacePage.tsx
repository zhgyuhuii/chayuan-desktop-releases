/**
 * 模型广场（单机版重构后）。
 *
 * 单机版定位:统一的「模型厂商配置入口」。所有用户共享同一份全局厂商配置,
 * 进来直接看到 7 个分类下的厂商卡片,点 ⚙️ 弹出 PlatformSettingsDialog 编辑;
 * 不再有「全网模型清单」「数据同步」「JSON 导入」「严格验证」等多人/云端语义。
 *
 * 信息架构(参考 CLAUDE.md §1.2):
 *
 *     ┌─ 分类 chips: 推荐 / 全部 / 本地 / 国内 / 国外 / 聚合 / 自定义 ┐
 *     ├─ 工具栏: 搜索框 + 「添加自定义」按钮 ────────────────┤
 *     └─ ProviderCard 网格(按分类 + 搜索过滤后的厂商列表) ──┘
 *
 * 历史:本页曾在最上方放过一个 VendorHeroStrip(8 高频厂商 + 「更多」),
 * 用户反馈视觉冗余 + 跟下方网格信息重复 — 2026-05-09 已删除。
 *
 * 数据源: ``usePlatformCatalogQuery`` —— catalog seed + DB 合并的厂商目录。
 * 写操作通过 PlatformSettingsDialog / PlatformCreateDialog 内部走
 * ``modelPlatform``,无需在本页直连 ``providers.ts``。
 */

import {
  type PlatformConfig,
  type ProviderCatalogEntry,
  type ProviderTag,
  getClientConfig,
} from '@chayuan/api';
import { cn } from '@chayuan/ui';
import { Plus, Search } from 'lucide-react';
import * as React from 'react';
import { useComposerStore } from '../../store/composer';
import { usePlatformCatalogQuery } from '../../store/modelPlatform';
import { PlatformCreateDialog } from './components/PlatformCreateDialog';
import { PlatformSettingsDialog } from './components/PlatformSettingsDialog';
import { ProviderCard } from './components/ProviderCard';

const CATEGORY_RECOMMENDED = '__recommended__';
const CATEGORY_ALL = '__all__';
const CATEGORY_CUSTOM = '__custom__';

/** 分类 chips:与后端 _CATEGORY_TAG_MAP / 后端 PROVIDER_CATALOG.tags 对齐。 */
const CATEGORY_TABS: ReadonlyArray<{
  value: string;
  label: string;
  /** 命中 catalog entry 的 tags;recommended/all/custom 走自定义谓词,无 tag */
  tag?: ProviderTag;
}> = [
  { value: CATEGORY_RECOMMENDED, label: '推荐' },
  { value: CATEGORY_ALL, label: '全部' },
  { value: '本地', label: '本地', tag: '本地' },
  { value: '国内', label: '国内', tag: '国内' },
  { value: '国外', label: '国外', tag: '国外' },
  { value: '聚合', label: '聚合', tag: '聚合' },
  { value: CATEGORY_CUSTOM, label: '自定义' },
];

/** 「推荐」补丁: catalog 没标 "推荐" 也兜底进推荐位的热门厂商。 */
const EXTRA_RECOMMENDED_PIDS = new Set<string>([
  'volcengine', // 豆包
  'moonshot', // Kimi
  'minimax',
  'openai',
  'anthropic',
]);

/** 把 catalog entry 适配成 ProviderCard / SettingsDialog 需要的 PlatformConfig。 */
function catalogEntryToPlatformConfig(e: ProviderCatalogEntry): PlatformConfig {
  return {
    platform_name: e.pid,
    platform_type: e.platform_type,
    api_base_url: e.api_base_url || e.default_api_base,
    api_key: '', // 永不回前端
    api_proxy: '',
    api_concurrencies: e.api_concurrencies,
    enabled: e.enabled,
    auto_detect_model: e.auto_detect_model,
    llm_models: e.llm_models,
    embed_models: e.embed_models,
    text2image_models: e.text2image_models,
    image2text_models: e.image2text_models,
    image2image_models: [],
    rerank_models: e.rerank_models,
    speech2text_models: e.speech2text_models,
    text2speech_models: e.text2speech_models,
    disabled_models: e.disabled_models,
    description: e.description,
  };
}

/** catalog 直接给文件名(如 ``deepseek.png``);拼到后端静态目录,与 VendorHeroStrip 对齐。 */
function resolveCatalogLogo(file?: string): string | undefined {
  if (!file) return undefined;
  const base = (getClientConfig().baseURL || '').replace(/\/api\/?$/, '').replace(/\/$/, '');
  return `${base}/img/model_logos/${file}`;
}

function filterByCategory(
  catalog: ProviderCatalogEntry[],
  category: string,
): ProviderCatalogEntry[] {
  if (category === CATEGORY_ALL) return catalog;
  if (category === CATEGORY_RECOMMENDED) {
    return catalog.filter(
      (e) => (e.tags ?? []).includes('推荐') || EXTRA_RECOMMENDED_PIDS.has(e.pid),
    );
  }
  if (category === CATEGORY_CUSTOM) {
    return catalog.filter((e) => e._custom || (e.tags ?? []).includes('自定义'));
  }
  // 否则 category 即 ProviderTag(本地/国内/国外/聚合)
  return catalog.filter((e) => (e.tags ?? []).includes(category));
}

function filterBySearch(
  catalog: ProviderCatalogEntry[],
  search: string,
): ProviderCatalogEntry[] {
  const q = search.trim().toLowerCase();
  if (!q) return catalog;
  return catalog.filter((e) => {
    const hay = [
      e.pid,
      e.display_name,
      e.platform_type,
      e.description,
      ...(e.llm_models ?? []),
      ...(e.embed_models ?? []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return hay.includes(q);
  });
}

export interface MarketplacePageProps {
  /** 嵌入模式(``AiPlatformPanel`` 第三行):去掉 max-w / page padding,让父级控制留白。 */
  embedded?: boolean;
  /** 隐藏 ``tags`` 含 "本地" 的厂商;``RuntimeCenter`` 已经独立配本地推理,避免重复。 */
  excludeLocal?: boolean;
  /**
   * 深链:进入页面时自动打开指定 ``pid`` 厂商的 PlatformSettingsDialog
   * (等价于用户点了那张卡的 ⚙️)。
   *
   * 来源 = 路由 ``/marketplace?configure=<pid>`` 的 search param,由 page-registry
   * 从 ``tab.path`` 解析后注入。catalog 是异步加载的,所以本组件用 effect 等
   * catalog ready 后再匹配并开窗;开窗后会通过 ``onConfigureConsumed`` 把 URL 上的
   * ``configure`` 参数清掉,避免返回 / 刷新重复弹窗。
   */
  configurePid?: string;
  /** ``configurePid`` 对应的弹窗已打开后回调 —— 用于清掉 URL 上的 ``configure`` 参数。 */
  onConfigureConsumed?(): void;
}

export const MarketplacePage: React.FC<MarketplacePageProps> = ({
  embedded = false,
  excludeLocal = false,
  configurePid,
  onConfigureConsumed,
}) => {
  const setModel = useComposerStore((s) => s.setModel);
  const currentModel = useComposerStore((s) => s.modelId);
  const [activeCategory, setActiveCategory] = React.useState<string>(CATEGORY_RECOMMENDED);
  const [search, setSearch] = React.useState('');
  const [settingsTarget, setSettingsTarget] = React.useState<PlatformConfig | null>(null);
  const [settingsExistsInDb, setSettingsExistsInDb] = React.useState(true);
  const [createOpen, setCreateOpen] = React.useState(false);

  const catalogQ = usePlatformCatalogQuery();
  const rawCatalog: ProviderCatalogEntry[] = catalogQ.data ?? [];
  const catalog: ProviderCatalogEntry[] = React.useMemo(() => {
    if (!excludeLocal) return rawCatalog;
    return rawCatalog.filter((v) => !(v.tags ?? []).includes('本地'));
  }, [excludeLocal, rawCatalog]);

  // ``excludeLocal`` 下若用户停在「本地」tab,自动 snap 回「推荐」,避免空目录。
  React.useEffect(() => {
    if (excludeLocal && activeCategory === '本地') {
      setActiveCategory(CATEGORY_RECOMMENDED);
    }
  }, [excludeLocal, activeCategory]);

  const categoryTabs = React.useMemo(
    () => (excludeLocal ? CATEGORY_TABS.filter((t) => t.value !== '本地') : CATEGORY_TABS),
    [excludeLocal],
  );

  // 按分类 + 搜索过滤
  const filtered = React.useMemo(() => {
    let list = filterByCategory(catalog, activeCategory);
    list = filterBySearch(list, search);
    return list;
  }, [activeCategory, catalog, search]);

  const onOpenSettings = React.useCallback((e: ProviderCatalogEntry) => {
    setSettingsTarget(catalogEntryToPlatformConfig(e));
    setSettingsExistsInDb(!!e.configured);
  }, []);

  // 深链:``/marketplace?configure=<pid>`` 进来时,等 catalog 加载完后自动
  // 打开对应厂商的 PlatformSettingsDialog(等价于点了那张卡的 ⚙️)。
  // 用 ref 保证一次深链只消费一次 —— 开窗后立刻回调清 URL 参数,避免重复弹。
  const consumedConfigureRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!configurePid) return;
    if (consumedConfigureRef.current === configurePid) return;
    // catalog 还没 ready —— 等下一次 catalog 变化的 effect 再试
    if (rawCatalog.length === 0) return;
    const entry = rawCatalog.find((e) => e.pid === configurePid);
    consumedConfigureRef.current = configurePid;
    if (entry) onOpenSettings(entry);
    // 不论是否命中都清掉 URL 参数:命中 → 已开窗;未命中 → 厂商不在目录里,
    // 留着参数也没意义,清掉避免刷新 / 返回反复触发。
    onConfigureConsumed?.();
  }, [configurePid, rawCatalog, onOpenSettings, onConfigureConsumed]);

  // 自动 snap:localStorage 持久化的 modelId 可能已不存在(厂商被禁用 / 模型被删等)。
  // 第一次拿到模型清单后,若 currentModel 不在任何启用平台的 llm_models 中 →
  // 自动切到第一个可用模型,避免用户发出去 400。
  const allUsableModels = React.useMemo(() => {
    const out: string[] = [];
    for (const e of catalog) {
      if (!e.enabled || (!e.api_base_url && !e.configured)) continue;
      out.push(...(e.llm_models ?? []));
    }
    return out;
  }, [catalog]);
  const snappedRef = React.useRef(false);
  React.useEffect(() => {
    if (snappedRef.current) return;
    if (allUsableModels.length === 0) return;
    if (allUsableModels.includes(currentModel)) {
      snappedRef.current = true;
      return;
    }
    setModel(allUsableModels[0]!);
    snappedRef.current = true;
  }, [allUsableModels, currentModel, setModel]);

  return (
    <div
      className={cn(
        'flex w-full flex-col gap-4',
        embedded ? '' : 'mx-auto h-full max-w-7xl px-6 py-6',
      )}
    >
      <section className="rounded-3xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-4 shadow-[var(--cy-shadow-sm)]">
        <div className="flex flex-wrap items-center gap-2">
          {categoryTabs.map((tab) => (
            <FilterPill
              key={tab.value}
              active={activeCategory === tab.value}
              onClick={() => setActiveCategory(tab.value)}
            >
              {tab.label}
            </FilterPill>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <div className="relative min-w-[260px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索厂商名称、平台类型或模型..."
              className="h-10 w-full rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] pl-9 pr-3 text-sm text-[var(--cy-text-primary)] outline-none transition-colors focus:border-[var(--cy-brand-400)]"
            />
          </div>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            title="添加自部署的 OpenAI 兼容厂商(vLLM / OneAPI / 自家网关等)"
            className="inline-flex h-10 items-center gap-2 rounded-2xl bg-[var(--cy-brand-500)] px-4 text-sm font-medium text-white shadow-sm transition-colors hover:bg-[var(--cy-brand-600)]"
          >
            <Plus className="h-4 w-4" />
            添加自定义
          </button>
        </div>
      </section>

      {/* 厂商网格 —— 按分类 + 搜索过滤后的 ProviderCard 列表。 */}
      {catalogQ.isLoading ? (
        <p className="rounded-3xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-12 text-center text-sm text-[var(--cy-text-tertiary)]">
          加载厂商目录中...
        </p>
      ) : filtered.length === 0 ? (
        <p className="rounded-3xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-12 text-center text-sm text-[var(--cy-text-tertiary)]">
          {search.trim()
            ? '没有匹配的厂商,试试调整搜索词或切换分类。'
            : activeCategory === CATEGORY_CUSTOM
              ? '暂无自定义厂商。点击右上角「添加自定义」接入 vLLM / OneAPI 等自部署服务。'
              : '该分类下暂无厂商。'}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((entry) => (
            <ProviderCard
              key={entry.pid}
              platform={catalogEntryToPlatformConfig(entry)}
              activeModel={currentModel}
              onSelectModel={setModel}
              onOpenSettings={() => onOpenSettings(entry)}
              displayName={entry.display_name}
              tags={entry.tags}
              applyKeyUrl={entry.apply_key_url}
              installGuideUrl={entry.install_guide_url}
              modelMetadata={entry.model_metadata}
              logoOverride={resolveCatalogLogo(entry.logo)}
            />
          ))}
        </div>
      )}

      <PlatformSettingsDialog
        platform={settingsTarget}
        existsInDb={settingsExistsInDb}
        onOpenChange={(o) => {
          if (!o) setSettingsTarget(null);
        }}
      />
      <PlatformCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(name) => {
          setActiveCategory(CATEGORY_CUSTOM);
          const entry = catalogQ.data?.find((x) => x.pid === name);
          if (entry) onOpenSettings(entry);
        }}
      />
    </div>
  );
};

const FilterPill: React.FC<{
  active: boolean;
  onClick(): void;
  children: React.ReactNode;
}> = ({ active, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      'rounded-full px-3 py-1.5 text-xs transition-colors',
      active
        ? 'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)] shadow-sm'
        : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
    )}
  >
    {children}
  </button>
);
