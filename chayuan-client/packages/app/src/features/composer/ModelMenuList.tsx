/**
 * 共享的"分组折叠 + 搜索"模型菜单。
 *
 * 同时被 Composer 顶行 Pill(ComposerModelPill) 和 ChatPage 顶栏(ModelPicker)使用。
 *
 * 行为:
 *   - 按 platform_name 分组,每组一个可点击的标题(logo + 名 + count + 折叠 ▶/▼)
 *   - 默认仅展开"当前选中模型所在的组";其它组初始折叠 → 下拉栏目不再无限拉长
 *   - 标题状态记到 localStorage('cy.modelPicker.collapsed.v1') 跨会话保留,
 *     新厂商首次出现时按"默认折叠"对待(避免一次性又把列表撑开)
 *   - 顶部一行筛选输入,实时缩窄分组与项;命中时自动展开命中组
 *   - 只展示对话模型:后端标注 `model_type='llm'` 的模型;旧后端未标注类型时兼容放行
 *
 * 性能:
 *   - 分组与过滤在 useMemo 里 O(n);n 通常 < 200 没必要 windowing
 *   - localStorage 写入 throttle 到下一帧,避免快速点击连发同步写
 */

import type { RawModelItem } from '@chayuan/api';
import { cn } from '@chayuan/ui';
import { Check, ChevronDown, ChevronRight, Search, TriangleAlert } from 'lucide-react';
import type { ModalityCapability } from '@chayuan/transport';
import * as React from 'react';
import {
  CapabilityChip,
  inferCapability,
  isChatCapability,
  isInteractiveCapability,
  listExistingCapabilities,
} from './modelCapabilityChip';
import { ModelSupportHelpButton } from './ModelSupportHelpDialog';

const STORAGE_KEY = 'cy.modelPicker.override.v2';

/** 本地 runtime 合成出的虚拟 platform,跟真平台 (deepseek/qwen 等) 平行展示。
 *  后端 SidecarRuntimeManager 注册的真 platform_name 是 `local-<cap>` (local-chat /
 *  local-embedding / ...),前端按前缀识别归到「本地模型」分组 + 排序首位。 */
export const LOCAL_PLATFORM_KEY = 'local';
export const LOCAL_PLATFORM_LABEL = '本地模型';

/** platform_name 是否属于本地 sidecar 命名空间(`local-*` 或裸 `local` 兼容老前端)。 */
function isLocalPlatform(name: string): boolean {
  return name === LOCAL_PLATFORM_KEY || name.startsWith('local-');
}

/** 模型下拉 / pill 上显示的友好名:本地 sidecar 模型只取路径最后一段 + 去掉 -GGUF 后缀,
 *  否则用裸 model id(三方 platform 的 id 一般已经够短,如 deepseek-v4-flash)。
 *
 *  例:'models/bundled/chat/Qwen3-4B-Instruct-2507-GGUF' → 'Qwen3-4B-Instruct-2507'
 *      'deepseek-v4-flash' → 'deepseek-v4-flash' */
export function displayLabel(m: { id: string; platform_name?: string | null }): string {
  if (!m.platform_name || !isLocalPlatform(m.platform_name)) return m.id;
  const last = m.id.split('/').filter(Boolean).pop() ?? m.id;
  return last.replace(/-(gguf|GGUF)$/, '');
}

interface PlatformGroup {
  /** 用作分组 key + 内部识别(同 platform_name) */
  platform: string;
  /** 82 题:展示给用户的友好名(如"深度求索 DeepSeek");无则等于 platform */
  displayName: string;
  items: RawModelItem[];
}

function groupByPlatform(list: ReadonlyArray<RawModelItem>): PlatformGroup[] {
  const map = new Map<string, { displayName: string; items: Map<string, RawModelItem> }>();
  for (const m of list) {
    const rawName = m.platform_name || '其它';
    // local / local-chat / local-embedding / ... 都归到同一个「本地模型」分组,
    // 否则会看到 1 个模型分裂成两组(老前端 'local' + 后端注册的 'local-chat' 同 id
    // 各自一组)。m.platform_name 在 items 里保留原值,路由 / 选中匹配仍按真名走。
    const key = isLocalPlatform(rawName) ? LOCAL_PLATFORM_KEY : rawName;
    const display = m.platform_display_name || m.platform_name || '其它';
    const entry = map.get(key) ?? { displayName: display, items: new Map<string, RawModelItem>() };
    if (!entry.displayName && display) entry.displayName = display;
    // 按 id 去重:同一 platform 下 model_id 重复(后端 scanner / auto-detect 双源
    // 偶尔会返同一条目两次)只保留首次出现,避免 React 渲染时 duplicate key 警告 +
    // 下拉视觉上同一模型显示两条。
    if (!entry.items.has(m.id)) {
      entry.items.set(m.id, m);
    }
    map.set(key, entry);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => {
      // 本地 sidecar(local / local-chat / ...)排首位,其它按字母序
      const aLocal = isLocalPlatform(a);
      const bLocal = isLocalPlatform(b);
      if (aLocal && !bLocal) return -1;
      if (!aLocal && bLocal) return 1;
      return a.localeCompare(b);
    })
    .map(([platform, { displayName, items }]) => ({
      platform,
      displayName:
        isLocalPlatform(platform) ? LOCAL_PLATFORM_LABEL : displayName || platform,
      items: Array.from(items.values()).sort((a, b) => a.id.localeCompare(b.id)),
    }));
}

