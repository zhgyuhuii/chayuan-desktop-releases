/**
 * 截图 → 自动附到聊天输入框 的编排 hook(顶层"按需选 UI 路径")。
 *
 * **架构定位**:
 * 唯一封装"截图按钮点击后到底发生什么"的编排层。下层有两条互斥实现:
 *
 *   ① **Desktop(Tauri):系统级 overlay**(微信 / QQ / Lightshot 风格)
 *      ``platform.capture.screenshotInteractive()`` 走 Tauri 创独立无边框透明
 *      窗口覆盖整个屏幕,用户在桌面任意位置拖框,确认后直接拿到**已裁剪**的
 *      Blob — 主窗这里只负责接 Blob 入附件,没有任何选区 UI。
 *
 *   ② **Web fallback:应用内 modal 选区**
 *      浏览器没有"全屏覆盖整个桌面"的能力,只能 ``getDisplayMedia`` 让用户选
 *      屏后,在 chayuan 主窗内部弹个 ``ScreenshotRegionDialog`` 让用户拖框裁剪。
 *
 * 上层 caller(ChatComposer / KbBoard / ConversationView)对此**完全无感**:
 * 都只看到 ``onScreenshot`` 一个回调 + 可选 ``dialog`` 节点。
 */
import * as React from 'react';
import { getPlatform } from '@chayuan/platform-shared';
import { reportError } from '../../store/errorDialog';
import { ScreenshotRegionDialog } from './ScreenshotRegionDialog';

export interface ChatScreenshotApi {
  available: boolean;
  busy: boolean;
  onScreenshot: () => void;
  /** Web fallback 路径用的选区 dialog;consumer 放进 render tree。
   *  Desktop 路径不需要(系统级 overlay 是独立窗),始终为 null。 */
  dialog: React.ReactNode;
}

export function useChatScreenshot(
  addImages: (files: File[]) => Promise<void>,
): ChatScreenshotApi {
  const pal = getPlatform();
  const hasInteractive = typeof pal.capture?.screenshotInteractive === 'function';
  const hasScreenshot = typeof pal.capture?.screenshot === 'function';
  // Desktop 走 interactive;Web 走 screenshot + 应用内选区
  const available = hasInteractive
    || (hasScreenshot && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getDisplayMedia);

  const [busy, setBusy] = React.useState(false);
  const inFlightRef = React.useRef(false);
  const [webCaptured, setWebCaptured] = React.useState<Blob | null>(null);

  const finishWith = React.useCallback(async (cropped: Blob | null) => {
    inFlightRef.current = false;
    setWebCaptured(null);
    setBusy(false);
    if (!cropped) return;
    const ts = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const name = `screenshot-${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}-${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}.png`;
    const file = new File([cropped], name, { type: cropped.type || 'image/png' });
    try {
      await addImages([file]);
    } catch (e) {
      reportError(e, '截图加入附件失败');
    }
  }, [addImages]);

  const onScreenshot = React.useCallback(() => {
    if (!available) {
      reportError(new Error('当前环境不支持截图'),
        '截图不可用 — 桌面版请确认已正确启动,Web 版需 HTTPS + 现代浏览器');
      return;
    }
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setBusy(true);

    (async () => {
      // 优先 desktop 系统级 overlay
      if (hasInteractive) {
        try {
          const cropped = await pal.capture!.screenshotInteractive!();
          await finishWith(cropped);
        } catch (e) {
          const name = (e instanceof DOMException) ? e.name : '';
          if (name !== 'AbortError' && name !== 'NotAllowedError') {
            reportError(e, '截图失败');
          }
          inFlightRef.current = false;
          setBusy(false);
        }
        return;
      }
      // Web fallback
      try {
        const raw = await pal.capture!.screenshot();
        setWebCaptured(raw);
      } catch (e) {
        const name = (e instanceof DOMException) ? e.name : '';
        if (name !== 'NotAllowedError' && name !== 'AbortError') {
          reportError(e, '截图失败');
        }
        inFlightRef.current = false;
        setBusy(false);
      }
    })();
  }, [available, hasInteractive, pal, finishWith]);

  const dialog = webCaptured ? (
    <ScreenshotRegionDialog
      blob={webCaptured}
      onConfirm={(cropped) => void finishWith(cropped)}
      onCancel={() => void finishWith(null)}
    />
  ) : null;

  return { available, busy, onScreenshot, dialog };
}
