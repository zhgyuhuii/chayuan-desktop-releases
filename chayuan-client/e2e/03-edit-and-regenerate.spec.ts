import { expect, test } from '@playwright/test';
import { buildSimpleStream, installMockBackend } from './helpers/mockBackend';

test('黄金路径 3: 编辑 / 重新生成', async ({ page }) => {
  const state = await installMockBackend(page);

  await page.goto('/');
  await page.getByLabel(/用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$/ }).click();

  // 第一次发消息
  await page.locator('textarea').fill('讲个笑话');
  await page.locator('textarea').press('Enter');
  await expect(page.getByText(/Hello from mock/)).toBeVisible({ timeout: 5000 });

  // 切换 mock 流，触发"重新生成"应该收到新 stream
  state.chatStreamBody = buildSimpleStream('换一个：……');

  // 找到「重新生成」按钮（aria-label='重新生成'）
  await page.getByRole('button', { name: '重新生成' }).first().click();

  await expect(page.getByText(/换一个：/)).toBeVisible({ timeout: 5000 });
});
