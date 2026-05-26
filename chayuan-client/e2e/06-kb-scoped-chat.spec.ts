import { expect, test } from '@playwright/test';
import { installMockBackend, buildSimpleStream } from './helpers/mockBackend';

/**
 * 黄金路径 6:登录 → /kb 多选知识库 → 在 KbBoard composer 提问 → 主区切到 Thread
 *                              → 右侧 chip rail 折叠态出现
 *
 * 关注点不在 LLM 输出本身(那是 spec 1 的事),而在 KbBoard 双态状态机:
 *   PICKER → THREAD+RAIL → 点 rail 按钮 → PICKER+OVERLAY
 */

test('黄金路径 6: KB 多选 → 提问 → 折叠到右栏 → 重新展开', async ({ page }) => {
  await installMockBackend(page, {
    chatStreamBody: buildSimpleStream('依据所选知识库回答如下...'),
  });

  // /knowledge_base/list_knowledge_bases mock 返若干 KB
  await page.route('**/knowledge_base/list_knowledge_bases', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 0,
        data: [
          {
            id: 1,
            kb_name: 'docs-internal',
            kb_info: '内部文档库',
            visibility: 'public',
            file_count: 12,
            create_time: new Date().toISOString(),
          },
          {
            id: 2,
            kb_name: 'product-spec',
            kb_info: '产品规格',
            visibility: 'public',
            file_count: 7,
            create_time: new Date().toISOString(),
          },
        ],
      }),
    });
  });

  await page.goto('/');

  // 登录(LoginModal 在主页右上角)
  await page.getByLabel(/账号|用户名/).fill('tester');
  await page.getByLabel(/密码/).fill('test1234');
  await page.getByRole('button', { name: /^登录$|下一步/ }).click();

  // 跳转到 /kb
  await page.goto('/kb');

  // KB 卡片可见(picker 态)
  await expect(page.getByText('docs-internal')).toBeVisible();
  await expect(page.getByText('product-spec')).toBeVisible();

  // 勾选第一个 KB
  await page.getByText('docs-internal').click();

  // 选择 toolbar 应显示"已选 1 个"
  await expect(page.getByText(/已选 1 个/)).toBeVisible();

  // 在底部 composer 输入并发送
  const textarea = page.locator('textarea');
  await textarea.fill('这份文档讲了什么');
  await textarea.press('Enter');

  // 主区切到 Thread,右侧 rail 出现(40px 竖条 + PanelRightOpen 按钮)
  await expect(page.getByText('依据所选知识库回答如下...')).toBeVisible({ timeout: 5000 });
  const railToggle = page.getByRole('button', { name: /已选 1 个知识库 — 点击展开/ });
  await expect(railToggle).toBeVisible();

  // 点 rail 按钮 → picker 重新铺满主区(overlay)
  await railToggle.click();
  await expect(page.getByText('docs-internal')).toBeVisible();

  // 右上 X 关掉 overlay → 回 chat 态
  // (overlay header 上的关闭按钮,aria-label 是 i18n 'knowledge.rail.expanded')
  await page.getByRole('button', { name: /收起选择面板|Collapse picker/ }).click();
  await expect(page.getByText('依据所选知识库回答如下...')).toBeVisible();
});
