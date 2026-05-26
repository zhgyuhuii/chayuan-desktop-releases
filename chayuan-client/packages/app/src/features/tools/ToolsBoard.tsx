/**
 * ToolsBoard —— 工具市场(/tools)。
 *
 * P2:内置工具切到 ``/tools/catalog``(catalog + 当前 yaml 值合并视图),
 *     卡片右上角 ⚙️ → ``ToolConfigDialog`` 改字段。
 *
 * P3:同时拉 ``/tools/custom``(用户定义的 HTTP 工具),与内置工具合并到
 *     同一卡片网格;header 加 [+ 单个 API] 按钮 → ``CustomToolFormDialog``;
 *     自定义工具卡片的 ⚙️ → 同一 dialog 的编辑模式(含删除按钮)。
 *
 * 卡片击行为不变:整张卡片切 Composer 的 selectedTools。
 */

import * as React from 'react';
import { Download, Loader2, Plus, Search, Wrench } from 'lucide-react';
import { Button, Input, cn } from '@chayuan/ui';
import type { CustomToolSpec, ToolCard as ToolCardData } from '@chayuan/api';
import { useComposerStore } from '../../store/composer';
import { ToolCard } from './ToolCard';
import { ToolConfigDialog } from './components/ToolConfigDialog';
import { CustomToolFormDialog, type CustomToolFormMode } from './components/CustomToolFormDialog';
import { OpenApiImportDialog } from './components/OpenApiImportDialog';
import { customToCard } from './lib/customToCard';
import { useCustomTools, useToolsCatalog } from './hooks';

type StatusTab = 'all' | 'enabled' | 'custom';

interface CombinedCard extends ToolCardData {
  /** 仅 builtin=false 时存在 — 自定义工具的原始 spec(编辑弹窗回填用) */
  _customSpec?: CustomToolSpec;
}

