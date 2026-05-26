/**
 * 多 Tab 工作区状态(ADR-0001)。
 *
 * 数据模型:
 *   tabs:        当前 Tab 列表(顺序即 UI 顺序)
 *   activeId:    当前激活 Tab id
 *
 * 行为:
 *   - open(path, opts):
 *       * 若已存在同 path 的 Tab → 激活之
 *       * 否则在 activeId 之后插入新 Tab,并激活
 *   - activate(id):  切换激活 Tab
 *   - close(id):     关闭 Tab;若关闭的是 active,激活相邻的 Tab(右优先,无右则左)
 *   - updatePath(id, path): 同 Tab 内导航(如 ChatPage 内换会话)
 *   - saveScroll(id, y):    记录滚动位置,激活时由 KeepAliveOutlet 还原
 *
 * 持久化:
 *   - localStorage 'cy.tabs' v1
 *   - 最多保留 8 个 Tab(LRU 由插入序自然形成);冷启动 hydrate 后由 router 校验路径合法性
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useModelArenaStore } from './modelArena';

const TAB_LIMIT = 8;

export interface Tab {
  /** 稳定 id;UI 用 key,也是 close/activate 的句柄 */
  id: string;
  /** 路由路径(含 params)*/
  path: string;
  /** 显示标题;由 path → title 映射或调用方显式设置 */
  title: string;
  /** 图标名(lucide 名 / token);可选 */
  icon?: string;
  /** 是否固定 Tab(无 close 按钮) — 主页可固定 */
  pinned?: boolean;
  /** 上次记录的 scrollY */
  scrollY?: number;
  /** 创建时间 */
  createdAt: number;
}

export interface OpenOptions {
  title?: string;
  icon?: string;
  pinned?: boolean;
  /** 强制新开,即使 path 已存在 */
  forceNew?: boolean;
}

interface TabsState {
  tabs: Tab[];
  activeId: string | null;

  open(path: string, opts?: OpenOptions): string;
  activate(id: string): void;
  activateByPath(path: string): boolean;
  close(id: string): void;
  closeOthers(id: string): void;
  closeAll(): void;
  updatePath(id: string, path: string, title?: string): void;
  rename(id: string, title: string): void;
  saveScroll(id: string, y: number): void;
  reorder(fromIndex: number, toIndex: number): void;
}

let _id = 0;
function nextId(): string {
  _id += 1;
  return `tab-${Date.now().toString(36)}-${_id.toString(36)}`;
}

/**
 * 工作区永远至少有一个 Tab — 关到空时回填一个"主页"。
 *
 * 为什么不让用户看到空白:
 *   - Chrome 渲染 TabHost 时 activeId=null 会出现整页空白,体验断档
 *   - sidebar 折叠态下"新建对话"按钮也不显眼
 *
 * 设计:
 *   - fallback path = '/home'(主页);title 用 i18n key 'nav.home',
 *     TabBar 渲染时自动翻译成"主页"。用户从主页可以选择走哪条入口,
 *     比直接落在一个空的 /chat 草稿上更可控。
 *   - 不重用已有 close/closeAll 内部逻辑,而是直接构造一份新的 list,避免
 *     close → ensureNonEmpty → close 递归
 */
const DEFAULT_FALLBACK_TAB: { path: string; title: string; icon: string } = {
  path: '/home',
  title: 'nav.home',
  icon: 'home',
};

function makeFallbackTab(): Tab {
  return {
    id: nextId(),
    path: DEFAULT_FALLBACK_TAB.path,
    title: DEFAULT_FALLBACK_TAB.title,
    icon: DEFAULT_FALLBACK_TAB.icon,
    createdAt: Date.now(),
  };
}

function findByPath(tabs: Tab[], path: string): Tab | undefined {
  return tabs.find((t) => t.path === path);
}

function findById(tabs: Tab[], id: string): Tab | undefined {
  return tabs.find((t) => t.id === id);
}

function neighborOf(tabs: Tab[], id: string): string | null {
  const idx = tabs.findIndex((t) => t.id === id);
  if (idx < 0) return null;
  return tabs[idx + 1]?.id ?? tabs[idx - 1]?.id ?? null;
}