/**
 * 用 Map<platform, boolean> 显式记录"用户的覆盖选择"——
 * true=用户主动展开,false=用户主动折叠,缺省=按默认规则(只有当前模型所在组展开)。
 *
 * 之前用 Set<collapsedPlatform> 记"被折叠的"是错的:对非默认展开的组(=非当前模型组),
 * 用户点一下"展开",代码 next.delete(platform) 把它移出 collapsed,但默认规则仍判它折叠
 * → 永远展不开。Map 三态语义化解这个问题。
 */
function readOverrideLS(): Map<string, boolean> {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return new Map();
    const obj = JSON.parse(raw) as unknown;
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const m = new Map<string, boolean>();
      for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (typeof v === 'boolean') m.set(k, v);
      }
      return m;
    }
  } catch {
    /* ignore */
  }
  return new Map();
}

function writeOverrideLS(m: Map<string, boolean>): void {
  try {
    const obj: Record<string, boolean> = {};
    for (const [k, v] of m) obj[k] = v;
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(obj));
  } catch {
    /* ignore */
  }
}

const Logo: React.FC<{ src?: string; name: string; size?: 'sm' | 'md' }> = ({
  src,
  name,
  size = 'sm',
}) => {
  const [errored, setErrored] = React.useState(false);
  const dims = size === 'md' ? 'h-4 w-4' : 'h-3.5 w-3.5';
  if (src && !errored) {
    return (
      <img
        src={src}
        alt={name}
        className={cn(dims, 'flex-shrink-0 rounded-sm object-contain')}
        loading="lazy"
        onError={() => setErrored(true)}
      />
    );
  }
  return (
    <span
      className={cn(
        dims,
        'flex flex-shrink-0 items-center justify-center rounded-sm bg-[var(--cy-brand-50)] text-[9px] font-bold text-[var(--cy-brand-600)]',
      )}
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
};

export interface ModelMenuListProps {
  models: ReadonlyArray<RawModelItem>;
  currentId: string;
  /**
   * 81 题:当前选中模型所属 platform_name(可选)。
   *
   * 跨平台同名模型(如 deepseek 与 baidu-qianfan 都有 deepseek-v4-flash)时,
   * 仅靠 currentId 匹配会让两个 entry 同时高亮"✓ 已选";传入 currentPlatform
   * 后,只有 (id, platform_name) 都匹配的那一个才被标选中。
   *
   * undefined 时退回旧行为(只匹配 id)— 兼容老调用方。
   */
  currentPlatform?: string;
  onPick(model: RawModelItem): void;
  resolveLogo(platform?: string, modelId?: string): string | undefined;
  /** 空列表占位文案;不传用默认 */
  emptyLabel?: string;
  /** 空列表时渲染在 emptyLabel 下方的可选操作(如"去模型广场"按钮) */
  emptyAction?: React.ReactNode;
  /** dropdown 容器最大高度(默认 384px) */
  maxHeight?: number;
  /**
   * 可选 capability 白名单 —— 传入后,下拉在 ``isInteractiveCapability`` 基础上
   * 再收窄,只展示落在白名单内的模型。绑定知识库时上层传 ``CHAT_CAPABILITIES``
   * (chat/vl),把 asr/tts/t2i 等非对话模型挡在选择器外,从源头避免知识问答
   * 选错模型 → 生成时才报 ModelNotConfigured / 404。不传则维持原行为。
   */
  allowCapabilities?: ReadonlySet<ModalityCapability>;
}

/**
 * 81 题:用复合键 (id, platform_name) 判断模型是否被选中。
 * 仅给 platform_name 时严格匹配;否则退回单 id 匹配(老调用方兼容)。
 */
function isSelectedModel(
  m: { id: string; platform_name?: string },
  currentId: string,
  currentPlatform?: string,
): boolean {
  if (m.id !== currentId) return false;
  if (!currentPlatform) return true; // 没传 platform → 旧行为
  return m.platform_name === currentPlatform;
}

export const ModelMenuList: React.FC<ModelMenuListProps> = ({
  models,
  currentId,
  currentPlatform,
  onPick,
  resolveLogo,
  emptyLabel = '暂无可用模型',
  emptyAction,
  maxHeight = 384,
  allowCapabilities,
}) => {
  const [keyword, setKeyword] = React.useState('');
  const [capFilter, setCapFilter] = React.useState<ModalityCapability | null>(null);
  const [override, setOverride] = React.useState<Map<string, boolean>>(() => readOverrideLS());
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  // 自动 focus 搜索框 — 提升键盘玩家效率
  React.useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 30);
    return () => clearTimeout(t);
  }, []);

  /** 先把 embedding / rerank 这种"后台索引"模型从用户面前藏起来 — 它们在
   *  对话框里点也没用,留着只是凑数 + 误导(参考 ``isInteractiveCapability``)。
   *
   *  本地 sidecar(``isLocalPlatform``)起的模型里,只有对话(chat / vl)模型
   *  能在对话框里真正用上;asr / tts / ocr / embedding / rerank / image-embedding
   *  这些本地模型即便"可交互"也无法在对话框发起调用(走生成会 ModelNotConfigured),
   *  无条件隐藏。云端的非对话模型(t2i / tts 等)不受影响 —— composer 本就支持。
   *
   *  再叠加可选 ``allowCapabilities`` 白名单(绑定知识库时只剩 chat/vl)。 */
  const interactiveModels = React.useMemo(
    () =>
      models.filter((m) => {
        const cap = inferCapability(m);
        if (!isInteractiveCapability(cap)) return false;
        // 本地非对话模型 — 无条件隐藏(与 allowCapabilities 是两条独立逻辑)
        if (isLocalPlatform(m.platform_name ?? '') && !isChatCapability(cap)) {
          return false;
        }
        if (allowCapabilities && !allowCapabilities.has(cap)) return false;
        return true;
      }),
    [models, allowCapabilities],
  );

  /** capability chip 行:只列出在当前 models 里**实际存在**的分类。 */
  const presentCaps = React.useMemo(
    () => listExistingCapabilities(interactiveModels),
    [interactiveModels],
  );

  /** 按 capability 过滤后的全集(分组前) */
  const modelsForGrouping = React.useMemo(() => {
    if (!capFilter) return interactiveModels;
    return interactiveModels.filter((m) => inferCapability(m) === capFilter);
  }, [interactiveModels, capFilter]);

  const allGroups = React.useMemo(
    () => groupByPlatform(modelsForGrouping),
    [modelsForGrouping],
  );

  /** 当前选中模型所在的 platform — 默认展开它(81 题:优先用 props 传入的精确 platform) */
  const expandedPlatform = React.useMemo(() => {
    if (currentPlatform) return currentPlatform;
    // 没传 → 退回找第一个 id 匹配的(旧行为)
    const cur = models.find((m) => m.id === currentId);
    return cur?.platform_name || '其它';
  }, [models, currentId, currentPlatform]);

  /** 关键字过滤后的可见组(也用于决定哪些组要因匹配自动展开) */
  const visibleGroups = React.useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return allGroups;
    return allGroups
      .map((g) => {
        // 82 题:搜索同时命中 pid 和 display_name(用户搜"深度求索"也能找到)
        const matchPlatform =
          g.platform.toLowerCase().includes(q) ||
          g.displayName.toLowerCase().includes(q);
        const items = matchPlatform
          ? g.items
          : g.items.filter((m) => m.id.toLowerCase().includes(q));
        return { ...g, items, _matchedByPlatform: matchPlatform };
      })
      .filter((g) => g.items.length > 0);
  }, [allGroups, keyword]);

  /**
   * 决定某组是否展开:
   *   - 有关键字时强制展开命中组
   *   - 用户显式选过 → 用 override.get(platform)(true=展开 / false=折叠)
   *   - 否则默认仅展开当前模型所在的那一组
   */
  const isExpanded = React.useCallback(
    (platform: string): boolean => {
      if (keyword.trim()) return true;
      const ov = override.get(platform);
      if (typeof ov === 'boolean') return ov;
      return platform === expandedPlatform;
    },
    [keyword, override, expandedPlatform],
  );

  const toggleGroup = (platform: string) => {
    // 拍一下当前是否展开,然后写入 override 的反值
    const nowExpanded = isExpanded(platform);
    setOverride((prev) => {
      const next = new Map(prev);
      next.set(platform, !nowExpanded);
      writeOverrideLS(next);
      return next;
    });
  };

  const totalShown = visibleGroups.reduce((s, g) => s + g.items.length, 0);

  return (
    <div className="flex flex-col" style={{ maxHeight }}>
      {/* 搜索栏 + capability 筛选 */}
      <div className="sticky top-0 z-[1] flex-shrink-0 space-y-1.5 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <input
              ref={inputRef}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索模型 / 厂商"
              className="h-7 w-full rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] pl-7 pr-2 text-xs text-[var(--cy-text-primary)] placeholder:text-[var(--cy-text-tertiary)] focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none"
              // 让 Radix 不要把方向键吞掉(否则方向键不能在输入框里左右)
              onKeyDown={(e) => e.stopPropagation()}
            />
          </div>
          {/* "?" 帮助按钮 — 弹支持的厂商目录 + API key 申请地址,不必离开
              对话窗口就能查"我能选什么、怎么搞 key" */}
          <ModelSupportHelpButton />
        </div>
        {/* capability 筛选 chip — 只显示当前 model 列表里**实际存在**的分类;
            ≥ 2 个分类才有筛选意义,只有 1 个分类不渲染避免噪音。 */}
        {presentCaps.length >= 2 && (
          <div className="flex flex-wrap items-center gap-1">
            <button
              type="button"
              onClick={() => setCapFilter(null)}
              aria-pressed={capFilter === null}
              className={cn(
                'inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-medium transition-colors',
                capFilter === null
                  ? 'border-[var(--cy-ink-700)] bg-[var(--cy-ink-700)] text-white'
                  : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
              )}
              title="显示所有类型"
            >
              全部
            </button>
            {presentCaps.map((meta) => (
              <CapabilityChip
                key={meta.cap}
                cap={meta.cap}
                active={capFilter === meta.cap}
                title={meta.longLabel}
                onClick={() => setCapFilter(capFilter === meta.cap ? null : meta.cap)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-1">
        {allGroups.length === 0 && (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <p className="text-xs text-[var(--cy-text-tertiary)]">{emptyLabel}</p>
            {emptyAction}
          </div>
        )}
        {allGroups.length > 0 && totalShown === 0 && (
          <p className="px-3 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">
            没有匹配的模型
          </p>
        )}
        {visibleGroups.map((g) => {
          const expanded = isExpanded(g.platform);
          const platformLogo = resolveLogo(g.platform);
          return (
            <div key={g.platform} className="mb-0.5">
              <button
                type="button"
                onClick={() => toggleGroup(g.platform)}
                className="group flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-1)]"
                aria-expanded={expanded}
              >
                {expanded ? (
                  <ChevronDown className="h-3 w-3 flex-shrink-0 opacity-70" />
                ) : (
                  <ChevronRight className="h-3 w-3 flex-shrink-0 opacity-70" />
                )}
                <Logo src={platformLogo} name={g.displayName} size="md" />
                <span
                  className="min-w-0 flex-1 truncate normal-case text-[var(--cy-text-secondary)]"
                  title={g.platform}    /* hover 显示 pid 用于调试 */
                >
                  {/* 82 题:显示中英文友好名(深度求索 DeepSeek)而非裸 pid (deepseek) */}
                  {g.displayName}
                </span>
                <span className="text-[10px] font-normal opacity-60">{g.items.length}</span>
              </button>
              {expanded && (
                <ul className="space-y-0.5 pl-1">
                  {g.items.map((m) => {
                    const logo = resolveLogo(m.platform_name, m.id);
                    // 81 题:用 (id, platform_name) 复合键判选中,
                    // 避免跨平台同名 model 同时高亮
                    const active = isSelectedModel(m, currentId, currentPlatform);
                    const disabled = m.available === false;
                    const itemCap = inferCapability(m);
                    return (
                      <li key={`${m.platform_name ?? ''}::${m.id}`}>
                        <button
                          type="button"
                          disabled={disabled}
                          title={disabled ? m.reason || '模型检查不可用' : undefined}
                          onClick={() => {
                            if (disabled) return;
                            onPick(m);
                          }}
                          className={cn(
                            'flex w-full items-center gap-2 rounded-md px-2 py-1.5 pl-7 text-left text-sm transition-colors',
                            disabled
                              ? 'cursor-not-allowed text-[var(--cy-text-tertiary)] opacity-55'
                              : active
                              ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                              : 'text-[var(--cy-text-primary)] hover:bg-[var(--cy-surface-1)]',
                          )}
                        >
                          {/* 模型 capability 色块 — 一眼能分辨"这是文生图 / 语音合成 / 对话",
                              色板与 ChatComposer 顶部 mode badge 完全一致 */}
                          <CapabilityChip cap={itemCap} compact />
                          <Logo src={logo} name={m.platform_name ?? m.id} />
                          <span
                            className="min-w-0 flex-1 truncate font-medium"
                            title={m.id}
                          >
                            {displayLabel(m)}
                          </span>
                          {disabled && (
                            <TriangleAlert className="h-3.5 w-3.5 flex-shrink-0 text-amber-500" />
                          )}
                          {active && <Check className="h-3.5 w-3.5 flex-shrink-0 opacity-80" />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
