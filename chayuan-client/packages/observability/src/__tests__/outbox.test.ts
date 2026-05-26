import { beforeEach, describe, expect, it, vi } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import { Outbox } from '../outbox';

/**
 * 内存 SQLite 适配器：模拟 PAL.db 的 INSERT/SELECT/DELETE 行为。
 * 仅识别 outbox 测试需要的 SQL 形态，与 platform-tauri 的 sqlite + platform-web 的 dexie 子集一致。
 */
function createMemDb() {
  let nextId = 1;
  const rows: Array<{ id: number; payload: string; attempts: number; created_at: number }> = [];

  const exec = vi.fn(async (sql: string, params: unknown[] = []) => {
    if (sql.startsWith('INSERT INTO lf_outbox')) {
      rows.push({
        id: nextId++,
        payload: String(params[0]),
        attempts: Number(params[1] ?? 0),
        created_at: Number(params[2] ?? Date.now()),
      });
      return;
    }
    if (sql.startsWith('DELETE FROM lf_outbox')) {
      const i = rows.findIndex((r) => r.id === Number(params[0]));
      if (i >= 0) rows.splice(i, 1);
      return;
    }
    throw new Error(`unhandled SQL: ${sql.slice(0, 60)}`);
  });

  const query = vi.fn(async (sql: string): Promise<unknown[]> => {
    if (sql.startsWith('SELECT * FROM lf_outbox')) {
      return [...rows].sort((a, b) => a.created_at - b.created_at);
    }
    return [];
  });

  return { exec, query, rows };
}

beforeEach(() => {
  const db = createMemDb();
  setPlatform({
    kind: 'web',
    runtime: { appName: 't', appVersion: '0', release: 't@0', defaultApiBase: '' },
    secure: { get: async () => null, set: async () => undefined, del: async () => undefined },
    db: {
      exec: db.exec,
      // 测试桩与 PAL.db.query 的范型签名差异;运行时数据形状一致,这里强转。
      query: db.query as unknown as <T = unknown>(sql: string) => Promise<T[]>,
    },
    fs: { pickFiles: async () => [], saveText: async () => undefined, readDropped: async () => [] },
    net: { fetch: globalThis.fetch, sse: globalThis.fetch },
    clipboard: { readText: async () => '', writeText: async () => undefined },
    notify: { show: async () => undefined },
    window: { onThemeChange: () => () => undefined, isDarkSystem: () => false },
    shell: { openExternal: async () => undefined, openPath: async () => undefined },
    dialog: {
      confirm: async () => true,
      prompt: async () => null,
      message: async () => undefined,
    },
  });
});

describe('Outbox', () => {
  it('成功 send 后从队列移除', async () => {
    const send = vi.fn(async () => true);
    const ob = new Outbox(send);
    await ob.enqueue({ a: 1 });
    await ob.enqueue({ a: 2 });
    const r = await ob.flush();
    expect(send).toHaveBeenCalledTimes(2);
    expect(r).toEqual({ sent: 2, failed: 0 });
    const r2 = await ob.flush();
    expect(r2).toEqual({ sent: 0, failed: 0 });
  });

  it('失败 send 后 attempts +1，超过 MAX 直接 drop', async () => {
    const send = vi.fn(async () => false);
    const ob = new Outbox(send);
    await ob.enqueue({ a: 1 });

    // 调用 6 次（MAX_ATTEMPTS）
    for (let i = 0; i < 6; i++) await ob.flush();
    const r = await ob.flush();
    expect(r).toEqual({ sent: 0, failed: 0 });
  });

  it('send 抛错被吞，不影响后续 flush', async () => {
    let n = 0;
    const send = vi.fn(async () => {
      n++;
      if (n === 1) throw new Error('boom');
      return true;
    });
    const ob = new Outbox(send);
    await ob.enqueue({ a: 1 });
    await ob.flush();
    await ob.flush();
    expect(send).toHaveBeenCalledTimes(2);
  });
});
