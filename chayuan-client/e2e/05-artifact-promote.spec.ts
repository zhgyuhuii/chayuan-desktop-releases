import { expect, test } from '@playwright/test';
import { installMockBackend } from './helpers/mockBackend';

test('黄金路径 5: 长代码块自动 promote 到 Artifact 面板', async ({ page }) => {
  // 构造一段含 mermaid 代码块的回答
  const mermaid = [
    '```mermaid',
    'graph TD',
    'A-->B',
    'B-->C',
    'C-->D',
    '```',
  ].join('\n');
  const sse = `data: ${JSON.stringify({ id: 'm1', choices: [{ delta: { content: mermaid } }] })}\n\ndata: [DONE]\n\n`;

  await installMockBackend(page, { chatStreamBody: sse });
  await page.goto('/');
  await page.getByLabel(/用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$/ }).click();

  await page.locator('textarea').fill('画一个流程图');
  await page.locator('textarea').press('Enter');

  // header 出现 "展开 Artifact"
  await expect(page.getByText(/Artifact \(\d+\)/)).toBeVisible({ timeout: 6000 });

  // 点击展开
  await page.getByRole('button', { name: /展开 Artifact/ }).click();

  // Artifact 面板出现
  await expect(page.getByRole('complementary', { name: 'Artifact 面板' })).toBeVisible();
});
