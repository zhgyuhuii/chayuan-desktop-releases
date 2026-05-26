/**
 * 关于弹窗 — 显示 logo、应用名、版本号、厂商、版权信息。
 *
 * 版本号取自 platform.runtime.appVersion(Tauri 注入构建版本;web 走 vite 静态注入)。
 */

import { getPlatform } from '@chayuan/platform-shared';
import { Dialog, DialogContent, DialogTitle } from '@chayuan/ui';
import type * as React from 'react';
import { CHAYUAN_LOGO_URL } from '../../lib/brandAssets';

export interface AboutDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

export const AboutDialog: React.FC<AboutDialogProps> = ({ open, onOpenChange }) => {
  const platform = getPlatform();
  const version = platform.runtime.appVersion;
  const year = new Date().getFullYear();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogTitle className="sr-only">关于察元</DialogTitle>
        <div className="flex flex-col items-center gap-3 px-2 py-4 text-center">
          <img src={CHAYUAN_LOGO_URL} alt="察元" className="h-16 w-16 object-contain" />
          <div>
            <h2 className="text-xl font-semibold text-[var(--cy-text-primary)]">察元</h2>
            <p className="mt-1 text-sm text-[var(--cy-text-secondary)]">
              Chayuan Desktop · 离线 AI 办公助手
            </p>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs text-[var(--cy-text-tertiary)]">
            <p>
              <span className="text-[var(--cy-text-secondary)]">版本号 </span>
              <span className="font-mono">v{version}</span>
            </p>
            <p>
              <span className="text-[var(--cy-text-secondary)]">厂商 </span>
              北京智灵鸟科技中心
            </p>
            <p className="mt-1">© {year} 北京智灵鸟科技中心 版权所有</p>
            <p>All rights reserved.</p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
