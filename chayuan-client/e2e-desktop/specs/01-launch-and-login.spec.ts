import { browser, $, expect } from '@wdio/globals';

describe('桌面 e2e：启动 + 登录', () => {
  it('应用启动后展示登录页', async () => {
    await browser.pause(1500);
    const title = await $('h1*=登录察元 AI');
    await expect(title).toBeDisplayed();
  });

  it('错误密码：401 不闪退', async () => {
    await $('input[type="text"]').setValue('tester');
    await $('input[type="password"]').setValue('wrong');
    await $('button=登录').click();
    await browser.pause(500);
    const errMsg = await $('div*=invalid');
    await expect(errMsg).toBeExisting();
  });

  it('正确凭据：登录成功 → 进入对话页', async () => {
    await $('input[type="text"]').setValue('tester');
    await $('input[type="password"]').setValue('test1234');
    await $('button=登录').click();
    await browser.pause(2000);
    const greeting = await $('h1*=察元 AI');
    await expect(greeting).toBeDisplayed();
  });
});
