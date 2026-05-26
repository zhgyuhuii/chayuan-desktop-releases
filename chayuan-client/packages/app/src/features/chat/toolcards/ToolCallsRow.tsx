/**
 * ToolCallsRow — 工具/MCP 调用聚合展示。
 *
 * 视觉与 KbResultsView 同形(border-l-2 + chevron + 顶部 summary 一行),
 * 让用户在一条助手消息里看到「本次调用了 N 个工具」一条线,展开看每个工具,
 * 再点开看每个工具的入参与结果详情。
 *
 * 三层折叠:
 *   ┌ 顶部条 (chevron)        ── 整组 toolCalls 入口,默认折叠
 *   │
 *   ├─ ToolCallEntry (chevron) ── 每一次调用,默认折叠
 *   │   ┌ 状态图标 + 工具名 + 简短预览(query/city/expression/...)
 *   │   │
 *   │   └─ embedded 工具卡       ── 入参 + 结果(具体卡按 tool 名分流)
 *
 * 状态图标:
 *   - start/delta → 旋转 Loader2(蓝)
 *   - end + 结果含 error 字段 → AlertCircle(红)
 *   - end + 正常 → CheckCircle2(绿)
 */

import { cn } from '@chayuan/ui';
import { AlertCircle, CheckCircle2, ChevronDown, Loader2, Wrench } from 'lucide-react';
import * as React from 'react';
import type { ChatToolCall } from '../useChayuanChat';
import { ToolCallCard } from './registry';

export interface ToolCallsRowProps {
  toolCalls: ChatToolCall[];
  /** 助手消息是否仍在流式;true 时顶部追加「调用中…」 */
  streaming?: boolean;
  className?: string;
}

export const ToolCallsRow: React.FC<ToolCallsRowProps> = ({
  toolCalls,
  streaming = false,
  className,
}) => {
  const [open, setOpen] = React.useState(false);
  if (!toolCalls?.length) return null;

  const totalCount = toolCalls.length;
  const runningCount = toolCalls.filter((t) => t.status === 'start' || t.status === 'delta').length;
  const finishedCount = toolCalls.filter((t) => t.status === 'end').length;
  const failedCount = toolCalls.filter((t) => isErrorResult(t.result)).length;

  return (
    <div
      className={cn(
        'w-full max-w-full overflow-hidden border-l-2 border-amber-300 bg-transparent',
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-2 py-1 text-left hover:bg-[var(--cy-bg-hover)]"
      >
        <span className="inline-flex min-w-0 flex-1 items-center gap-1.5">
          <span className="inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600">
            <Wrench className="h-3 w-3" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--cy-text-tertiary)]">
            <span className="font-medium text-[var(--cy-text-secondary)]">本次调用工具</span>
            <span className="mx-1">·</span>
            {totalCount} 个{finishedCount ? <> · 完成 {finishedCount}</> : null}
            {runningCount ? <> · 调用中 {runningCount}</> : null}
            {failedCount ? <> · 失败 {failedCount}</> : null}
            {streaming && runningCount ? '…' : ''}
          </span>
        </span>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 flex-shrink-0 text-[var(--cy-text-tertiary)] transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>
      {open ? (
        <div className="space-y-1.5 border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 py-2">
          {toolCalls.map((tc) => (
            <ToolCallEntry key={tc.id} tc={tc} />
          ))}
        </div>
      ) : null}
    </div>
  );
};

const ToolCallEntry: React.FC<{ tc: ChatToolCall }> = ({ tc }) => {
  const [open, setOpen] = React.useState(false);
  const name = tc.name ?? 'unknown';
  const isFailed = isErrorResult(tc.result);
  const isRunning = tc.status === 'start' || tc.status === 'delta';
  const preview = oneLinePreview(tc);

  return (
    <div className="rounded-lg border border-[var(--cy-border-subtle)] bg-background/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[11px] hover:bg-[var(--cy-bg-hover)]"
      >
        {isRunning ? (
          <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin text-primary" />
        ) : isFailed ? (
          <AlertCircle className="h-3.5 w-3.5 flex-shrink-0 text-rose-500" />
        ) : tc.status === 'end' ? (
          <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0 text-emerald-500" />
        ) : (
          <Wrench className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        )}
        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">{name}</span>
        {preview ? (
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{preview}</span>
        ) : (
          <span className="flex-1 text-muted-foreground">
            {isRunning ? '调用中…' : tc.status === 'end' ? '已完成' : ''}
          </span>
        )}
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 flex-shrink-0 text-muted-foreground transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>
      {open ? (
        <div className="border-t border-[var(--cy-border-subtle)] px-2 py-2 text-xs">
          <ToolCallCard
            name={name}
            args={tc.arguments}
            result={tc.result}
            status={tc.status}
            embedded
          />
        </div>
      ) : null}
    </div>
  );
};

/** 结果里若 stringify 后能找出 error/失败/异常 字样,认为本次调用失败。 */
function isErrorResult(r: unknown): boolean {
  if (r === null || r === undefined) return false;
  if (typeof r === 'string') return /\b(error|失败|异常|exception)\b/i.test(r);
  if (typeof r === 'object') {
    try {
      const s = JSON.stringify(r);
      return /"error"\s*:/i.test(s) || /"errcode"\s*:\s*[1-9]/.test(s);
    } catch {
      return false;
    }
  }
  return false;
}

/** 折叠头一行预览 — 从 args / result 提取最显眼的字段。 */
function oneLinePreview(tc: ChatToolCall): string {
  const a = parseLoose(tc.arguments);
  if (a && typeof a === 'object') {
    const o = a as Record<string, unknown>;
    const candidates = [
      o.query,
      o.q,
      o.text,
      o.expression,
      o.expr,
      o.city,
      o.location,
      o.url,
      o.prompt,
      o.keyword,
      o.keywords,
    ];
    for (const c of candidates) {
      if (typeof c === 'string' && c.trim()) return truncate(c.trim(), 80);
    }
  }
  if (typeof tc.arguments === 'string' && tc.arguments.trim() && !looksLikeJson(tc.arguments)) {
    return truncate(tc.arguments.trim(), 80);
  }
  return '';
}

function parseLoose(v: unknown): unknown {
  if (v === undefined || v === null || v === '') return null;
  if (typeof v === 'string') {
    try {
      return JSON.parse(v);
    } catch {
      return null;
    }
  }
  return v;
}

function looksLikeJson(s: string): boolean {
  const t = s.trim();
  return (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'));
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
