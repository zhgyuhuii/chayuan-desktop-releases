import { browser, $, expect } from '@wdio/globals';

describe('桌面 e2e：发消息 + 反馈', () => {
  before(async () => {
    // 假设已通过 spec 01 进入主面板；若独立运行需重做登录
    await browser.pause(1000);
    const greeting = await $('h1*=察元 AI');
    if (!(await greeting.isDisplayed())) {
      await $('input[type="text"]').setValue('tester');
      await $('input[type="password"]').setValue('test1234');
      await $('button=登录').click();
      await browser.pause(2000);
    }
  });

  it('发送 → 流式输出', async () => {
    const textarea = await $('textarea');
    await textarea.setValue('你好');
    await textarea.keys(['Enter']);
    await browser.pause(2500);
    const out = await $('div*=Hello from mock');
    await expect(out).toBeExisting();
  });

  it('点赞 → /chat/feedback 被调用', async () => {
    const thumb = await $('button[aria-label="赞"]');
    await thumb.click();
    await browser.pause(800);
    const stat = await fetch('http://127.0.0.1:7891/__mock__/state').then((r) => r.json());
    expect(stat.captured.feedback.length).toBeGreaterThan(0);
    expect(stat.captured.feedback[0].score).toBe(1);
  });
});
