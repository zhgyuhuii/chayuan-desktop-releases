import { beforeEach, describe, expect, it } from 'vitest';
import { setPlatform } from '@chayuan/platform-shared';
import { configureClient } from '../client';
import { localRuntime } from '../localRuntime';

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
    secure: { get: async () => null, set: async () => undefined, del: async () => undefined },
    db: { exec: async () => undefined, query: async () => [] },
    fs: { pickFiles: async () => [], saveText: async () => undefined, readDropped: async () => [] },
    net: { fetch: fakeFetch, sse: fakeFetch },
    clipboard: { readText: async () => '', writeText: async () => undefined },
    notify: { show: async () => undefined },
    window: { onThemeChange: () => () => undefined, isDarkSystem: () => false },
    shell: { openExternal: async () => undefined },
    dialog: { confirm: async () => true, prompt: async () => null, message: async () => undefined },
  } as never);
  configureClient({ baseURL: 'http://api.local', timeoutMs: 5000 });
});

describe('localRuntime client', () => {
  it('getStatus 命中 GET /runtime/llama/status 并解包 envelope', async () => {
    response = () =>
      new Response(
        JSON.stringify({ code: 0, data: { state: 'ready', endpoint: 'http://127.0.0.1:62582', pid: 1234 } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const s = await localRuntime.getStatus();
    expect(s.state).toBe('ready');
    expect(s.endpoint).toBe('http://127.0.0.1:62582');
    expect(s.pid).toBe(1234);
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/status$/);
    expect(calls[0]!.init?.method ?? 'GET').toBe('GET');
  });

  it('start 命中 POST 并把 model_id 写进 body', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.start({ model_id: 'Qwen3-4B-Q3' });
    expect(calls[0]!.init?.method).toBe('POST');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/start$/);
    const body = calls[0]!.init?.body;
    expect(typeof body).toBe('string');
    expect(JSON.parse(body as string)).toEqual({ model_id: 'Qwen3-4B-Q3' });
  });

  it('start 默认参数发空 body', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.start();
    expect(JSON.parse(calls[0]!.init?.body as string)).toEqual({});
  });

  it('setConfig 把 patch 透传到 body', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          data: {
            preload_on_startup: true,
            host: '127.0.0.1',
            port: 62590,
            api_key: '',
            expose_lan: true,
            default_chat_model: '',
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const s = await localRuntime.setConfig({ port: 62590, expose_lan: true });
    expect(s.port).toBe(62590);
    expect(s.expose_lan).toBe(true);
    expect(JSON.parse(calls[0]!.init?.body as string)).toEqual({ port: 62590, expose_lan: true });
  });

  it('getInstallInfo 命中 GET /runtime/llama/install-info', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          data: {
            chayuan_root: '/data/cy',
            models_root: '/data/cy/models',
            bundled_models_root: '/data/cy/models/bundled',
            llama_server_exe: '/install/services/llama-server/llama-server.exe',
            build_version: 'b4404',
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const info = await localRuntime.getInstallInfo();
    expect(info.build_version).toBe('b4404');
    expect(info.llama_server_exe).toMatch(/llama-server\.exe$/);
  });

  it('stop / restart 命中正确 path', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'stopped' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.stop();
    expect(calls.at(-1)?.url).toMatch(/\/runtime\/llama\/stop$/);
    expect(calls.at(-1)?.init?.method).toBe('POST');

    await localRuntime.restart();
    expect(calls.at(-1)?.url).toMatch(/\/runtime\/llama\/restart$/);
  });

  it('getStatusFor(embedding) 命中 GET /runtime/llama/embedding/status', async () => {
    response = () =>
      new Response(
        JSON.stringify({ code: 0, data: { state: 'ready', endpoint: 'http://127.0.0.1:62583' } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const s = await localRuntime.getStatusFor('embedding');
    expect(s.state).toBe('ready');
    expect(s.endpoint).toBe('http://127.0.0.1:62583');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/embedding\/status$/);
  });

  it('startFor(rerank) 命中 POST /runtime/llama/rerank/start 并把 model_id 写 body', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.startFor('rerank', { model_id: 'bge-rerank-m3' });
    expect(calls[0]!.init?.method).toBe('POST');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/rerank\/start$/);
    expect(JSON.parse(calls[0]!.init?.body as string)).toEqual({ model_id: 'bge-rerank-m3' });
  });

  it('getRegistry() 返三个 capability 字典', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          data: {
            chat: { state: 'ready', endpoint: 'http://127.0.0.1:62582' },
            embedding: { state: 'stopped' },
            rerank: { state: 'stopped' },
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const reg = await localRuntime.getRegistry();
    expect(reg.chat.state).toBe('ready');
    expect(reg.embedding.state).toBe('stopped');
    expect(reg.rerank.state).toBe('stopped');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/registry$/);
  });

  it('getStatusFor(asr) 命中 GET /runtime/llama/asr/status', async () => {
    response = () =>
      new Response(
        JSON.stringify({ code: 0, data: { state: 'ready', endpoint: 'http://127.0.0.1:62585' } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const s = await localRuntime.getStatusFor('asr');
    expect(s.state).toBe('ready');
    expect(s.endpoint).toBe('http://127.0.0.1:62585');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/asr\/status$/);
  });

  it('startFor(asr) 命中 POST /runtime/llama/asr/start', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.startFor('asr');
    expect(calls[0]!.init?.method).toBe('POST');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/asr\/start$/);
  });

  it('getRegistry() 返四个 capability(含 asr)', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          data: {
            chat: { state: 'ready' },
            embedding: { state: 'stopped' },
            rerank: { state: 'stopped' },
            asr: { state: 'stopped' },
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const reg = await localRuntime.getRegistry();
    expect(reg.asr.state).toBe('stopped');
  });

  it('getStatusFor(image-embedding) 命中 GET /runtime/llama/image-embedding/status', async () => {
    response = () =>
      new Response(
        JSON.stringify({ code: 0, data: { state: 'ready', endpoint: 'http://127.0.0.1:62586' } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const s = await localRuntime.getStatusFor('image-embedding');
    expect(s.state).toBe('ready');
    expect(s.endpoint).toBe('http://127.0.0.1:62586');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/image-embedding\/status$/);
  });

  it('startFor(image-embedding) 命中 POST /runtime/llama/image-embedding/start', async () => {
    response = () =>
      new Response(JSON.stringify({ code: 0, data: { state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    await localRuntime.startFor('image-embedding');
    expect(calls[0]!.init?.method).toBe('POST');
    expect(calls[0]!.url).toMatch(/\/runtime\/llama\/image-embedding\/start$/);
  });

  it('getRegistry() 返五个 capability(含 image-embedding)', async () => {
    response = () =>
      new Response(
        JSON.stringify({
          code: 0,
          data: {
            chat: { state: 'ready' },
            embedding: { state: 'stopped' },
            rerank: { state: 'stopped' },
            asr: { state: 'stopped' },
            'image-embedding': { state: 'stopped' },
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    const reg = await localRuntime.getRegistry();
    expect(reg['image-embedding'].state).toBe('stopped');
  });
});
