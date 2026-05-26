/**
 * 会话列表服务端态 + 本地缓存的合并视图。
 *
 * 来源：
 *  - 后端 /chat/conversations（登录态）：作为权威来源；空时不阻塞；
 *  - 本地 SQLite/Dexie：游客态/离线时主显示；登录后用 remote_id merge。
 *
 * 暴露：useConversationList、useDeleteConversation、useRenameConversation。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { conversations, type ServerConversation } from '@chayuan/api';
import {
  deleteConversation as deleteLocal,
  listConversations as listLocal,
  renameConversation as renameLocal,
  type ConversationRecord,
} from './persistence';

export const CONVERSATION_KEYS = {
  list: ['conversations', 'list'] as const,
  remote: ['conversations', 'remote'] as const,
};

export interface ConversationView extends ConversationRecord {
  /** 是否已经同步到后端（remote_id 非空） */
  synced: boolean;
}

async function loadAll(): Promise<ConversationView[]> {
  const local = await listLocal(200);
  let remote: ServerConversation[] = [];
  try {
    remote = await conversations.list(200);
  } catch {
    // 游客 / 接口不可达 — 仅本地
    remote = [];
  }
  // 以 remote_id 为键合并；远端独有的转 ConversationView
  const byRemote = new Map<string, ConversationView>();
  for (const l of local) {
    if (l.remote_id) byRemote.set(l.remote_id, { ...l, synced: true });
  }
  for (const r of remote) {
    const existing = byRemote.get(r.id);
    if (existing) continue;
    byRemote.set(r.id, {
      id: r.id,
      remote_id: r.id,
      title: r.name || '会话',
      model: '',
      mode: r.chat_type ?? 'agent',
      meta: null,
      created_at: r.create_time ? Date.parse(r.create_time) : Date.now(),
      updated_at: r.create_time ? Date.parse(r.create_time) : Date.now(),
      synced: true,
    });
  }
  // 仅本地（未同步）的拼回；按 updated_at 倒序
  const localOnly = local.filter((l) => !l.remote_id).map((l) => ({ ...l, synced: false }));
  return [...byRemote.values(), ...localOnly].sort((a, b) => b.updated_at - a.updated_at);
}

export function useConversationList() {
  return useQuery({
    queryKey: CONVERSATION_KEYS.list,
    queryFn: loadAll,
    staleTime: 30_000,
    retry: 0,
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (item: ConversationView) => {
      await deleteLocal(item.id);
      if (item.remote_id) {
        try {
          await conversations.delete(item.remote_id);
        } catch {
          /* tolerate */
        }
      }
    },
    onMutate: async (item) => {
      await qc.cancelQueries({ queryKey: CONVERSATION_KEYS.list });
      const prev = qc.getQueryData<ConversationView[]>(CONVERSATION_KEYS.list);
      qc.setQueryData<ConversationView[]>(CONVERSATION_KEYS.list, (old) =>
        old?.filter((c) => c.id !== item.id) ?? [],
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(CONVERSATION_KEYS.list, ctx.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATION_KEYS.list });
    },
  });
}

/**
 * 一键清空所有会话:并发 delete,任一失败不阻塞其他,一次性 invalidate 列表。
 *
 * 后端无 bulk 端点,只能 1 个 conversation 1 次请求。100 条上限并发跑差不多 1-2s,
 * 用 Promise.allSettled 兜底单条失败,UI 层先用 setQueryData 清空给即时反馈。
 */
export function useClearAllConversations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (items: ConversationView[]) => {
      // 本地批量删
      await Promise.allSettled(items.map((it) => deleteLocal(it.id)));
      // 后端逐个删(只对有 remote_id 的)
      const remote = items.filter((it) => it.remote_id);
      await Promise.allSettled(
        remote.map((it) => conversations.delete(it.remote_id as string)),
      );
    },
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: CONVERSATION_KEYS.list });
      const prev = qc.getQueryData<ConversationView[]>(CONVERSATION_KEYS.list);
      // 即时清空 UI
      qc.setQueryData<ConversationView[]>(CONVERSATION_KEYS.list, []);
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(CONVERSATION_KEYS.list, ctx.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATION_KEYS.list });
    },
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ item, name }: { item: ConversationView; name: string }) => {
      await renameLocal(item.id, name);
      if (item.remote_id) {
        try {
          await conversations.rename(item.remote_id, name);
        } catch {
          /* tolerate */
        }
      }
    },
    onMutate: async ({ item, name }) => {
      await qc.cancelQueries({ queryKey: CONVERSATION_KEYS.list });
      const prev = qc.getQueryData<ConversationView[]>(CONVERSATION_KEYS.list);
      qc.setQueryData<ConversationView[]>(CONVERSATION_KEYS.list, (old) =>
        old?.map((c) => (c.id === item.id ? { ...c, title: name, updated_at: Date.now() } : c)) ?? [],
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(CONVERSATION_KEYS.list, ctx.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATION_KEYS.list });
    },
  });
}
