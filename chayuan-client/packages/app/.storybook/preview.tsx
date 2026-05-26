import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TooltipProvider } from '@chayuan/ui';
import { setPlatform, type Platform } from '@chayuan/platform-shared';
import type { Preview } from '@storybook/react';
import '@chayuan/ui/styles.css';

/**
 * 注入最小可运行 platform，让所有依赖 PAL 的组件不抛 "platform 未注入" 错误。
 */
const stubPlatform: Platform = {
  kind: 'web',
  runtime: { appName: 'sb', appVersion: '0.0.0', release: 'sb@0', defaultApiBase: '' },
  secure: { get: async () => null, set: async () => undefined, del: async () => undefined },
  db: { exec: async () => undefined, query: async () => [] },
  fs: {
    pickFiles: async () => [],
    saveText: async () => undefined,
    readDropped: async () => [],
  },
  net: { fetch: globalThis.fetch.bind(globalThis), sse: globalThis.fetch.bind(globalThis) },
  clipboard: { readText: async () => '', writeText: async () => undefined },
  notify: { show: async () => undefined },
  window: { onThemeChange: () => () => undefined, isDarkSystem: () => false },
  shell: { openExternal: async () => undefined, openPath: async () => undefined },
};
setPlatform(stubPlatform);

const qc = new QueryClient({ defaultOptions: { queries: { retry: 0, staleTime: Infinity } } });

const preview: Preview = {
  decorators: [
    (Story) => (
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <div className="bg-background p-6 text-foreground">
            <Story />
          </div>
        </TooltipProvider>
      </QueryClientProvider>
    ),
  ],
  parameters: {
    backgrounds: {
      default: 'light',
      values: [
        { name: 'light', value: '#ffffff' },
        { name: 'dark', value: '#0a0e1a' },
      ],
    },
    actions: { argTypesRegex: '^on[A-Z].*' },
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
  },
};

export default preview;
