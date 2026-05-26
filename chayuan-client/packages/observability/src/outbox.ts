/**
 * Langfuse 离线队列：所有 ingest 请求过这里，失败入 SQLite/IndexedDB；
 * 后台 flush 任务每 N 秒重试一次。容量上限保护。
 *
 * 通过 PAL.db 抹平桌面 / Web。
 */

import { getPlatform } from '@chayuan/platform-shared';

export interface OutboxItem {
  id?: number;
  payload: unknown;
  attempts: number;
  created_at: number;
}

const TABLE = 'lf_outbox';
const MAX_ITEMS = 10_000;
const MAX_ATTEMPTS = 6;

export interface OutboxFlushFn {
  (payload: unknown): Promise<boolean>; // true = success
}

export class Outbox {
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private flushing = false;

  constructor(private readonly send: OutboxFlushFn) {}

  async enqueue(payload: unknown): Promise<void> {
    const db = getPlatform().db;
    await db.exec(
      `INSERT INTO ${TABLE} (payload, attempts, created_at) VALUES (?, ?, ?)`,
      [JSON.stringify(payload), 0, Date.now()],
    );
    await this.cap();
  }

  /** 启动后台刷新，间隔 ms */
  start(intervalMs = 30_000): void {
    if (this.flushTimer) return;
    this.flushTimer = setInterval(() => void this.flush().catch(() => undefined), intervalMs);
    void this.flush().catch(() => undefined);
  }

  stop(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
  }

  async flush(limit = 200): Promise<{ sent: number; failed: number }> {
    if (this.flushing) return { sent: 0, failed: 0 };
    this.flushing = true;
    let sent = 0;
    let failed = 0;
    try {
      const db = getPlatform().db;
      const rows = await db.query<OutboxItem & { payload: string }>(
        `SELECT * FROM ${TABLE} ORDER BY created_at LIMIT ${Math.max(1, Math.min(limit, 1000))}`,
      );
      for (const r of rows) {
        let payload: unknown;
        try {
          payload = JSON.parse(r.payload);
        } catch {
          await db.exec(`DELETE FROM ${TABLE} WHERE id = ?`, [r.id ?? null]);
          continue;
        }
        try {
          const ok = await this.send(payload);
          if (ok) {
            await db.exec(`DELETE FROM ${TABLE} WHERE id = ?`, [r.id ?? null]);
            sent++;
          } else {
            await this.bump(r);
            failed++;
          }
        } catch {
          await this.bump(r);
          failed++;
        }
      }
    } finally {
      this.flushing = false;
    }
    return { sent, failed };
  }

  private async bump(r: OutboxItem): Promise<void> {
    const db = getPlatform().db;
    if (r.attempts + 1 >= MAX_ATTEMPTS) {
      await db.exec(`DELETE FROM ${TABLE} WHERE id = ?`, [r.id ?? null]);
      return;
    }
    // 简化：直接 +1（Dexie 适配器不支持 UPDATE，这里改为 delete+insert）
    await db.exec(`DELETE FROM ${TABLE} WHERE id = ?`, [r.id ?? null]);
    await db.exec(
      `INSERT INTO ${TABLE} (payload, attempts, created_at) VALUES (?, ?, ?)`,
      [JSON.stringify(r.payload), r.attempts + 1, r.created_at],
    );
  }

  private async cap(): Promise<void> {
    const db = getPlatform().db;
    const rows = await db.query<{ id: number; created_at: number }>(
      `SELECT * FROM ${TABLE} ORDER BY created_at`,
    );
    if (rows.length <= MAX_ITEMS) return;
    const drop = rows.length - MAX_ITEMS;
    for (let i = 0; i < drop; i++) {
      await db.exec(`DELETE FROM ${TABLE} WHERE id = ?`, [rows[i]!.id]);
    }
  }
}
