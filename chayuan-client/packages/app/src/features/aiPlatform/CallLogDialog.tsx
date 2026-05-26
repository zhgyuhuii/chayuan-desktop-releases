/**
 * 调用日志 Dialog — 显示 ASR / OCR 最近调用记录(2s 轮询)。
 *
 * 用户从「本地模型服务」分组顶部「调用日志」按钮打开。每行展示:
 *   - 时间戳(本地时间,精确到秒)
 *   - 成功/失败徽标(success / 后端返空 / exception)
 *   - 耗时 ms / 入参字节数
 *   - 文本预览(asr 转写文本 / ocr 识别文字,截 300 字符)
 *   - 错误信息(如果 fail)
 *   - extra 字段:filename / language / audio_format / box_count 等
 *
 * 当前 key 范围:'asr' / 'ocr'(后端 modality_routes.py 只在 transcribe/ocr endpoint 调 record_call)。
 */
import * as React from 'react';
import { ClipboardCopy, RefreshCw } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@chayuan/ui';
import { modality, type CallLogEntry } from '@chayuan/api';
import { notifySuccess, reportError } from '../../store/errorDialog';

type LogKey = 'asr' | 'ocr';

const KEY_LABEL: Record<LogKey, string> = {
  asr: '语音识别 (ASR)',
  ocr: '图像 OCR',
};

export interface CallLogDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 初始 tab,默认 'asr'。*/
  initialKey?: LogKey;
}

export const CallLogDialog: React.FC<CallLogDialogProps> = ({
  open, onOpenChange, initialKey,
}) => {
  const [key, setKey] = React.useState<LogKey>(initialKey ?? 'asr');
  const [entries, setEntries] = React.useState<CallLogEntry[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [autoRefresh, setAutoRefresh] = React.useState(true);

  // open 切换或 key 切换时立即拉一次
  const fetchOnce = React.useCallback(async (k: LogKey) => {
    setLoading(true);
    try {
      const r = await modality.callLog(k, 200);
      setEntries(r.entries ?? []);
    } catch (e) {
      // dialog 关闭瞬间 / 切 tab 瞬间 in-flight 请求会被 abort,这是正常清理流程,
      // 不弹错误框。只匹配 AbortError 名字 / 中文消息,不吞掉真正网络错误。
      const isAbort = e instanceof Error && (
        e.name === 'AbortError' ||
        /aborted|abort|已中止/i.test(e.message)
      );
      if (!isAbort) reportError(e, '读取调用日志失败');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!open) return;
    void fetchOnce(key);
  }, [open, key, fetchOnce]);

  // 2s 轮询(open && autoRefresh 时启动)
  React.useEffect(() => {
    if (!open || !autoRefresh) return;
    const t = setInterval(() => { void fetchOnce(key); }, 2000);
    return () => clearInterval(t);
  }, [open, autoRefresh, key, fetchOnce]);

  const copyAll = () => {
    const text = entries.map((e) => formatLine(e)).join('\n');
    navigator.clipboard.writeText(text).then(
      () => notifySuccess(`已复制 ${entries.length} 条日志到剪贴板`),
      () => reportError(new Error('navigator.clipboard 不可用'), '复制失败'),
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>多模态调用日志</DialogTitle>
          <DialogDescription>
            后端 in-memory 环形 buffer(最多 200 条/项),进程重启即丢。
            每 2 秒自动刷新,可关闭。
          </DialogDescription>
        </DialogHeader>

        {/* tab + 工具栏 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--cy-border-subtle)] pb-2">
          <div className="inline-flex h-8 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-0.5 text-xs">
            {(Object.keys(KEY_LABEL) as LogKey[]).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKey(k)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full px-3 transition',
                  key === k
                    ? 'bg-[var(--cy-brand-500)] text-white shadow'
                    : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-1)]',
                )}
              >
                {KEY_LABEL[k]}
              </button>
            ))}
          </div>
          <span className="text-xs text-[var(--cy-text-tertiary)]">{entries.length} 条</span>
          <label className="ml-auto flex items-center gap-1 text-xs text-[var(--cy-text-secondary)]">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            自动刷新 (2s)
          </label>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void fetchOnce(key)}
            disabled={loading}
            className="h-7 text-xs"
          >
            <RefreshCw className={cn('mr-1 h-3 w-3', loading && 'animate-spin')} />
            刷新
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={copyAll}
            disabled={entries.length === 0}
            className="h-7 text-xs"
            title="把所有日志拷到剪贴板,方便贴给排查人员"
          >
            <ClipboardCopy className="mr-1 h-3 w-3" />
            复制全部
          </Button>
        </div>

        <div className="max-h-[60vh] min-h-[200px] overflow-y-auto rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] text-xs">
          {entries.length === 0 ? (
            <div className="flex h-32 items-center justify-center text-[var(--cy-text-tertiary)]">
              {loading ? '加载中…' : `还没有 ${KEY_LABEL[key]} 调用记录`}
            </div>
          ) : (
            <ul className="divide-y divide-[var(--cy-border-subtle)]">
              {[...entries].reverse().map((e, i) => (
                <LogRow key={`${e.ts}-${i}`} entry={e} />
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const LogRow: React.FC<{ entry: CallLogEntry }> = ({ entry }) => {
  const ts = new Date(entry.ts);
  const tsLabel = `${pad(ts.getHours())}:${pad(ts.getMinutes())}:${pad(ts.getSeconds())}`;
  const dateLabel = `${ts.getMonth() + 1}/${ts.getDate()}`;
  const ok = entry.success;
  const extras: string[] = [];
  for (const k of ['filename', 'language', 'audio_format', 'lang', 'box_count', 'port']) {
    const v = entry[k];
    if (v != null && v !== '') extras.push(`${k}=${String(v)}`);
  }
  return (
    <li className="px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[10px] text-[var(--cy-text-tertiary)]">{dateLabel} {tsLabel}</span>
        <span
          className={cn(
            'inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
            ok
              ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
              : 'bg-red-500/15 text-red-700 dark:text-red-300',
          )}
        >
          {ok ? '成功' : '失败'}
        </span>
        <span className="text-[10px] text-[var(--cy-text-secondary)]">
          {entry.duration_ms} ms · {formatBytes(entry.bytes_in)}
        </span>
        {extras.length > 0 && (
          <span className="text-[10px] text-[var(--cy-text-tertiary)]">{extras.join(' · ')}</span>
        )}
      </div>
      {entry.preview && (
        <div className="mt-1 whitespace-pre-wrap break-words rounded bg-[var(--cy-surface-1)] px-2 py-1 text-[var(--cy-text-primary)]">
          {entry.preview}
        </div>
      )}
      {entry.error && (
        <div className="mt-1 whitespace-pre-wrap break-words rounded border border-red-500/30 bg-red-500/5 px-2 py-1 text-red-700 dark:text-red-300">
          {entry.error}
        </div>
      )}
    </li>
  );
};

function formatLine(e: CallLogEntry): string {
  return `[${e.ts}] ${e.success ? 'OK' : 'FAIL'} ${e.duration_ms}ms ${e.bytes_in}B preview=${JSON.stringify(e.preview || '')} error=${JSON.stringify(e.error || '')}`;
}

function pad(n: number): string { return String(n).padStart(2, '0'); }

function formatBytes(n: number): string {
  if (!n) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default CallLogDialog;
