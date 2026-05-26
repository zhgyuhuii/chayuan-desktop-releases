/**
 * WindowControls —— Windows 风格三联键(min / max / close)。
 *
 * 设计:仅 Tauri 桌面端显示;Web 端隐藏(浏览器自带 chrome)。
 * 检测:platform.window.minimize 是否实现(可选 API)。
 *
 * Tauri 2 API 由 platform-tauri 实现层 minimize/maximize/close 提供;
 * 当前 platform-tauri 与 Tauri 2 仍在迁移(M5 治理),所以这里走 try/catch 不让 UI 崩。
 */

import * as React from 'react';
import { Minus, Square, X } from 'lucide-react';
import { getPlatform } from '@chayuan/platform-shared';
import { cn } from '@chayuan/ui';

export const WindowControls: React.FC<{ className?: string }> = ({ className }) => {
  const platform = getPlatform();
  if (platform.kind !== 'desktop') return null;

  const win = platform.window;
  const safe = (fn?: () => Promise<void>) => async () => {
    try {
      await fn?.();
    } catch (e) {
      console.warn('[WindowControls] action failed', e);
    }
  };

  return (
    <div
      className={cn(
        'flex h-9 items-center',
        // 桌面端常见的"非客户区"标记;Tauri 配 dragRegion 用
        '[-webkit-app-region:no-drag]',
        className,
      )}
    >
      <Btn aria-label="minimize" onClick={safe(win.minimize)}>
        <Minus className="h-3.5 w-3.5" />
      </Btn>
      <Btn aria-label="maximize" onClick={safe(win.maximize)}>
        <Square className="h-3 w-3" />
      </Btn>
      <Btn aria-label="close" onClick={safe(win.close)} danger>
        <X className="h-4 w-4" />
      </Btn>
    </div>
  );
};

const Btn: React.FC<
  React.ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean }
> = ({ className, danger, ...props }) => (
  <button
    type="button"
    className={cn(
      'flex h-full w-11 items-center justify-center text-[var(--cy-text-secondary)] transition-colors',
      danger ? 'hover:bg-red-500 hover:text-white' : 'hover:bg-[var(--cy-surface-2)]',
      className,
    )}
    {...props}
  />
);
