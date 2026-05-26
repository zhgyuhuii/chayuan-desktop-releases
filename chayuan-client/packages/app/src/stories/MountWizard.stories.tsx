/**
 * Storybook: 数据挂载向导 (MountWizard) 视觉回归。
 *
 * 修复后用 Radix Dialog,这套故事固化:
 *   - Step1 选数据源(12 张卡片)
 *   - Step2 配置表单(动态字段)
 *   - Step3 模式 + 范围(corpus 高亮警告 + 目标 KB 输入)
 *   - Step4 预览样本(字段 schema 表格)
 *
 * fetchMock 拦截 /data-mounts/sources + /sources/probe + /sources/analyze。
 */
import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MountWizard } from '../features/annotation/data-mounts/MountWizard';

interface FetchStub {
  url: string | RegExp;
  body: unknown;
  status?: number;
  method?: string;
}

const installFetchStubs = (stubs: FetchStub[]): (() => void) => {
  const originalFetch = window.fetch;
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === 'string' ? input : input.toString();
    const m = (init?.method ?? 'GET').toUpperCase();
    for (const s of stubs) {
      const matched = typeof s.url === 'string' ? u.includes(s.url) : s.url.test(u);
      const methodMatch = !s.method || s.method.toUpperCase() === m;
      if (matched && methodMatch) {
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

const SAMPLE_SOURCES = [
  { type_id: 'kb', label: '知识库', description: '从已建好的本地知识库取切片',
    icon: 'library', capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [
      { name: 'kb_name', label: '知识库名称', type: 'string', required: true },
      { name: 'top_k', label: '条数上限', type: 'int', default: 200 },
    ] } },
  { type_id: 'file', label: '文件 / 文件夹', description: '本地路径或 glob',
    icon: 'folder', capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'path', label: '路径', type: 'string', required: true },
    ] } },
  { type_id: 'web', label: 'Web 网页', description: 'URL 抓取',
    icon: 'globe', capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'urls', label: 'URL', type: 'string', required: true },
    ] } },
  { type_id: 'sql', label: 'SQL', description: 'Postgres / MySQL / SQLite',
    icon: 'database', capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'url', label: 'URL', type: 'password', required: true },
      { name: 'query', label: 'SELECT', type: 'string', required: true },
    ] } },
];

const Wrap: React.FC<{ stubs: FetchStub[]; children: React.ReactNode }> = ({ stubs, children }) => {
  React.useEffect(() => installFetchStubs(stubs), [stubs]);
  const qc = React.useMemo(() => new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  }), []);
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

const meta: Meta<typeof MountWizard> = {
  title: 'annotation/MountWizard',
  component: MountWizard,
  parameters: { layout: 'fullscreen' },
};
export default meta;
type Story = StoryObj<typeof MountWizard>;

const defaultStubs: FetchStub[] = [
  { url: '/data-mounts/sources', body: { code: 0, data: SAMPLE_SOURCES } },
  { url: '/data-mounts/sources/probe',
    body: { code: 0, data: { status: 'ok', message: '可访问', counted: 1234 } } },
  { url: '/data-mounts/sources/analyze',
    body: { code: 0, data: {
      total_estimate: 200,
      items: [
        { id: 'doc-1', text: 'KB 切片样例 1: ...', metadata: { source: 'kb:legal', score: 0.92 } },
        { id: 'doc-2', text: 'KB 切片样例 2: ...', metadata: { source: 'kb:legal', score: 0.88 } },
      ],
      fields: [
        { name: 'source', type: 'string', sample_values: ['kb:legal'], fill_rate: 1.0, unique_count: 1, notes: '' },
        { name: 'score',  type: 'float',  sample_values: [0.92, 0.88], fill_rate: 1.0, unique_count: 2, notes: '' },
      ],
    } } },
];

export const NewDraft: Story = {
  render: () => (
    <Wrap stubs={defaultStubs}>
      <MountWizard initial={null} onClose={() => undefined} onCreated={() => undefined} />
    </Wrap>
  ),
};

export const EditExisting: Story = {
  render: () => (
    <Wrap stubs={defaultStubs}>
      <MountWizard
        initial={{
          id: 'm-edit-1',
          name: '法务库 fewshot',
          description: '从法务 KB 抽 30 条作 fewshot',
          scope_type: 'user',
          scope_id: '',
          source_filter: {
            spec: {
              source_type: 'kb',
              options: { kb_name: 'legal_kb', top_k: 30 },
              max_items: 30,
            },
          },
          mount_modes: ['fewshot', 'context'],
          priority: 1,
          max_items: 30,
          max_tokens: 1200,
          enabled: true,
          status: 'draft',
          version: 1,
        }}
        onClose={() => undefined}
        onCreated={() => undefined}
      />
    </Wrap>
  ),
};
