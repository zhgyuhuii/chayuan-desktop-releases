/**
 * 本地 runtime 诊断报告 Dialog。
 *
 * 触发方:LocalRuntimePanel 的「生成诊断报告」按钮。
 * 行为:
 *   1. open=true 时调 diagnose.run()
 *   2. 拿到 DiagnoseReport → 渲染 markdown
 *   3. 复制按钮把 markdown 塞 navigator.clipboard
 *   4. 调用失败显友好错误,不闪退
 */

import * as React from 'react';
import { Copy } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@chayuan/ui';
import { diagnose, type DiagnoseReport } from '@chayuan/api';

export interface DiagnoseModalProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

const SEV_ICON: Record<string, string> = { ok: '✓', warn: '⚠', fail: '✗' };

function renderMarkdown(r: DiagnoseReport): string {
  const head =
    `# Chayuan 本地 Runtime 诊断报告\n\n` +
    `- 时间: ${r.timestamp}\n` +
    `- 平台: ${r.platform}\n` +
    `- Python: ${r.python_version}\n` +
    `- chayuan-server: ${r.chayuan_server_version}\n` +
    `- chayuan_root: ${r.chayuan_root}\n\n` +
    `## 结果: ${r.summary.ok} ✓ / ${r.summary.warn} ⚠ / ${r.summary.fail} ✗\n\n`;
  const rows = r.checks
    .map((c) => `| ${c.name} | ${SEV_ICON[c.severity] ?? '?'} | ${c.detail.replace(/\|/g, '\\|')} |`)
    .join('\n');
  return `${head}| 检查项 | 状态 | 说明 |\n|---|---|---|\n${rows}\n`;
}

export const DiagnoseModal: React.FC<DiagnoseModalProps> = ({ open, onOpenChange }) => {
  const [loading, setLoading] = React.useState(false);
  const [report, setReport] = React.useState<DiagnoseReport | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      setReport(null);
      setError(null);
      setCopied(false);
      return;
    }
    setLoading(true);
    setError(null);
    diagnose
      .run()
      .then((r) => setReport(r))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
      })
      .finally(() => setLoading(false));
  }, [open]);

  const md = report ? renderMarkdown(report) : '';

  const onCopy = () => {
    if (!md) return;
    void navigator.clipboard?.writeText(md);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>本地 Runtime 诊断报告</DialogTitle>
          <DialogDescription>
            生成后可复制全文,贴到 GitHub issue / 客服群帮助开发者定位问题。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          {loading && (
            <div className="py-8 text-center text-sm text-[var(--cy-text-tertiary)]">
              正在跑 10 项检查…
            </div>
          )}
          {error && (
            <div className="rounded-md border border-rose-500/30 bg-rose-50 p-3 text-sm text-rose-800 dark:bg-rose-950/30 dark:text-rose-200">
              <div className="font-medium">无法生成报告</div>
              <div className="mt-1 break-all">{error}</div>
              <div className="mt-2 text-xs">
                请先确保桌面应用正在运行;如仍不行,在装机目录跑{' '}
                <code>diagnose.ps1</code> / <code>diagnose.sh</code> 留存日志后上报。
              </div>
            </div>
          )}
          {report && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-[var(--cy-surface-1)] p-3 text-xs text-[var(--cy-text-primary)]">
              {md}
            </pre>
          )}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onCopy}
            disabled={!report}
          >
            <Copy className="mr-1 h-3.5 w-3.5" />
            {copied ? '已复制' : '复制全部'}
          </Button>
          <Button type="button" size="sm" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default DiagnoseModal;
