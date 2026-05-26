import { expect, test } from '@playwright/test';
import { installMockBackend } from './helpers/mockBackend';

test('黄金路径 1: 登录 → 发消息 → 流式输出 → 反馈', async ({ page }) => {
  const state = await installMockBackend(page);

  await page.goto('/');

  // 登录页
  await expect(page.getByText('登录察元 AI')).toBeVisible();
  await page.getByLabel(/用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$/ }).click();

  // 进主面板
  await expect(page.getByText('察元 AI')).toBeVisible();
  await expect(page.getByText('问点什么开始对话')).toBeVisible();

  // 发送
  const textarea = page.locator('textarea');
  await textarea.fill('你好');
  await textarea.press('Enter');

  // 流式输出
  await expect(page.getByText('Hello from mock')).toBeVisible({ timeout: 5000 });

  // 反馈：点赞
  await page.getByRole('button', { name: '赞' }).first().click();

  // 验证 /chat/feedback 被调用且 score=1
  await expect.poll(() => state.capturedFeedback.length).toBeGreaterThan(0);
  expect(state.capturedFeedback[0]).toMatchObject({ score: 1, name: 'user_feedback' });
});
