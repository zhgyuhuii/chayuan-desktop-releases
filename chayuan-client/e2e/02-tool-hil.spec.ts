import { expect, test } from '@playwright/test';
import { buildHilStream, buildSimpleStream, installMockBackend } from './helpers/mockBackend';

test('黄金路径 2: 工具 HIL 中断 → 批准 → 续跑', async ({ page }) => {
  const state = await installMockBackend(page, {
    chatStreamBody: buildHilStream(),
    resumeStreamBody: buildSimpleStream('搜索完成，结果如下：…'),
  });

  await page.goto('/');
  await page.getByLabel(/用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$/ }).click();

  // 选中 web_search 工具
  await page.getByRole('button', { name: /工具/ }).first().click();
  await page.getByText('联网搜索').click();

  // 发起对话
  await page.locator('textarea').fill('查一下最新天气');
  await page.locator('textarea').press('Enter');

  // 应出现「需要您的批准」
  await expect(page.getByText('需要您的批准')).toBeVisible({ timeout: 5000 });

  // 批准
  await page.getByRole('button', { name: '批准' }).click();

  // /chat/v2/chat/resume 被调用
  await expect.poll(() => state.capturedResume.length).toBeGreaterThan(0);
  expect(state.capturedResume[0]).toMatchObject({ approved: true });

  // 续跑后出现新内容
  await expect(page.getByText(/搜索完成/)).toBeVisible({ timeout: 5000 });
});
