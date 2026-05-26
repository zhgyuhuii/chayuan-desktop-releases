/**
 * 96 题:模型对抗(model arena)— 多永道对话状态机。
 *
 * 设计要点(用户决策):
 *   1. lanes 无上限,可拖出 / 排序 / 调宽 / 折叠
 *   2. 加新道 model 跟随**最后一次操作**(lastTouchedModelId)
 *   3. 任意删,删完 main 也行;lanes 列空就由 ChatPage 自动重建一道
 *   4. unifiedSend 默认 OFF
 *   5. 各道 conversationId 独立(各自新建)
 *
 * v2(per-tab 隔离):lanes / unifiedSend 按 scope(= tab.id)分桶,
 * 不同 tab 各自独立。lastTouchedModel / lastTouchedSeed 仍是全局 UX 提示。
 *
 * 单永道(默认状态)= UI 完全等价老 ChatPage,零视觉回归。
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { ComposerSeed } from './composer';

export interface Lane {
  /** 永道 id;首道默认 'main' */
  id: string;
  /** 本道的会话 id;null 表示草稿(首发时 lazy create) */
  conversationId: string | null;
  /** 本道选用的 model;``platform::model_id`` 形式或裸 id */
  modelId: string | null;
  /** 折叠状态:true 时只显示一根竖条 */
  collapsed: boolean;
  /** 拖出独立窗口(主窗口隐藏占位) */
  detached: boolean;
  /** 拖宽度;undefined 表示用默认 1fr */
  widthPx?: number;
  /** 本道第一条用户提问 — onConversationCreated 触发时 ChatPage 写入,
   *  折叠条上当作标题展示;null 表示尚未发过任何消息(空草稿) */
  firstQuestion?: string | null;
}

export interface ArenaScopeState {
  lanes: Lane[];
  unifiedSend: boolean;
}

interface ArenaState {
  /** 按 scope(tab.id)分桶的状态;空 scope 由 selectors 兜默认值 */
  byTabId: Record<string, ArenaScopeState>;

  /** 上一次用户主动操作的 model(最后选模型 / 最后发消息的那道的 model)
   *  加新道时 default 跟它;空时用 main 道的 modelId。**全局**(跨 tab) */
  lastTouchedModelId: string | null;
  /** 上一次用户操作的 composer 完整快照 — 用作新 lane seed。**全局** */
  lastTouchedSeed: ComposerSeed | null;
  setLastTouchedSeed(seed: ComposerSeed): void;

  // ---- scope lifecycle ----
  /** 关 tab 时调,清掉对应桶,避免 localStorage 越长越大 */
  removeScope(scope: string): void;

  // ---- mutations(全部接 scope 作首参) ----
  addLane(scope: string, opts?: { modelId?: string }): string;
  removeLane(scope: string, laneId: string): void;
  setModel(scope: string, laneId: string, modelId: string | null): void;
  setConversationId(scope: string, laneId: string, conversationId: string | null): void;
  setFirstQuestion(scope: string, laneId: string, q: string | null): void;
  toggleCollapsed(scope: string, laneId: string): void;
  setDetached(scope: string, laneId: string, detached: boolean): void;
  setWidth(scope: string, laneId: string, widthPx: number | undefined): void;
  reorder(scope: string, fromIndex: number, toIndex: number): void;
  setUnifiedSend(scope: string, on: boolean): void;
  /** 重置 scope 到单道(老 ChatPage 形态);scope 不存在则创建 */
  resetToSingleLane(scope: string): void;
  /** 用于 ArenaDispatcher 统一发送 — 拿当前 scope 可发送的 lane */
  getActiveLanes(scope: string): Lane[];
}

const DEFAULT_LANE_ID = 'main';

function createDefaultLane(modelId: string | null = null): Lane {
  return {
    id: DEFAULT_LANE_ID,
    conversationId: null,
    modelId,
    collapsed: false,
    detached: false,
  };
}

function genLaneId(): string {
  return `lane-${Math.random().toString(36).slice(2, 9)}`;
}

/** 稳定空数组:scope 未初始化时 selectors 返回它,zustand 浅比无变化 */
const EMPTY_LANES: Lane[] = [];

function getScope(s: ArenaState, scope: string): ArenaScopeState | undefined {
  return s.byTabId[scope];
}

function patchScope(
  s: ArenaState,
  scope: string,
  patch: (prev: ArenaScopeState) => ArenaScopeState,
): Partial<ArenaState> {
  const prev: ArenaScopeState = s.byTabId[scope] ?? { lanes: [createDefaultLane(s.lastTouchedModelId)], unifiedSend: false };
  const next = patch(prev);
  return { byTabId: { ...s.byTabId, [scope]: next } };
}

