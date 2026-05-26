/**
 * Scores 趋势 mini-chart：纯 SVG，避免 recharts 重依赖。
 * 数据来自 /admin/lf/scores；按 name 分组计 day-bucket 平均值。
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { http } from '@chayuan/api';

interface ScoresResp {
  data: Array<{ id: string; name: string; value: number; timestamp?: string; traceId?: string }>;
  meta: { totalItems: number };
}

export const ScoreTrend: React.FC = () => {
  const q = useQuery({
    queryKey: ['admin', 'lf', 'scores-trend'],
    queryFn: () => http.get<ScoresResp>('/admin/lf/scores', { query: { limit: 500 } }),
    staleTime: 30_000,
    retry: 0,
  });

  const series = React.useMemo(() => {
    const items = q.data?.data ?? [];
    // 按 name 分组；每组按 day bucket 求平均
    const byName: Record<string, Map<string, { sum: number; n: number }>> = {};
    for (const it of items) {
      const day = (it.timestamp ?? '').slice(0, 10) || new Date().toISOString().slice(0, 10);
      const m = (byName[it.name] ??= new Map());
      const cur = m.get(day) ?? { sum: 0, n: 0 };
      cur.sum += it.value;
      cur.n += 1;
      m.set(day, cur);
    }
    return Object.entries(byName).map(([name, m]) => ({
      name,
      points: [...m.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([day, v]) => ({ day, avg: v.sum / Math.max(1, v.n), n: v.n })),
    }));
  }, [q.data]);

  if (q.isLoading) return <div className="p-4 text-xs text-muted-foreground">加载中…</div>;
  if (!series.length) return <div className="p-4 text-xs text-muted-foreground">暂无 score 数据</div>;

  return (
    <div className="space-y-3 p-4">
      {series.map((s) => (
        <SparkLine key={s.name} title={s.name} points={s.points} />
      ))}
    </div>
  );
};

const SparkLine: React.FC<{ title: string; points: Array<{ day: string; avg: number; n: number }> }> = ({
  title,
  points,
}) => {
  const w = 320;
  const h = 60;
  if (points.length < 2) {
    const v = points[0]?.avg ?? 0;
    return (
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium">{title}</span>
          <span className="text-muted-foreground">单点 {v.toFixed(2)}</span>
        </div>
      </div>
    );
  }
  const ys = points.map((p) => p.avg);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const norm = (v: number) => (max === min ? 0.5 : (v - min) / (max - min));
  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * (w - 8) + 4;
      const y = h - 6 - norm(p.avg) * (h - 12);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const last = points[points.length - 1]!;

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground">最近 {last.avg.toFixed(2)} · 共 {points.reduce((s, p) => s + p.n, 0)} 条</span>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="text-primary">
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>{points[0]!.day}</span>
        <span>{last.day}</span>
      </div>
    </div>
  );
};
