import { beforeEach, describe, expect, it, vi } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import { configureClient, http, request } from '../client';
import { BizError } from '../errors';
import { setTokens, clearTokens } from '../auth-store';

interface MockCall {
  url: string;
  init?: RequestInit;
}

let calls: MockCall[] = [];
let response: (call: MockCall) => Response = () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });

const fakeFetch: typeof globalThis.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : (input as Request).url;
  const call = { url, init: init ?? undefined };
  calls.push(call);
  return response(call);
};

beforeEach(() => {
  calls = [];
  response = () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
  setPlatform({
    kind: 'web',
    runtime: { appName: 't', appVersion: '0', release: 't@0', defaultApiBase: 'http://api.local' },
    secure: { get: async () => null, set: async () => undefined, del: async () => undefined },
    db: { exec: async () => undefined, query: async () => [] },
    fs: { pickFiles: async () => [], saveText: async () => undefined, readDropped: async () => [] },
    net: { fetch: fakeFetch, sse: fakeFetch },
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
  configureClient({ baseURL: 'http://api.local', timeoutMs: 5000 });
});

describe('client', () => {
  it('注入 X-Trace-Id 与 traceparent 头', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { ok: true } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await http.get('/foo');
    const headers = new Headers(calls[0]!.init!.headers);
    expect(headers.get('X-Trace-Id')).toMatch(/[0-9a-f-]{36}/);
    expect(headers.get('traceparent')).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
  });

  it('拆 envelope：{code:0,data:T} → 直接返回 data', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { hello: 'world' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    const out = await http.get<{ hello: string }>('/foo');
    expect(out).toEqual({ hello: 'world' });
  });

  it('列表 envelope：{code, items, total}（无 data 字段） → 保留整 envelope，r.items 可读', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 200, msg: 'ok', items: [{ id: 'a' }, { id: 'b' }], total: 2 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    interface ItemsResp<T> { code: number; msg: string; items: T[]; total: number }
    const out = await http.get<ItemsResp<{ id: string }>>('/list');
    expect(out.items).toHaveLength(2);
    expect(out.total).toBe(2);
  });

  it('混合 envelope：{code, data:T, extra} 也保留整体（兼容旧式后端）', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 200, msg: 'ok', data: { name: 'foo' }, version: '1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    const out = await http.get<{ data: { name: string }; version: string }>('/x');
    // 因 envelope 含 'version' 这种额外字段，保留 envelope，r.data / r.version 都可读
    expect(out.data).toEqual({ name: 'foo' });
    expect(out.version).toBe('1');
  });

  it('envelope code != 0 抛 BizError(biz)', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 500, msg: 'boom' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await expect(http.get('/foo')).rejects.toMatchObject({
      kind: 'biz',
      code: 500,
      message: 'boom',
    });
  });

  it('raw=true 不拆壳', async () => {
    response = () =>
      new Response(JSON.stringify({ id: 1, name: 'x' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    const out = await request<{ id: number; name: string }>('/users/1', { raw: true });
    expect(out.data).toEqual({ id: 1, name: 'x' });
  });

  it('401 → /auth/refresh → 新 token 重发', async () => {
    await setTokens('expired', 'r');
    let n = 0;
    response = (call) => {
      n++;
      if (call.url.endsWith('/auth/refresh')) {
        return new Response(JSON.stringify({ access_token: 'fresh', refresh_token: 'r2' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      const auth = new Headers(call.init?.headers).get('Authorization');
      if (auth === 'Bearer fresh') {
        return new Response(JSON.stringify({ code: 0, data: 'ok' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      return new Response('Unauthorized', { status: 401 });
    };
    const out = await http.get<string>('/secure');
    expect(out).toBe('ok');
    expect(n).toBeGreaterThanOrEqual(2);
    await clearTokens();
  });

  it('网络错误转 BizError(http)', async () => {
    response = () => {
      throw new TypeError('network');
    };
    try {
      await http.get('/foo');
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(BizError);
      expect((e as BizError).kind).toBe('http');
    }
  });
});
