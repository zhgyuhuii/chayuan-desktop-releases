/**
 * Storybook 故事：``AiPlatformPanel`` 在加载 / 空 / 异常 / 数据齐全等几种态下的样子。
 *
 * 由于面板内部直接走 ``aiPlatform`` / ``runtimeServices`` 等 API，我们用
 * ``parameters.fetchMock`` 在 Story 装载前拦截 ``window.fetch`` 返回 stub。
 * 这样 Storybook UI 演示 / chromatic / Playwright 都能跑同一份组件树。
 */
import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { AiPlatformPanel } from '../features/aiPlatform/AiPlatformPanel';

interface FetchStub {
  url: string | RegExp;
  body: unknown;
  status?: number;
}

const installFetchStubs = (stubs: FetchStub[]) => {
  const originalFetch = window.fetch;
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === 'string' ? input : input.toString();
    for (const s of stubs) {
      const matched = typeof s.url === 'string' ? u.includes(s.url) : s.url.test(u);
      if (matched) {
        return new Response(JSON.stringify(s.body), {
          status: s.status ?? 200,
          headers: { 'content-type': 'application/json' },
        });
      }
    }
    return originalFetch(input, init);
  }) as typeof window.fetch;
  return () => {
    window.fetch = originalFetch;
  };
};

const StubProvider: React.FC<{ stubs: FetchStub[]; children: React.ReactNode }> = ({
  stubs,
  children,
}) => {
  React.useEffect(() => installFetchStubs(stubs), [stubs]);
  return <div className="bg-zinc-50 p-6 dark:bg-zinc-900">{children}</div>;
};

const FULL_MODELS = [
  { id: 'qwen2.5:4b', capability: 'chat', enabled: true, runtime: 'ollama' },
  { id: 'bge-m3:latest', capability: 'text-embedding', enabled: true, runtime: 'infinity' },
  { id: 'jina-clip-v1', capability: 'image-embedding', enabled: false, runtime: 'infinity' },
  { id: 'bge-reranker-v2', capability: 'rerank', enabled: true, runtime: 'infinity' },
  { id: 'sd-1.5', capability: 'text-to-image', enabled: true, runtime: 'comfyui' },
  { id: 'svd-xt', capability: 'text-to-video', enabled: false, runtime: 'comfyui' },
  { id: 'cosyvoice-base', capability: 'text-to-speech', enabled: true, runtime: 'cosyvoice' },
  { id: 'paraformer-zh', capability: 'asr', enabled: true, runtime: 'funasr' },
  { id: 'rapidocr-v1', capability: 'ocr', enabled: true, runtime: 'rapidocr' },
];

const FULL_SERVICES = {
  data: [
    { name: 'postgres', host: '127.0.0.1', port: 35432, user: 'chayuan', password: '****', kind: 'postgres', managed: true },
    { name: 'redis', host: '127.0.0.1', port: 36379, password: '****', kind: 'redis', managed: true },
    { name: 'minio', host: '127.0.0.1', port: 39000, user: 'chayuan', password: '****', kind: 'minio', managed: true },
    { name: 'milvus', host: '127.0.0.1', port: 39530, kind: 'milvus', managed: true },
  ],
};

const HEALTHY_DOCTOR = {
  generated_at: new Date().toISOString(),
  host: { os: 'Linux', python: '3.12.3' },
  preflight: { ok: true, checks: [] },
  runtime: { services: FULL_SERVICES.data },
  adapters: [
    { name: 'ollama', kind: 'http', probe_url: 'http://127.0.0.1:11434/api/tags', ok: true, latency_ms: 18 },
    { name: 'infinity', kind: 'http', probe_url: 'http://127.0.0.1:7997/health', ok: true, latency_ms: 11 },
    { name: 'comfyui', kind: 'http', probe_url: 'http://127.0.0.1:18188/system_stats', ok: false, error: 'connection refused' },
    { name: 'piper', kind: 'subprocess', ok: true },
  ],
};

const meta: Meta<typeof AiPlatformPanel> = {
  title: 'AI Platform / SettingsPanel',
  component: AiPlatformPanel,
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof AiPlatformPanel>;

export const HealthyAndFullCatalog: Story = {
  render: () => (
    <StubProvider
      stubs={[
        { url: '/v1/models', body: { object: 'list', data: FULL_MODELS } },
        { url: '/runtime/services', body: FULL_SERVICES },
        { url: '/runtime/vendor', body: { os: 'linux', arch: 'x86_64', services: [] } },
        { url: '/v1/admin/doctor', body: HEALTHY_DOCTOR },
      ]}
    >
      <AiPlatformPanel />
    </StubProvider>
  ),
};

export const EmptyCatalog: Story = {
  render: () => (
    <StubProvider
      stubs={[
        { url: '/v1/models', body: { object: 'list', data: [] } },
        { url: '/runtime/services', body: { data: [] } },
        { url: '/runtime/vendor', body: { os: 'linux', arch: 'x86_64', services: [] } },
        { url: '/v1/admin/doctor', body: { ...HEALTHY_DOCTOR, adapters: [] } },
      ]}
    >
      <AiPlatformPanel />
    </StubProvider>
  ),
};

export const ServicesUnreachable: Story = {
  render: () => (
    <StubProvider
      stubs={[
        { url: '/v1/models', body: { object: 'list', data: FULL_MODELS } },
        { url: '/runtime/services', body: { detail: 'internal error' }, status: 500 },
        { url: '/runtime/vendor', body: { detail: 'internal error' }, status: 500 },
        {
          url: '/v1/admin/doctor',
          body: {
            ...HEALTHY_DOCTOR,
            preflight: { ok: false, checks: [{ name: 'docker', ok: false, message: 'Docker daemon not reachable' }] },
            adapters: [
              { name: 'ollama', kind: 'http', probe_url: 'http://127.0.0.1:11434/api/tags', ok: false, error: 'connect: connection refused' },
            ],
          },
        },
      ]}
    >
      <AiPlatformPanel />
    </StubProvider>
  ),
};
