/**
 * Storybook: 训练数据中心「数据挂载」tab 的几种状态。
 *
 * 用 fetchMock 拦截 /data-mounts/* 各端点;chromatic 截图覆盖:
 *   - Empty:第一次进还没有任何挂载
 *   - Populated:已有 4 条 mount(多种 source / 状态)
 *   - WizardStep1:点新建 → 选数据源(12 张卡片)
 *   - WizardStep4:走到第 4 步看到字段 schema 表格
 */
import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DataMountsPanel } from '../features/annotation/data-mounts/DataMountsPanel';

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

const Wrap: React.FC<{ stubs: FetchStub[]; children: React.ReactNode }> = ({ stubs, children }) => {
  React.useEffect(() => installFetchStubs(stubs), [stubs]);
  const qc = React.useMemo(() => new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  }), []);
  return (
    <QueryClientProvider client={qc}>
      <div className="h-[640px] w-[1100px]">{children}</div>
    </QueryClientProvider>
  );
};

const SAMPLE_SOURCES = [
  { type_id: 'kb', label: '知识库', description: '从已建好的本地知识库取切片',
    icon: 'library', capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [{ name: 'kb_name', label: '知识库名称', type: 'string', required: true }] } },
  { type_id: 'file', label: '文件 / 文件夹', description: '本地路径或 glob;PDF/Doc/CSV 自动 loader',
    icon: 'folder', capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [{ name: 'path', label: '路径', type: 'string', required: true }] } },
  { type_id: 'web', label: 'Web 网页', description: 'URL 抓取;langchain WebBaseLoader',
    icon: 'globe', capabilities: ['corpus', 'context'], spec_form: { fields: [{ name: 'urls', label: 'URLs', type: 'string', required: true }] } },
  { type_id: 'sql', label: 'SQL 数据库', description: 'Postgres / MySQL / SQLite',
    icon: 'database', capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [{ name: 'url', label: 'URL', type: 'password', required: true }] } },
  { type_id: 'annotation', label: '标注样本', description: '已通过的标注任务样本',
    icon: 'tag', capabilities: ['context', 'fewshot', 'preference', 'safety'],
    spec_form: { fields: [{ name: 'status', label: '状态', type: 'select', options: [{ value: 'approved', label: '已通过' }] }] } },
  { type_id: 'conversation', label: '历史对话', description: 'thumbs-up 对话 → fewshot / preference',
    icon: 'message-square', capabilities: ['fewshot', 'preference'],
    spec_form: { fields: [{ name: 'min_thumbs', label: '最少点赞', type: 'int' }] } },
];

const SAMPLE_MOUNTS = [
  {
    id: 'm1', name: '法务库 corpus 候选', description: '某律所知识库;corpus 模式',
    scope_type: 'global', scope_id: '',
    source_filter: { spec: { source_type: 'kb', options: { kb_name: 'legal_kb' } }, target_kb: 'main_kb' },
    mount_modes: ['corpus'], priority: 5, max_items: 1000, max_tokens: 1600,
    enabled: true, status: 'published', version: 2,
    update_time: new Date().toISOString(),
  },
  {
    id: 'm2', name: 'FAQ 例 fewshot', description: '从 annotation 抽 50 条;fewshot 模式',
    scope_type: 'user', scope_id: '12',
    source_filter: { spec: { source_type: 'annotation', options: { status: 'approved' } } },
    mount_modes: ['fewshot'], priority: 0, max_items: 50, max_tokens: 1200,
    enabled: true, status: 'published', version: 1,
    update_time: new Date(Date.now() - 86400_000).toISOString(),
  },
  {
    id: 'm3', name: '内部 wiki 抓取', description: 'Confluence space; context 模式',
    scope_type: 'kb', scope_id: 'main_kb',
    source_filter: { spec: { source_type: 'confluence', options: {} } },
    mount_modes: ['context'], priority: 1, max_items: 200, max_tokens: 1600,
    enabled: true, status: 'draft', version: 1,
    update_time: new Date(Date.now() - 3600_000).toISOString(),
  },
  {
    id: 'm4', name: '安全规则集', description: 'SQL 配置表;safety 注入',
    scope_type: 'global', scope_id: '',
    source_filter: { spec: { source_type: 'sql', options: {} } },
    mount_modes: ['safety'], priority: 10, max_items: 30, max_tokens: 800,
    enabled: false, status: 'disabled', version: 3,
    update_time: new Date(Date.now() - 7 * 86400_000).toISOString(),
  },
];

const meta: Meta<typeof DataMountsPanel> = {
  title: 'annotation/DataMountsPanel',
  component: DataMountsPanel,
  parameters: { layout: 'fullscreen' },
};
export default meta;
type Story = StoryObj<typeof DataMountsPanel>;

export const Empty: Story = {
  render: () => (
    <Wrap stubs={[
      { url: '/data-mounts/sources', body: { code: 0, data: SAMPLE_SOURCES } },
      { url: /\/data-mounts(\?|$)/, body: { code: 0, data: [] } },
    ]}>
      <DataMountsPanel />
    </Wrap>
  ),
};

export const Populated: Story = {
  render: () => (
    <Wrap stubs={[
      { url: '/data-mounts/sources', body: { code: 0, data: SAMPLE_SOURCES } },
      { url: /\/data-mounts(\?|$)/, body: { code: 0, data: SAMPLE_MOUNTS } },
    ]}>
      <DataMountsPanel />
    </Wrap>
  ),
};
