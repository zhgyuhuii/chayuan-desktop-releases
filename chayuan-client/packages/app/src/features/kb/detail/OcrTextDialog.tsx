/**
 * 图片 OCR 文字查看弹窗。
 *
 * 触发方:图像 KB 卡片上的「含文字」徽标(点击)。
 * 用途:OCR 文本可能很长 / 多行,徽标的 title tooltip 既看不全也没法复制 ——
 *       这里完整展示 + 一键复制全文。
 */

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@chayuan/ui';
import { Copy } from 'lucide-react';
import * as React from 'react';

export interface OcrTextDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 要展示的 OCR 文本。 */
  text: string;
}

export const OcrTextDialog: React.FC<OcrTextDialogProps> = ({ open, onOpenChange, text }) => {
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  const onCopy = () => {
    if (!text) return;
    void navigator.clipboard?.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>图片文字(OCR)</DialogTitle>
          <DialogDescription>从图片里识别出的文字,可选中或一键复制。</DialogDescription>
        </DialogHeader>
        <div className="max-h-[55vh] overflow-y-auto">
          <pre className="whitespace-pre-wrap break-words rounded-md bg-[var(--cy-surface-1)] p-3 text-sm leading-relaxed text-[var(--cy-text-primary)]">
            {text || '(无文字)'}
          </pre>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={onCopy} disabled={!text}>
            <Copy className="mr-1 h-3.5 w-3.5" />
            {copied ? '已复制' : '复制全文'}
          </Button>
          <Button type="button" size="sm" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default OcrTextDialog;
