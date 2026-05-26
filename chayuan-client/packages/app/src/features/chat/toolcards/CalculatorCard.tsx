import * as React from 'react';
import { Calculator, Loader2 } from 'lucide-react';
import type { ToolCallProps } from './registry';

interface CalcArgs { expression?: string; expr?: string; query?: string }

export const CalculatorCard: React.FC<ToolCallProps> = ({ name, args, result, status, embedded }) => {
  const a = useParsed<CalcArgs>(args);
  const expr = a?.expression ?? a?.expr ?? a?.query ?? '';
  const value = formatResult(result);

  const body = (
    <pre className="rounded bg-background/60 p-2 font-mono text-sm">
      {expr || '—'} <span className="text-muted-foreground">= </span>
      <span className="font-bold">{value}</span>
    </pre>
  );

  if (embedded) return <div>{body}</div>;

  return (
    <div className="rounded-lg border bg-amber-50 px-3 py-2 text-xs dark:bg-amber-950/40">
      <div className="mb-1 flex items-center gap-2">
        {status === 'end' ? <Calculator className="h-3.5 w-3.5 text-amber-500" /> : <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" />}
        <span className="font-medium">{name}</span>
      </div>
      {body}
    </div>
  );
};

function formatResult(r: unknown): string {
  if (r === null || r === undefined || r === '') return '…';
  if (typeof r === 'number') return String(r);
  if (typeof r === 'string') {
    try {
      const parsed = JSON.parse(r);
      if (parsed && typeof parsed === 'object' && 'result' in (parsed as Record<string, unknown>)) {
        return String((parsed as { result: unknown }).result);
      }
      return r;
    } catch {
      return r;
    }
  }
  if (typeof r === 'object' && r !== null && 'result' in (r as Record<string, unknown>)) {
    return String((r as { result: unknown }).result);
  }
  return JSON.stringify(r);
}

function useParsed<T>(v: unknown): T | null {
  return React.useMemo<T | null>(() => {
    if (v === undefined || v === null || v === '') return null;
    if (typeof v === 'string') {
      try {
        return JSON.parse(v) as T;
      } catch {
        return null;
      }
    }
    return v as T;
  }, [v]);
}
