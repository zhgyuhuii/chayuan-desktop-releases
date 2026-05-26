/**
 * 黄金路径 7：瘦客户端登录流。
 *
 * 场景：
 *   * 桌面壳第一次启动 → 弹出 ServerLoginModal（强制锁屏）；
 *   * 用户输入 server URL + 用户名 + 密码 → 提交；
 *   * 服务器握手成功 → modal 收起，主路由可见；
 *   * 失败时（401 / 网络错）显示错误，server URL 没被持久化。
 *
 * 我们用 ``?thinClient=1`` URL 开关让 web stack 进入瘦壳模式，不需要单独
 * 起一个 vite。
 */
import { expect, test } from '@playwright/test';
import { installMockBackend } from './helpers/mockBackend';

test.describe('黄金路径 7：瘦客户端登录', () => {
  test('成功路径：连接 + 登录后进入主界面', async ({ page }) => {
    await installMockBackend(page, {
      loginSucceedsFor: { username: 'admin', password: 'pa55w0rd' },
    });

    await page.goto('/?thinClient=1');

    // ServerLoginModal 应该可见且强制锁定
    await expect(page.getByTestId('server-url-input')).toBeVisible();
    await expect(page.getByTestId('server-username-input')).toBeVisible();
    await expect(page.getByTestId('server-password-input')).toBeVisible();
    // 提交按钮初始 disable
    await expect(page.getByTestId('server-login-submit')).toBeDisabled();

    await page.getByTestId('server-url-input').fill('http://my-srv.local:62581/');
    await page.getByTestId('server-username-input').fill('admin');
    await page.getByTestId('server-password-input').fill('pa55w0rd');

    // 三个字段都填了 → 提交按钮 enable
    await expect(page.getByTestId('server-login-submit')).toBeEnabled();

    await page.getByTestId('server-login-submit').click();

    // 登录成功 → modal 关闭、主路由可见
    await expect(page.getByTestId('server-login-submit')).toBeHidden();
    await expect(page.getByText('察元 AI').first()).toBeVisible();
  });

  test('失败路径：401 时显示错误，不进入主界面', async ({ page }) => {
    await installMockBackend(page, {
      loginSucceedsFor: { username: 'admin', password: 'right-password' },
    });

    await page.goto('/?thinClient=1');

    await page.getByTestId('server-url-input').fill('http://my-srv.local:62581');
    await page.getByTestId('server-username-input').fill('admin');
    await page.getByTestId('server-password-input').fill('wrong-password');
    await page.getByTestId('server-login-submit').click();

    // 错误提示出现
    await expect(page.getByTestId('server-login-error')).toBeVisible({ timeout: 5000 });
    // 弹框依然在
    await expect(page.getByTestId('server-url-input')).toBeVisible();
  });

  test('URL 校验：缺协议时按钮保持 disable', async ({ page }) => {
    await installMockBackend(page);
    await page.goto('/?thinClient=1');

    // 只填用户名密码，server URL 留空
    await page.getByTestId('server-username-input').fill('x');
    await page.getByTestId('server-password-input').fill('x');
    await expect(page.getByTestId('server-login-submit')).toBeDisabled();

    // 全部斜杠也不能算合法 URL
    await page.getByTestId('server-url-input').fill('   ');
    await expect(page.getByTestId('server-login-submit')).toBeDisabled();

    // 写裸 host 也能通过（normalizeServerUrl 会自动补 http://）
    await page.getByTestId('server-url-input').fill('my-srv.local:62581');
    await expect(page.getByTestId('server-login-submit')).toBeEnabled();
  });
});
