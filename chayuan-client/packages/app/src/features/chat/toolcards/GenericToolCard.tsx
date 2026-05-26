import * as React from 'react';
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import type { ToolCallProps } from './registry';

/** 兜底卡：任何未知工具都用它，展示原始入参与结果 JSON。 */
export const GenericToolCard: React.FC<ToolCallProps> = ({ name, args, result, status, embedded }) => {
  const argsText = React.useMemo(() => formatJson(args), [args]);
  const resultText = React.useMemo(() => formatJson(result), [result]);

  const body = (
    <>
      {argsText ? (
        <div>
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">入参</div>
          <pre className="overflow-auto whitespace-pre-wrap rounded bg-background/60 p-2 font-mono text-[11px] text-muted-foreground">{argsText}</pre>
        </div>
      ) : null}
      {resultText ? (
        <div className={argsText ? 'mt-1.5' : ''}>
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">结果</div>
          <pre className="overflow-auto whitespace-pre-wrap rounded bg-background/60 p-2 font-mono text-[11px]">{resultText}</pre>
        </div>
      ) : null}
      {!argsText && !resultText ? (
        <span className="text-[11px] text-muted-foreground">无入参与结果数据</span>
      ) : null}
    </>
  );

  if (embedded) return <div className="space-y-1">{body}</div>;

  return (
    <div className="rounded-lg border bg-muted/40 px-3 py-2 text-xs">
      <div className="mb-1 flex items-center gap-2">
        {status === 'start' || status === 'delta' ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        ) : status === 'end' ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <AlertCircle className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">{name}</span>
        <span className="text-muted-foreground">
          {status === 'start' || status === 'delta' ? '调用中…' : status === 'end' ? '已完成' : ''}
        </span>
      </div>
      {body}
    </div>
  );
};

function formatJson(v: unknown): string {
  if (v === undefined || v === null || v === '') return '';
  if (typeof v === 'string') {
    try {
      return JSON.stringify(JSON.parse(v), null, 2);
    } catch {
      return v;
    }
  }
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
