import { create } from 'zustand';

export type ArtifactKind = 'code' | 'markdown' | 'mermaid' | 'html' | 'json';

export interface Artifact {
  id: string;
  /** 用于聚合多次更新（如 LLM 增量 patch） */
  key: string;
  title: string;
  kind: ArtifactKind;
  /** 代码语言；仅 kind=code 用 */
  language?: string;
  content: string;
  /** 关联消息 / trace */
  messageId?: string;
  traceId?: string;
  createdAt: number;
  updatedAt: number;
}

interface ArtifactState {
  open: boolean;
  current: string | null;
  items: Record<string, Artifact>;

  upsert(a: Artifact): void;
  /** patch 只允许覆盖 content / title / language，避免误改 key */
  patch(id: string, patch: Partial<Pick<Artifact, 'content' | 'title' | 'language' | 'kind'>>): void;
  setCurrent(id: string | null): void;
  setOpen(open: boolean): void;
  clear(): void;
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  open: false,
  current: null,
  items: {},

  upsert: (a) =>
    set((s) => ({
      items: { ...s.items, [a.id]: a },
      current: s.current ?? a.id,
    })),

  patch: (id, patch) =>
    set((s) => {
      const cur = s.items[id];
      if (!cur) return s;
      return {
        items: {
          ...s.items,
          [id]: { ...cur, ...patch, updatedAt: Date.now() },
        },
      };
    }),

  setCurrent: (current) => set({ current }),
  setOpen: (open) => set({ open }),
  clear: () => set({ open: false, current: null, items: {} }),
}));
