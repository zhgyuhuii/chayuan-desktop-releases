import { beforeEach, describe, expect, it } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import { configureClient } from '../client';
import { diagnose } from '../diagnose';

interface MockCall { url: string; init?: RequestInit }
let calls: MockCall[] = [];
let response: (call: MockCall) => Response = () =>
  new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });

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
  } as never);
  configureClient({ baseURL: 'http://api.local' });
});

describe('diagnose client', () => {
  it('run() 命中 GET /runtime/diagnose 并解包 envelope', async () => {
    response = () => new Response(
      JSON.stringify({
        code: 0,
        data: {
          timestamp: '2026-05-15T14:00:00',
          platform: 'linux',
          python_version: '3.11.5',
          chayuan_root: '/data',
          chayuan_server_version: '1.0.0',
          checks: [
            { name: 'sidecar.healthz', ok: true, severity: 'ok', detail: 'ok' },
          ],
          summary: { ok: 1, warn: 0, fail: 0 },
        },
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    );
    const r = await diagnose.run();
    expect(r.summary.ok).toBe(1);
    expect(r.checks[0]!.name).toBe('sidecar.healthz');
    expect(calls[0]!.url).toMatch(/\/runtime\/diagnose$/);
    expect(calls[0]!.init?.method ?? 'GET').toBe('GET');
  });

  it('run() 带 abort signal (验证 timeoutMs 传到了 client)', async () => {
    response = () => new Response(JSON.stringify({ code: 0, data: {} }), {
      status: 200, headers: { 'content-type': 'application/json' },
    });
    await diagnose.run();
    expect(calls[0]!.init?.signal).toBeDefined();
  });
});
