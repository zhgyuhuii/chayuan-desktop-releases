import { expect, test } from '@playwright/test';
import { buildSimpleStream, installMockBackend } from './helpers/mockBackend';

test('黄金路径 4: 网络失败 → 错误展示 → 自动重试', async ({ page }) => {
  const state = await installMockBackend(page, { chatFail: true });

  await page.goto('/');
  await page.getByLabel(/用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$/ }).click();

  // 发起；后端 503
  await page.locator('textarea').fill('hi');
  await page.locator('textarea').press('Enter');

  // 应该有错误提示
  await expect(page.locator('text=/error|失败|503/i').first()).toBeVisible({ timeout: 6000 });

  // 切换 mock 为成功
  state.chatFail = false;
  state.chatStreamBody = buildSimpleStream('恢复后的回答');

  // 触发 online 事件 → 自动重发
  await page.evaluate(() => globalThis.dispatchEvent(new Event('online')));

  await expect(page.getByText('恢复后的回答')).toBeVisible({ timeout: 6000 });
});