export const ToolsBoard: React.FC = () => {
  const [tab, setTab] = React.useState<StatusTab>('all');
  const [keyword, setKeyword] = React.useState('');
  // 配置弹窗:builtin → ToolConfigDialog;custom → CustomToolFormDialog 编辑模式
  const [configKey, setConfigKey] = React.useState<string | null>(null);
  const [customForm, setCustomForm] = React.useState<{
    mode: CustomToolFormMode; initial?: CustomToolSpec | null;
  } | null>(null);
  const [openApiOpen, setOpenApiOpen] = React.useState(false);

  const selected = useComposerStore((s) => s.selectedTools);
  const setTools = useComposerStore((s) => s.setTools);

  const catalogQ = useToolsCatalog();
  const customQ = useCustomTools();

  const builtinCards: ToolCardData[] = catalogQ.data?.cards ?? [];
  const customSpecs: CustomToolSpec[] = customQ.data ?? [];
  // 拼成统一卡片列表;自定义工具排前面(用户更关心自己加的)
  const allCards: CombinedCard[] = React.useMemo(() => {
    const customCards: CombinedCard[] = customSpecs.map((s) => ({
      ...customToCard(s),
      _customSpec: s,
    }));
    return [...customCards, ...builtinCards];
  }, [builtinCards, customSpecs]);

  const data = React.useMemo(() => {
    switch (tab) {
      case 'enabled': return allCards.filter((c) => c.enabled);
      case 'custom':  return allCards.filter((c) => !c.builtin);
      case 'all':
      default:        return allCards;
    }
  }, [allCards, tab]);

  const filtered = React.useMemo(() => {
    if (!keyword.trim()) return data;
    const q = keyword.toLowerCase();
    return data.filter((c) => {
      const hay =
        `${c.key} ${c.title} ${c.summary} ${c.description} ${c.tags.join(' ')}`.toLowerCase();
      return hay.includes(q);
    });
  }, [data, keyword]);

  const toggle = (key: string) =>
    setTools(selected.includes(key) ? selected.filter((x) => x !== key) : [...selected, key]);

  const openConfig = (card: CombinedCard) => {
    if (card.builtin) {
      setConfigKey(card.key);
    } else if (card._customSpec) {
      setCustomForm({ mode: 'edit', initial: card._customSpec });
    }
  };

  const activeBuiltinCard = React.useMemo(
    () => builtinCards.find((c) => c.key === configKey) ?? null,
    [builtinCards, configKey],
  );

  const enabledCount = allCards.filter((c) => c.enabled).length;
  const customCount = customSpecs.length;
  const loading = catalogQ.isLoading || customQ.isLoading;
  const error = (catalogQ.error || customQ.error) as Error | null;

  return (
    <div className="mx-auto flex h-full w-full max-w-7xl flex-col px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-[var(--cy-text-primary)]">
            <Wrench className="h-5 w-5 text-violet-600" /> 工具
          </h1>
          <p className="mt-0.5 text-xs text-[var(--cy-text-tertiary)]">
            内置 + 自定义工具集合;hover 卡片右上角点 ⚙️ 配置参数,选中卡片在下次对话作为可调用工具组
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCustomForm({ mode: 'create' })}
            className="h-8 text-xs"
          >
            <Plus className="h-3.5 w-3.5" /> 单个 API
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setOpenApiOpen(true)}
            className="h-8 text-xs"
            title="从 OpenAPI / Swagger spec 批量导入接口为自定义工具"
          >
            <Download className="h-3.5 w-3.5" /> 批量 OpenAPI
          </Button>
          <div className="inline-flex rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-0.5 text-xs">
            {(['all', 'enabled', 'custom'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={cn(
                  'rounded-full px-3 py-1 transition-colors',
                  tab === t
                    ? 'bg-[var(--cy-surface-base)] font-medium text-[var(--cy-text-primary)] shadow-sm'
                    : 'text-[var(--cy-text-secondary)]',
                )}
              >
                {t === 'all'
                  ? `全部 ${allCards.length}`
                  : t === 'enabled'
                  ? `已启用 ${enabledCount}`
                  : `自定义 ${customCount}`}
              </button>
            ))}
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索工具名 / 描述 / 标签"
              className="h-8 w-56 rounded-full pl-7 text-xs"
            />
          </div>
        </div>
      </header>

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex h-32 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : error ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm font-medium text-[var(--cy-text-primary)]">加载失败</p>
            <p className="max-w-md break-words text-xs text-[var(--cy-text-secondary)]">
              {error.message}
            </p>
            <Button size="sm" variant="outline" onClick={() => { void catalogQ.refetch(); void customQ.refetch(); }}>
              重试
            </Button>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            keyword={keyword}
            tab={tab}
            onCreateCustom={() => setCustomForm({ mode: 'create' })}
            onSeeAll={() => setTab('all')}
          />
        ) : (
          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}
          >
            {filtered.map((c, i) => (
              <ToolCard
                key={c.key}
                tool={c}
                index={i}
                selected={selected.includes(c.key)}
                onToggle={() => toggle(c.key)}
                onOpenConfig={() => openConfig(c)}
              />
            ))}
          </div>
        )}
      </div>

      {selected.length > 0 && (
        <footer className="flex flex-shrink-0 items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-2 text-xs">
          <span className="text-[var(--cy-text-secondary)]">
            已选 <b className="text-[var(--cy-text-primary)]">{selected.length}</b> 个工具,
            将在下次对话生效
          </span>
          <Button size="sm" variant="ghost" onClick={() => setTools([])}>
            清空
          </Button>
        </footer>
      )}

      {/* 内置工具配置弹窗 */}
      <ToolConfigDialog
        open={!!configKey}
        onOpenChange={(o) => { if (!o) setConfigKey(null); }}
        card={activeBuiltinCard}
      />

      {/* 自定义工具表单弹窗(新建 / 编辑共用) */}
      {customForm && (
        <CustomToolFormDialog
          open
          onOpenChange={(o) => { if (!o) setCustomForm(null); }}
          mode={customForm.mode}
          initial={customForm.initial ?? null}
        />
      )}

      {/* P4:OpenAPI 批量导入弹窗 */}
      <OpenApiImportDialog open={openApiOpen} onOpenChange={setOpenApiOpen} />
    </div>
  );
};

// ── 空态 ────────────────────────────────────────────────────────

const EmptyState: React.FC<{
  keyword: string;
  tab: StatusTab;
  onCreateCustom(): void;
  onSeeAll(): void;
}> = ({ keyword, tab, onCreateCustom, onSeeAll }) => (
  <div className="flex h-full min-h-[260px] flex-col items-center justify-center gap-3 text-center">
    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
      <Wrench className="h-7 w-7" />
    </div>
    <p className="text-sm font-medium text-[var(--cy-text-primary)]">
      {keyword
        ? '没有匹配工具'
        : tab === 'enabled'
        ? '暂无启用的工具'
        : tab === 'custom'
        ? '还没有添加自定义工具'
        : '暂无可用工具'}
    </p>
    <div className="flex items-center gap-2">
      {tab !== 'all' && (
        <Button size="sm" variant="outline" onClick={onSeeAll}>
          查看全部工具
        </Button>
      )}
      {tab === 'custom' && (
        <Button size="sm" onClick={onCreateCustom}>
          <Plus className="h-3.5 w-3.5" /> 添加单个 API
        </Button>
      )}
    </div>
  </div>
);
