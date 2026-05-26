/**
 * ``serverModelsBootstrap`` + ``serverModelsProcessArgs`` API 客户端测试。
 *
 * 用 fakeFetch 桩验证:
 *   - URL / query params 拼装正确
 *   - 返回字段 schema 与后端契约一致
 *   - 空响应 / 错误响应不抛
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import {
  configureClient,
  serverModelsBootstrap,
  serverModelsProcessArgs,
} from '../index';

interface MockCall {
  url: string;
  init?: RequestInit;
}

let calls: MockCall[] = [];
let response: (call: MockCall) => Response = () =>
  new Response('{}', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

const fakeFetch: typeof globalThis.fetch = async (input, init) => {
  const url =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : (input as Request).url;
  const call = { url, init: init ?? undefined };
  calls.push(call);
  return response(call);
};

beforeEach(() => {
  calls = [];
  response = () =>
    new Response('{}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  setPlatform({
    kind: 'web',
    runtime: {
      appName: 't',
      appVersion: '0',
      release: 't@0',
      defaultApiBase: 'http://api.local',
    },
    secure: {
      get: async () => null,
      set: async () => undefined,
      del: async () => undefined,
    },
    db: { exec: async () => undefined, query: async () => [] },
    fs: {
      pickFiles: async () => [],
      saveText: async () => undefined,
      readDropped: async () => [],
    },
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
  configureClient({ baseURL: 'http://api.local' });
});

// ─────────────────────── bootstrap ───────────────────────

describe('serverModelsBootstrap.get', () => {
  it('hits /admin/models/bootstrap without query when no opts given', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          ready: true,
          missing: [],
          statuses: [],
          install_hints: [],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const out = await serverModelsBootstrap.get();
    expect(out.ready).toBe(true);
    expect(out.missing).toEqual([]);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toMatch(/\/admin\/models\/bootstrap$/);
  });

  it('forwards do_scan=false as query', async () => {
    await serverModelsBootstrap.get({ doScan: false });
    expect(calls[0]!.url).toContain('do_scan=false');
  });

  it('joins required capability list with commas', async () => {
    await serverModelsBootstrap.get({
      required: ['chat', 'text-embedding'],
    });
    expect(calls[0]!.url).toMatch(/required=chat%2Ctext-embedding/);
  });

  it('returns shape with statuses + install_hints when missing', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          ready: false,
          missing: ['text-embedding', 'rerank'],
          statuses: [
            {
              capability: 'chat',
              satisfied: true,
              candidate_count: 1,
              candidates: [
                {
                  model_id: 'qwen3-4b',
                  path: '/m/q.gguf',
                  format: 'gguf',
                  family: 'qwen3',
                  size_bytes: 2500_000_000,
                },
              ],
            },
          ],
          install_hints: [
            {
              release: 'lite',
              description: '单机轻量版',
              approx_size_mb: 3500,
              covered_capabilities: ['rerank', 'text-embedding'],
              mirrors: [
                {
                  name: 'hf-mirror',
                  endpoint: 'https://hf-mirror.com',
                  note: '中国大陆推荐',
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const out = await serverModelsBootstrap.get();
    expect(out.ready).toBe(false);
    expect(out.missing.length).toBe(2);
    const first = out.statuses[0];
    const firstHint = out.install_hints[0];
    expect(first).toBeDefined();
    expect(firstHint).toBeDefined();
    expect(first!.capability).toBe('chat');
    expect(first!.candidates[0]?.model_id).toBe('qwen3-4b');
    expect(firstHint!.release).toBe('lite');
    expect(firstHint!.mirrors[0]?.name).toBe('hf-mirror');
  });
});

// ─────────────────────── process_args ───────────────────────

describe('serverModelsProcessArgs.get', () => {
  it('hits /admin/models/process_args and returns parsed snapshot', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          msg: 'ok',
          data: {
            llamacpp: {
              process: 'llamacpp',
              args: ['--model', '/m/q.gguf', '--ctx-size', '4096'],
              env: {},
              resolved_models: { chat: 'qwen3-4b' },
              missing: [],
              reason: 'resolved by default',
              ok: true,
            },
            infinity: {
              process: 'infinity',
              args: [],
              env: {},
              resolved_models: {},
              missing: ['embedding', 'rerank'],
              reason: '',
              ok: false,
            },
            ollama: {
              process: 'ollama',
              args: [],
              env: { OLLAMA_MODELS: '/m/chat/_ollama' },
              resolved_models: {},
              missing: [],
              reason: 'derived from models_dir',
              ok: true,
            },
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const out = await serverModelsProcessArgs.get();
    expect(Object.keys(out).sort()).toEqual(['infinity', 'llamacpp', 'ollama']);
    const llamacpp = out.llamacpp;
    const infinity = out.infinity;
    const ollama = out.ollama;
    expect(llamacpp).toBeDefined();
    expect(infinity).toBeDefined();
    expect(ollama).toBeDefined();
    expect(llamacpp!.ok).toBe(true);
    expect(llamacpp!.args).toContain('--model');
    expect(infinity!.ok).toBe(false);
    expect(infinity!.missing).toContain('embedding');
    expect(ollama!.env.OLLAMA_MODELS).toBe('/m/chat/_ollama');
  });

  it('returns empty object when response body missing data field', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, msg: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    const out = await serverModelsProcessArgs.get();
    expect(out).toEqual({});
  });
});
