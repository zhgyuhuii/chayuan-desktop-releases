/**
 * 本地 trace 列表（从已持久化的 messages 聚合）。
 *
 * 设计：
 * - 后端 Langfuse 的 read API 需要 secret key，不适合放客户端；
 * - 客户端所有 trace 都来自我们发出的请求 → 我们已经在 messages.parts(JSON) 里存了 traceId；
 * - 这里直接 SELECT 出最近 N 条带 trace_id 的消息，按 trace_id 去重，再 join
 *   conversation 名称，作为 admin 看板的"最近 trace"基础源。
 *
 * 同时合并 outbox 里仍未 flush 的事件作为「待上传」标记。
 */

import { useQuery } from '@tanstack/react-query';
import { getPlatform } from '@chayuan/platform-shared';

export interface LocalTraceRow {
  traceId: string;
  conversationId: string;
  conversationTitle?: string;
  role: string;
  preview: string;
  createdAt: number;
}

export interface OutboxStat {
  pending: number;
}

const QK = ['admin', 'local-traces'] as const;
const QK_OUTBOX = ['admin', 'outbox-stat'] as const;

export function useLocalTraces() {
  return useQuery({
    queryKey: QK,
    queryFn: async (): Promise<LocalTraceRow[]> => {
      const db = getPlatform().db;
      const rows = await db.query<{
        id: string;
        conv_id: string;
        role: string;
        parts: string;
        trace_id: string | null;
        created_at: number;
      }>(`SELECT * FROM messages ORDER BY created_at DESC LIMIT 200`);
      const convs = await db.query<{ id: string; title: string }>(`SELECT * FROM conversations`);
      const titleByConv = new Map(convs.map((c) => [c.id, c.title]));

      const seen = new Set<string>();
      const out: LocalTraceRow[] = [];
      for (const r of rows) {
        if (!r.trace_id || seen.has(r.trace_id)) continue;
        seen.add(r.trace_id);
        let preview = '';
        try {
          const p = JSON.parse(r.parts) as { content?: string };
          preview = (p.content ?? '').slice(0, 80);
        } catch {
          /* keep empty */
        }
        out.push({
          traceId: r.trace_id,
          conversationId: r.conv_id,
          conversationTitle: titleByConv.get(r.conv_id),
          role: r.role,
          preview,
          createdAt: r.created_at,
        });
      }
      return out;
    },
    staleTime: 10_000,
    retry: 0,
  });
}

export function useOutboxStat() {
  return useQuery({
    queryKey: QK_OUTBOX,
    queryFn: async (): Promise<OutboxStat> => {
      const db = getPlatform().db;
      const rows = await db.query<{ id: number }>(`SELECT * FROM lf_outbox`);
      return { pending: rows.length };
    },
    staleTime: 5_000,
    retry: 0,
    refetchInterval: 5_000,
  });
}