export const useTabsStore = create<TabsState>()(
  persist(
    (set, get) => ({
      tabs: [],
      activeId: null,

      open(path, opts = {}) {
        const { tabs, activeId } = get();

        // 复用已存在的 Tab(除非 forceNew)
        if (!opts.forceNew) {
          const existing = findByPath(tabs, path);
          if (existing) {
            set({ activeId: existing.id });
            return existing.id;
          }
        }

        const id = nextId();
        const tab: Tab = {
          id,
          path,
          title: opts.title ?? path,
          createdAt: Date.now(),
          ...(opts.icon !== undefined ? { icon: opts.icon } : {}),
          ...(opts.pinned !== undefined ? { pinned: opts.pinned } : {}),
        };

        // 在 active 之后插入
        const insertAt = activeId
          ? tabs.findIndex((t) => t.id === activeId) + 1
          : tabs.length;
        let next = [...tabs.slice(0, insertAt), tab, ...tabs.slice(insertAt)];

        // LRU 收紧:超过 LIMIT 时丢弃最早的非 pinned 非 active
        if (next.length > TAB_LIMIT) {
          const droppable = next.filter((t) => !t.pinned && t.id !== id);
          if (droppable.length > 0) {
            droppable.sort((a, b) => a.createdAt - b.createdAt);
            const drop = droppable[0];
            if (drop) next = next.filter((t) => t.id !== drop.id);
          }
        }

        set({ tabs: next, activeId: id });
        return id;
      },

      activate(id) {
        if (!findById(get().tabs, id)) return;
        set({ activeId: id });
      },

      activateByPath(path) {
        const t = findByPath(get().tabs, path);
        if (!t) return false;
        set({ activeId: t.id });
        return true;
      },

      close(id) {
        const { tabs, activeId } = get();
        const target = findById(tabs, id);
        if (!target || target.pinned) return;
        let remaining = tabs.filter((t) => t.id !== id);
        let nextActive =
          activeId === id ? (neighborOf(tabs, id) ?? null) : activeId;
        // 关到空 → 自动回填一个"新建对话",激活之
        if (remaining.length === 0) {
          const fallback = makeFallbackTab();
          remaining = [fallback];
          nextActive = fallback.id;
        }
        // 清掉关闭 tab 的 arena scope 桶,避免 localStorage 越长越大
        useModelArenaStore.getState().removeScope(id);
        set({ tabs: remaining, activeId: nextActive });
      },

      closeOthers(id) {
        const { tabs } = get();
        const keep = tabs.filter((t) => t.id === id || t.pinned);
        const closed = tabs.filter((t) => !keep.some((k) => k.id === t.id));
        const arena = useModelArenaStore.getState();
        closed.forEach((t) => arena.removeScope(t.id));
        if (keep.length === 0) {
          const fallback = makeFallbackTab();
          set({ tabs: [fallback], activeId: fallback.id });
          return;
        }
        set({ tabs: keep, activeId: id });
      },

      closeAll() {
        const { tabs } = get();
        const pinned = tabs.filter((t) => t.pinned);
        const closed = tabs.filter((t) => !t.pinned);
        const arena = useModelArenaStore.getState();
        closed.forEach((t) => arena.removeScope(t.id));
        // pinned 列表为空 → 仍要保留一个 fallback,工作区永不空白
        if (pinned.length === 0) {
          const fallback = makeFallbackTab();
          set({ tabs: [fallback], activeId: fallback.id });
          return;
        }
        set({ tabs: pinned, activeId: pinned[0]?.id ?? null });
      },

      updatePath(id, path, title) {
        set((s) => ({
          tabs: s.tabs.map((t) =>
            t.id === id ? { ...t, path, ...(title !== undefined ? { title } : {}) } : t,
          ),
        }));
      },

      rename(id, title) {
        set((s) => ({
          tabs: s.tabs.map((t) => (t.id === id ? { ...t, title } : t)),
        }));
      },

      saveScroll(id, y) {
        // 不触发渲染:用 setState 但只在变化时
        const cur = get().tabs.find((t) => t.id === id);
        if (!cur || cur.scrollY === y) return;
        set((s) => ({
          tabs: s.tabs.map((t) => (t.id === id ? { ...t, scrollY: y } : t)),
        }));
      },

      reorder(fromIndex, toIndex) {
        if (fromIndex === toIndex) return;
        set((s) => {
          const next = [...s.tabs];
          const [moved] = next.splice(fromIndex, 1);
          if (!moved) return {};
          next.splice(toIndex, 0, moved);
          return { tabs: next };
        });
      },
    }),
    {
      name: 'cy.tabs',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ tabs: s.tabs, activeId: s.activeId }),
      // 冷启动 hydrate 后:tabs 为空(全新用户 / 上次关到空)→ 兜底回填
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (!state.tabs || state.tabs.length === 0) {
          const fallback = makeFallbackTab();
          state.tabs = [fallback];
          state.activeId = fallback.id;
        } else if (!state.activeId || !state.tabs.some((t) => t.id === state.activeId)) {
          // activeId 失效(指向被删的 tab)→ 激活第一个
          state.activeId = state.tabs[0]?.id ?? null;
        }
      },
    },
  ),
);

/** 选择器:命名稳定,React 组件少重渲 */
export const tabSelectors = {
  list: (s: TabsState) => s.tabs,
  activeId: (s: TabsState) => s.activeId,
  activeTab: (s: TabsState) =>
    s.activeId ? s.tabs.find((t) => t.id === s.activeId) ?? null : null,
};
