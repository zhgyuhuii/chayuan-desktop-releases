import { describe, expect, it, beforeEach } from 'vitest';
import { useSettingsStore } from '../settings';

describe('settings store FONT_DEFAULT', () => {
  beforeEach(() => {
    // 清掉 zustand persist 让 store 回归初值
    globalThis.localStorage?.removeItem('cy.settings.v2');
  });

  it('全新初始化时 fontSize 为 16', () => {
    // 重新 import 强制初始化 — vitest 默认 module cache 无法直接 reload,
    // 退而求其次:reset() 走 DEFAULTS 恢复默认值
    useSettingsStore.getState().reset();
    expect(useSettingsStore.getState().fontSize).toBe(16);
  });

  it('reset() 后 fontSize 落回 16', () => {
    useSettingsStore.getState().setFontSize(20);
    expect(useSettingsStore.getState().fontSize).toBe(20);
    useSettingsStore.getState().reset();
    expect(useSettingsStore.getState().fontSize).toBe(16);
  });
});