export const useModelArenaStore = create<ArenaState>()(
  persist(
    (set, get) => ({
      byTabId: {},
      lastTouchedModelId: null,
      lastTouchedSeed: null,

      setLastTouchedSeed: (seed) => set({
        lastTouchedSeed: seed,
        lastTouchedModelId: seed.modelId ?? null,
      }),

      removeScope: (scope) => set((s) => {
        if (!(scope in s.byTabId)) return s;
        const { [scope]: _drop, ...rest } = s.byTabId;
        return { byTabId: rest };
      }),

      addLane: (scope, opts) => {
        const id = genLaneId();
        const cur = getScope(get(), scope);
        const fallbackModel = get().lastTouchedModelId
          || cur?.lanes[0]?.modelId
          || null;
        const newLane: Lane = {
          id,
          conversationId: null,
          modelId: opts?.modelId ?? fallbackModel,
          collapsed: false,
          detached: false,
        };
        set((s) => ({
          ...patchScope(s, scope, (prev) => ({
            ...prev,
            lanes: [...prev.lanes, newLane],
          })),
          lastTouchedModelId: newLane.modelId ?? s.lastTouchedModelId,
        }));
        return id;
      },

      removeLane: (scope, laneId) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.filter((l) => l.id !== laneId),
        })));
        // ChatPage 监听;lanes 空时它会自动重建 main 道(避免空白页)
      },

      setModel: (scope, laneId, modelId) => {
        set((s) => ({
          ...patchScope(s, scope, (prev) => ({
            ...prev,
            lanes: prev.lanes.map((l) =>
              l.id === laneId ? { ...l, modelId } : l,
            ),
          })),
          lastTouchedModelId: modelId ?? s.lastTouchedModelId,
        }));
      },

      setConversationId: (scope, laneId, conversationId) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.map((l) =>
            // 切到新会话时清掉旧的 firstQuestion,避免侧边栏切换时残留上一会话标题
            l.id === laneId ? { ...l, conversationId, firstQuestion: null } : l,
          ),
        })));
      },

      setFirstQuestion: (scope, laneId, q) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.map((l) =>
            l.id === laneId ? { ...l, firstQuestion: q } : l,
          ),
        })));
      },

      toggleCollapsed: (scope, laneId) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.map((l) =>
            l.id === laneId ? { ...l, collapsed: !l.collapsed } : l,
          ),
        })));
      },

      setDetached: (scope, laneId, detached) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.map((l) =>
            l.id === laneId ? { ...l, detached } : l,
          ),
        })));
      },

      setWidth: (scope, laneId, widthPx) => {
        set((s) => patchScope(s, scope, (prev) => ({
          ...prev,
          lanes: prev.lanes.map((l) =>
            l.id === laneId ? { ...l, widthPx } : l,
          ),
        })));
      },

      reorder: (scope, fromIndex, toIndex) => {
        set((s) => {
          const cur = getScope(s, scope);
          if (!cur) return s;
          if (fromIndex === toIndex) return s;
          if (fromIndex < 0 || fromIndex >= cur.lanes.length) return s;
          if (toIndex < 0 || toIndex >= cur.lanes.length) return s;
          const next = cur.lanes.slice();
          const [moved] = next.splice(fromIndex, 1);
          if (!moved) return s;
          next.splice(toIndex, 0, moved);
          return patchScope(s, scope, (prev) => ({ ...prev, lanes: next }));
        });
      },

      setUnifiedSend: (scope, on) => set((s) => patchScope(s, scope, (prev) => ({
        ...prev,
        unifiedSend: on,
      }))),

      resetToSingleLane: (scope) => set((s) => ({
        byTabId: {
          ...s.byTabId,
          [scope]: {
            lanes: [createDefaultLane(s.lastTouchedModelId)],
            unifiedSend: false,
          },
        },
      })),

      getActiveLanes: (scope) => {
        // 折叠的道仍参与统一发送(用户折叠是为了视觉省地方,不是停用)
        // 拖出去的道:由 BroadcastChannel 自己处理,主窗口 dispatcher 不发
        const cur = getScope(get(), scope);
        return cur ? cur.lanes.filter((l) => !l.detached) : [];
      },
    }),
    {
      name: 'chayuan.modelArena',
      version: 2,
      storage: createJSONStorage(() => localStorage),
      // 只持久化布局相关,不持久化 conversationId(各次进 chat 重建)
      partialize: (s) => ({
        byTabId: Object.fromEntries(
          Object.entries(s.byTabId).map(([k, v]) => [k, {
            // firstQuestion 跟 conversationId 绑,conversationId 清掉就一起清,避免页面刷新后残留上次会话的标题
            lanes: v.lanes.map((l) => ({ ...l, conversationId: null, detached: false, firstQuestion: null })),
            unifiedSend: v.unifiedSend,
          }]),
        ),
        lastTouchedModelId: s.lastTouchedModelId,
      }),
      migrate: (persisted, version) => {
        // v1:全局 lanes / unifiedSend → v2 把它们丢弃,只保留 lastTouchedModelId
        // 旧用户首次刷新损失多泳道布局(一次性);新会话由 ChatPage 自动重建单道
        if (version < 2 && persisted && typeof persisted === 'object') {
          const p = persisted as Partial<{ lastTouchedModelId: string | null }>;
          return {
            byTabId: {},
            lastTouchedModelId: p.lastTouchedModelId ?? null,
            lastTouchedSeed: null,
          };
        }
        return persisted;
      },
    },
  ),
);

/** 便捷 selector — 返回稳定空 ref,避免无 scope 时的 re-render */
export const selectScopedLanes = (scope: string) =>
  (s: ArenaState): Lane[] => s.byTabId[scope]?.lanes ?? EMPTY_LANES;

export const selectScopedIsMultiLane = (scope: string) =>
  (s: ArenaState): boolean => (s.byTabId[scope]?.lanes?.length ?? 0) > 1;

export const selectScopedUnifiedSend = (scope: string) =>
  (s: ArenaState): boolean => s.byTabId[scope]?.unifiedSend ?? false;
