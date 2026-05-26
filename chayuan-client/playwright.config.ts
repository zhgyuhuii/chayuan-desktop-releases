import { defineConfig, devices } from '@playwright/test';

/**
 * 五条黄金路径 e2e 配置。
 *
 * - 默认对 Web 版（apps/web）跑；桌面版用 Tauri WebDriver 单独跑（未配置 in scope）。
 * - dev server 由 webServer 拉起；端口与 vite 一致 5173。
 * - 后端调用全部在 spec 内通过 page.route 拦截，避免依赖真实 chayuan-server。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: process.env.E2E_NO_SERVER
    ? undefined
    : {
        command: 'pnpm --filter @chayuan/web dev',
        url: 'http://127.0.0.1:5173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
