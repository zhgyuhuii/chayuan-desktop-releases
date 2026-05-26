/**
 * KbBoard 视图偏好 — 持久化到 localStorage,刷新 / 重开都保留。
 *
 * 仅放跨会话有意义的"看法":分类筛选 / 排序 / 视图密度。
 * 临时性的 keyword(搜索框)、chatStarted、pickerOpen 仍是组件内 useState,
 * 不污染持久化体积。
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type KbFilter = 'all' | 'mine' | 'public' | 'recommended';
export type KbSort = 'latest' | 'name' | 'docs';
export type KbView = 'card' | 'list';

export interface KbLocalGroup {
  id: string;
  name: string;
  createTime: number;
}

interface KbBoardPrefsState {
  filter: KbFilter;
  sort: KbSort;
  view: KbView;
  groups: KbLocalGroup[];
  groupByKuId: Record<string, string>;
  activeGroupId: string | null;
  setFilter(f: KbFilter): void;
  setSort(s: KbSort): void;
  setView(v: KbView): void;
  setActiveGroup(id: string | null): void;
  createGroup(name: string): string;
  assignToGroup(kuIds: string[], groupId: string | null): void;
  deleteGroup(id: string): void;
}

export const useKbBoardPrefs = create<KbBoardPrefsState>()(
  persist(
    (set) => ({
      filter: 'all',
      sort: 'latest',
      view: 'card',
      groups: [],
      groupByKuId: {},
      activeGroupId: null,
      setFilter: (filter) => set({ filter }),
      setSort: (sort) => set({ sort }),
      setView: (view) => set({ view }),
      setActiveGroup: (activeGroupId) => set({ activeGroupId }),
      createGroup: (name) => {
        const id = `g_${Date.now().toString(36)}_${Math.random().toString(16).slice(2, 8)}`;
        set((s) => ({
          groups: [...s.groups, { id, name: name.trim(), createTime: Date.now() }],
          activeGroupId: id,
        }));
        return id;
      },
      assignToGroup: (kuIds, groupId) => set((s) => {
        const next = { ...s.groupByKuId };
        for (const kuId of kuIds) {
          if (groupId) next[kuId] = groupId;
          else delete next[kuId];
        }
        return { groupByKuId: next };
      }),
      deleteGroup: (id) => set((s) => {
        const next = { ...s.groupByKuId };
        for (const [kuId, groupId] of Object.entries(next)) {
          if (groupId === id) delete next[kuId];
        }
        return {
          groups: s.groups.filter((g) => g.id !== id),
          groupByKuId: next,
          activeGroupId: s.activeGroupId === id ? null : s.activeGroupId,
        };
      }),
    }),
    {
      name: 'cy.kb.board',
      storage: createJSONStorage(() => localStorage),
      version: 2,
    },
  ),
);
