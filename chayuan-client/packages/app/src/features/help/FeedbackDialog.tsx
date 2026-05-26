/**
 * 反馈弹窗 — 显示「关注我们」二维码,扫码即可联系或提建议。
 *
 * 二维码图源:images/pay/follow.png(由 brandAssets.FOLLOW_QR_URL 暴露)。
 */

import { Dialog, DialogContent, DialogTitle } from '@chayuan/ui';
import type * as React from 'react';
import { FOLLOW_QR_URL } from '../../lib/brandAssets';

export interface FeedbackDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

export const FeedbackDialog: React.FC<FeedbackDialogProps> = ({ open, onOpenChange }) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xs">
        <DialogTitle className="text-center text-base font-semibold text-[var(--cy-text-primary)]">
          关注我们
        </DialogTitle>
        <div className="flex flex-col items-center gap-2 px-2 py-2 text-center">
          <img
            src={FOLLOW_QR_URL}
            alt="关注我们二维码"
            className="h-56 w-56 rounded-md object-contain"
          />
          <p className="text-xs text-[var(--cy-text-tertiary)]">
            扫码联系我们,反馈问题或交流使用心得
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
};
