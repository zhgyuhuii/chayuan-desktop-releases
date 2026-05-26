/**
 * startCapabilityChat 单测 — 行为合同:
 *   1. setModel 必须被以 (modelId, platformName) 调用一次
 *   2. setActive(null) 必须被调用一次(进入草稿态)
 *   3. tabs.open('/chat', { forceNew: true, title 包含 cap label }) 一次
 *   4. navigate('/chat') 最后调
 *
 * 严格按顺序,任何一步漏掉都算回归。
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { startCapabilityChat } from '../startCapabilityChat';
import { useComposerStore } from '../../../../store/composer';
import { useTabsStore } from '../../../../store/tabs';

describe('startCapabilityChat', () => {
  beforeEach(() => {
    // 重置 store 状态
    const c = useComposerStore.getState();
    c.setModel('', undefined);
    c.setActive(null);
  });

  it('已就绪 cap → setModel + setActive(null) + open new tab + navigate', () => {
    const setModel = vi.spyOn(useComposerStore.getState(), 'setModel');
    const setActive = vi.spyOn(useComposerStore.getState(), 'setActive');
    const open = vi.spyOn(useTabsStore.getState(), 'open');
    const navigate = vi.fn();

    startCapabilityChat(
      {
        cap: 't2i',
        model: {
          id: 'qwen-image-max',
          object: 'model',
          platform_name: 'bailian',
          model_type: 'text2image',
          available: true,
        },
      },
      { navigate },
    );

    expect(setModel).toHaveBeenCalledWith('qwen-image-max', 'bailian');
    expect(setActive).toHaveBeenCalledWith(null);
    expect(open).toHaveBeenCalledWith(
      '/chat',
      expect.objectContaining({
        forceNew: true,
        // title 包含 capability 短标签(t2i = "文生图")
        title: expect.stringContaining('文生图'),
      }),
    );
    expect(navigate).toHaveBeenCalledWith('/chat');
  });

  it('不同 cap 在 tab title 里反映对应中文标签', () => {
    const open = vi.spyOn(useTabsStore.getState(), 'open');
    const navigate = vi.fn();

    startCapabilityChat(
      {
        cap: 'tts',
        model: { id: 'cosyvoice-v1', object: 'model', platform_name: 'bailian' },
      },
      { navigate },
    );

    expect(open).toHaveBeenCalledWith(
      '/chat',
      expect.objectContaining({ title: expect.stringContaining('语音合成') }),
    );
  });
});
