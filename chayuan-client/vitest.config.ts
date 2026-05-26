import { defineConfig } from 'vitest/config';
import { resolve } from 'node:path';

/**
 * 单一 vitest 配置覆盖所有 packages；alias 把 @chayuan/* 指向源码以避免预构建。
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['packages/**/__tests__/**/*.test.{ts,tsx}'],
    globals: false,
    reporters: 'default',
  },
  resolve: {
    alias: {
      '@chayuan/platform-shared': resolve(__dirname, 'packages/platform-shared/src/index.ts'),
      '@chayuan/api': resolve(__dirname, 'packages/api/src/index.ts'),
      '@chayuan/observability': resolve(__dirname, 'packages/observability/src/index.ts'),
      '@chayuan/transport': resolve(__dirname, 'packages/transport/src/index.ts'),
    },
  },
});
