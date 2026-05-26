import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import {
  clearTokens,
  getAccessToken,
  refreshAccessToken,
  setTokens,
} from '../auth-store';

const memSecure = (() => {
  const m = new Map<string, string>();
  return {
    get: vi.fn(async (k: string) => m.get(k) ?? null),
    set: vi.fn(async (k: string, v: string) => void m.set(k, v)),
    del: vi.fn(async (k: string) => void m.delete(k)),
    _map: m,
  };
})();

beforeEach(() => {
  memSecure._map.clear();
  vi.clearAllMocks();
  setPlatform({
    kind: 'web',
    runtime: { appName: 'test', appVersion: '0.0.0', release: 'test@0', defaultApiBase: '' },
    secure: memSecure,
    db: { exec: async () => undefined, query: async () => [] },
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

afterEach(async () => {
  await clearTokens();
});

describe('auth-store', () => {
  it('设置 / 读取 / 清除 token', async () => {
    await setTokens('a', 'r');
    expect(await getAccessToken()).toBe('a');
    await clearTokens();
    expect(await getAccessToken()).toBeNull();
  });

  it('refreshAccessToken 单飞：N 个并发请求只触发一次 doRefresh', async () => {
    await setTokens('expired', 'refresh-1');
    const doRefresh = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 20));
      return { access_token: 'fresh', refresh_token: 'refresh-2' };
    });

    const results = await Promise.all([
      refreshAccessToken(doRefresh),
      refreshAccessToken(doRefresh),
      refreshAccessToken(doRefresh),
    ]);
    expect(results).toEqual(['fresh', 'fresh', 'fresh']);
    expect(doRefresh).toHaveBeenCalledTimes(1);
    expect(await getAccessToken()).toBe('fresh');
  });

  it('refresh 失败返回 null，不抛', async () => {
    await setTokens('expired', 'r');
    const doRefresh = vi.fn(async () => null);
    const r = await refreshAccessToken(doRefresh);
    expect(r).toBeNull();
  });

  it('无 refresh token 时直接返回 null', async () => {
    await clearTokens();
    const r = await refreshAccessToken(async () => ({ access_token: 'x' }));
    expect(r).toBeNull();
  });
});
