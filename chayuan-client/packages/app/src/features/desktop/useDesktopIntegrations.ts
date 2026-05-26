/**
 * 桌面专属集成 hook：
 *
 * - 全局快捷键 ⌘+Shift+Space → 打开 / 收起主窗口（通过 cy:open-cmdk）
 * - 全局快捷键 ⌘+Shift+S → 截图并粘贴到 composer
 * - 监听 cy:new-chat（来自托盘菜单）→ 路由跳新对话
 * - 流式完成且窗口最小化时 → 系统通知
 *
 * 仅在 platform.kind === 'desktop' 时挂载；Web 端 noop。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { getPlatform, isDesktop } from '@chayuan/platform-shared';
import { useComposerStore } from '../../store/composer';
import { useTabsStore } from '../../store/tabs';
// upsertConversation 之前在 onNewChat 预建会话用,改草稿模式后已不需要

export function useDesktopIntegrations(): void {
  const navigate = useNavigate();

  // 全局快捷键
  React.useEffect(() => {
    if (!isDesktop()) return;
    const platform = getPlatform();
    if (!platform.shortcut) return;

    // 包一层 safe wrapper:register() 在快捷键已被占用时会 reject
    // ("HotKey already registered ..."),原代码直接 push promise 不 catch,
    // 启动期就在 console 里堆 unhandled rejection。常见触发:上一进程
    // 异常退出 OS 级 hotkey 还在注册表里(Tauri 2 plugin-global-shortcut 不
    // 强制清理)。这里把失败降级成 noop unregister,不阻塞其它逻辑。
    const safeRegister = (
      accelerator: string,
      handler: () => void | Promise<void>,
    ): Promise<() => Promise<void>> =>
      platform.shortcut!.register(accelerator, handler).catch((e: unknown) => {
        // "HotKey already registered" 在 React StrictMode dev 模式下是常态 —
        // effect 双触发,cleanup 是 async unregister 没赶上第二次 register。
        // 降到 debug 不污染 console;其它真实错误仍 warn。
        const msg = String((e as { message?: string })?.message ?? e);
        if (/already registered/i.test(msg)) {
          console.debug(`[shortcut] register ${accelerator} skipped (already registered)`);
        } else {
          console.warn(`[shortcut] register ${accelerator} failed:`, e);
        }
        return (async () => {}) as () => Promise<void>;
      });

    const offers: Array<Promise<() => Promise<void>>> = [];
    offers.push(
      safeRegister('CmdOrCtrl+Shift+Space', () => {
        // 让 CommandPalette 自己监听该事件；与 ⌘K 复用
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
      }),
    );
    if (platform.capture) {
      offers.push(
        safeRegister('CmdOrCtrl+Shift+S', async () => {
          try {
            const blob = await platform.capture!.screenshot();
            // 粘贴到 composer：转 File → 走 useAttachments 不容易（需要在 composer 范围）；
            // 简化路径：派发自定义事件，由 ChatPage 拦截
            const file = new File([blob], `screenshot-${Date.now()}.png`, { type: 'image/png' });
            window.dispatchEvent(new CustomEvent('cy:paste-image', { detail: { file } }));
          } catch (e) {
            console.warn('screenshot failed', e);
          }
        }),
      );
    }
    return () => {
      offers.forEach((p) => p.then((off) => off()).catch(() => undefined));
    };
  }, []);

  // 来自 Rust 托盘的「新对话」—— 双通道:emit 事件(快) + pending flag 兜底。
  React.useEffect(() => {
    if (!isDesktop()) return;

    // 去重:emit 事件和 pending-flag 兜底可能为同一次点击各触发一遍。
    // 记录上次新建时间,1s 内重复触发直接吞掉,避免开两个 tab。
    let lastNewChatAt = 0;
    const onNewChat = () => {
      const now = Date.now();
      if (now - lastNewChatAt < 1000) return;
      lastNewChatAt = now;
      // 跟 Sidebar.onNewChat 完全一致 — 必须三件套都做,缺一不可:
      //   1. setActive(null):草稿模式,不绑老会话
      //   2. tabs.open('/chat', {forceNew:true}):每次开**新** tab,避免复用
      //      上一个 /chat tab(里面可能正流式,会被打断)
      //   3. navigate('/chat'):路由跳过去
      // 之前只做 1+3 没开 tab:用户在主页 / 其它 tab 时点托盘"新对话",
      // 路由是跳了但 tab 栏没新增、composer 在主页底下不可见 → 表现"无反应"
      useComposerStore.getState().setActive(null);
      useTabsStore.getState().open('/chat', {
        title: '新对话',
        icon: 'message-square',
        forceNew: true,
      });
      void navigate({ to: '/chat' as never });
    };

    // Tauri 通过 webview emit;用 listen API。
    // app 包不直接依赖 @tauri-apps/api(它在 platform-tauri),走动态字符串 import 绕过 TS 模块解析。
    let cleanup: (() => void) | null = null;
    let disposed = false;
    void (async () => {
      try {
        const winName = '@tauri-apps' + '/api/window';
        const winMod = (await import(/* @vite-ignore */ winName)) as {
          getCurrentWindow: () => {
            listen: (event: string, cb: () => void) => Promise<() => void>;
          };
        };
        const win = winMod.getCurrentWindow();
        const off = await win.listen('cy:new-chat', () => onNewChat());
        if (disposed) {
          // 卸载赶在 listen 注册完成之前 — 直接注销,别留悬挂监听
          off();
          return;
        }
        cleanup = off;

        // 兜底:listener 注册「之前」用户可能已点过托盘「新对话」,那次
        // emit 没人收到。listener 就绪后向 Rust 取一次 pending flag,
        // 命中就补触发(onNewChat 内的时间窗去重保证不会和 emit 重复开 tab)。
        const coreName = '@tauri-apps' + '/api/core';
        const coreMod = (await import(/* @vite-ignore */ coreName)) as {
          invoke: <T>(cmd: string) => Promise<T>;
        };
        const pending = await coreMod.invoke<boolean>('chayuan_take_pending_new_chat');
        if (!disposed && pending) onNewChat();
      } catch {
        /* noop in non-tauri */
      }
    })();
    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [navigate]);

  // 流式完成 + 窗口不可见 → 系统通知,提醒用户回来看
  // 订阅 composer.isStreaming 的 true → false 跳变;只在 document.hidden / 失焦时通知
  React.useEffect(() => {
    if (!isDesktop()) return;
    const platform = getPlatform();
    if (!platform.notify) return;

    let lastIsStreaming = useComposerStore.getState().isStreaming;
    const unsub = useComposerStore.subscribe((state) => {
      const cur = state.isStreaming;
      const prev = lastIsStreaming;
      lastIsStreaming = cur;
      // 只关心 true → false 完成跳变
      if (!(prev && !cur)) return;
      // 窗口可见且聚焦 → 用户在看,不打扰
      const visible = typeof document !== 'undefined' && document.visibilityState === 'visible';
      const focused = typeof document !== 'undefined' && document.hasFocus();
      if (visible && focused) return;
      void platform.notify
        .show('察元 AI', '回答已生成,点击窗口查看')
        .catch((e) => console.warn('[notify] failed', e));
    });
    return () => unsub();
  }, []);
}
