/**
 * useFullscreenWorkspace —— 工作区"专注模式"。
 *
 * 行为:
 *   - 切到全屏:body.classList += 'cy-fullscreen';Sidebar / TabBar 隐藏(由 globals.css 管),
 *     当前 Tab 的 Main 区域占满 100vh。
 *   - F11 触发(浏览器 / 桌面通用);Esc 退出。
 *   - 第一次进入时弹一次提示("按 F11 / 点 ⛶ 切换")。
 *
 * 设计:
 *   - 状态放 zustand 全局,避免 Chrome / 子页面间多份。
 *   - 只跟 cy-embed-mode 共用 `data-shell` 钩子,不与之冲突(embed 优先级更高 — 反正
 *     embed 模式下永远是"独占主区"语义)。
 */
import * as React from 'react';
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface FullscreenState {
  active: boolean;
  /** 提示弹过一次后写 true(localStorage) */
  hintShown: boolean;
  toggle(): void;
  set(active: boolean): void;
  markHintShown(): void;
}

export const useFullscreenStore = create<FullscreenState>()(
  persist(
    (set, get) => ({
      active: false,
      hintShown: false,
      toggle() {
        set({ active: !get().active });
      },
      set(active) {
        set({ active });
      },
      markHintShown() {
        set({ hintShown: true });
      },
    }),
    {
      name: 'cy.fullscreen',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      // active 状态不持久化(刷新后默认非全屏);只持久化 hintShown
      partialize: (s) => ({ hintShown: s.hintShown, active: false }),
    },
  ),
);

/**
 * 在 Chrome 顶层挂一次:监听 F11 / Esc + 同步 body class。
 */
export function useFullscreenWorkspace(): void {
  const active = useFullscreenStore((s) => s.active);
  const setActive = useFullscreenStore((s) => s.set);

  // body class 同步
  React.useEffect(() => {
    document.body.classList.toggle('cy-fullscreen', active);
    return () => {
      document.body.classList.remove('cy-fullscreen');
    };
  }, [active]);

  // 键盘:F11 toggle / Esc 退出
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // 输入框中不响应 F11(避免和浏览器全屏冲突也避免误触)
      const target = e.target as HTMLElement | null;
      const inEditable =
        !!target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);

      if (e.key === 'F11') {
        e.preventDefault();
        setActive(!useFullscreenStore.getState().active);
      } else if (e.key === 'Escape' && useFullscreenStore.getState().active && !inEditable) {
        setActive(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setActive]);
}
