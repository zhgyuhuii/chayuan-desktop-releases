/**
 * 流式可视化指标：TTFT / token rate / 累计时长。
 *
 * 设计：
 * - 由 ChatPage 直接 props 传当前 streaming message 的内容和起止时间；
 * - 内部用 ref 累积 token 数与最近 1 秒滑窗速率；
 * - 性能：100ms 节流刷新，不每个 token 重渲。
 */

import * as React from 'react';
import { Activity } from 'lucide-react';

export interface StreamMetricsProps {
  active: boolean;
  /** 流式中的 assistant 当前文本，仅用来推 token 数（rough 按字符 / 4） */
  text: string;
  /** 流式开始时间戳 ms；active=true → false 边沿后保留最近一次显示 */
  startedAt: number | null;
  /** 第一 token 时间戳；用于 TTFT */
  firstTokenAt: number | null;
}

export const StreamMetrics: React.FC<StreamMetricsProps> = ({ active, text, startedAt, firstTokenAt }) => {
  const [, force] = React.useReducer((x: number) => x + 1, 0);
  React.useEffect(() => {
    if (!active) return;
    const t = setInterval(force, 250);
    return () => clearInterval(t);
  }, [active]);

  if (!active && !startedAt) return null;
  const now = Date.now();
  const elapsed = startedAt ? Math.max(0, (firstTokenAt ?? now) - startedAt) : 0;
  const tokens = Math.ceil((text?.length ?? 0) / 4);
  const sinceFirst = firstTokenAt ? Math.max(0.001, (now - firstTokenAt) / 1000) : 0;
  const rate = sinceFirst > 0 ? Math.round(tokens / sinceFirst) : 0;

  return (
    <span className="flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
      <Activity className={`h-3 w-3 ${active ? 'animate-pulse text-primary' : ''}`} />
      {firstTokenAt && <span>TTFT {elapsed}ms</span>}
      {rate > 0 && <span>· {rate} tok/s</span>}
      {tokens > 0 && <span>· {tokens} tok</span>}
    </span>
  );
};
