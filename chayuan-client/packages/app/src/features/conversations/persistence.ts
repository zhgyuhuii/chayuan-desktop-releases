/**
 * 会话 / 消息本地持久化层。
 *
 * 通过 PAL.db 抹平桌面 SQLite 与 Web Dexie；
 * 业务侧只面向以下高层 API：
 *   - upsertConversation / getConversation / listConversations / deleteConversation / renameConversation
 *   - appendMessage / updateMessage / listMessages
 *
 * 注意：
 * - 流式过程中**不**频繁写库（每个 token 都写代价大）；改在 `done`/`abort` 时刷一次。
 * - 多 client 实例共享同一 DB；遇到冲突遵循 last-write-wins。
 */

import { getPlatform } from '@chayuan/platform-shared';
import type { ChatMessage } from '../chat/useChayuanChat';

export interface ConversationRecord {
  id: string;
  remote_id?: string | null;
  title: string;
  model: string;
  mode: string;
  meta?: Record<string, unknown> | null;
  created_at: number;
  updated_at: number;
}

const TBL_CONV = 'conversations';
const TBL_MSG = 'messages';

export async function upsertConversation(c: ConversationRecord): Promise<void> {
  const db = getPlatform().db;
  await db.exec(
    `INSERT OR REPLACE INTO ${TBL_CONV} (id, remote_id, title, model, mode, meta, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      c.id,
      c.remote_id ?? null,
      c.title,
      c.model,
      c.mode,
      c.meta ? JSON.stringify(c.meta) : null,
      c.created_at,
      c.updated_at,
    ],
  );
}

export async function getConversation(id: string): Promise<ConversationRecord | null> {
  const db = getPlatform().db;
  const rows = await db.query<{
    id: string;
    remote_id: string | null;
    title: string;
    model: string;
    mode: string;
    meta: string | null;
    created_at: number;
    updated_at: number;
  }>(`SELECT * FROM ${TBL_CONV} WHERE id = ?`, [id]);
  const r = rows[0];
  if (!r) return null;
  return {
    ...r,
    meta: r.meta ? (JSON.parse(r.meta) as Record<string, unknown>) : null,
  };
}

export async function listConversations(limit = 100): Promise<ConversationRecord[]> {
  const db = getPlatform().db;
  const rows = await db.query<{
    id: string;
    remote_id: string | null;
    title: string;
    model: string;
    mode: string;
    meta: string | null;
    created_at: number;
    updated_at: number;
  }>(`SELECT * FROM ${TBL_CONV} ORDER BY updated_at DESC LIMIT ${Math.max(1, Math.min(limit, 1000))}`);
  return rows.map((r) => ({
    ...r,
    meta: r.meta ? (JSON.parse(r.meta) as Record<string, unknown>) : null,
  }));
}

export async function deleteConversation(id: string): Promise<void> {
  const db = getPlatform().db;
  await db.exec(`DELETE FROM ${TBL_MSG} WHERE conv_id = ?`, [id]);
  await db.exec(`DELETE FROM ${TBL_CONV} WHERE id = ?`, [id]);
}

export async function renameConversation(id: string, title: string): Promise<void> {
  const db = getPlatform().db;
  await db.exec(`UPDATE ${TBL_CONV} SET title = ?, updated_at = ? WHERE id = ?`, [title, Date.now(), id]);
}

// ── messages ────────────────────────────────────────────────

export async function appendMessage(convId: string, msg: ChatMessage): Promise<void> {
  const db = getPlatform().db;
  await db.exec(
    `INSERT OR REPLACE INTO ${TBL_MSG} (id, conv_id, role, parts, trace_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [msg.id, convId, msg.role, JSON.stringify(msg), msg.traceId ?? null, msg.createdAt],
  );
}

export async function listMessages(convId: string): Promise<ChatMessage[]> {
  const db = getPlatform().db;
  const rows = await db.query<{ parts: string }>(
    `SELECT * FROM ${TBL_MSG} WHERE conv_id = ? ORDER BY created_at`,
    [convId],
  );
  return rows
    .map((r) => {
      try {
        return JSON.parse(r.parts) as ChatMessage;
      } catch {
        return null;
      }
    })
    .filter((m): m is ChatMessage => !!m);
}
