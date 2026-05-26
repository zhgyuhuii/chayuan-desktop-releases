import * as React from 'react';
import { Loader2, Search, ExternalLink } from 'lucide-react';
import { getPlatform } from '@chayuan/platform-shared';
import type { ToolCallProps } from './registry';

interface SearchHit {
  title?: string;
  url?: string;
  snippet?: string;
  score?: number;
}

interface SearchArgs {
  query?: string;
  q?: string;
  top_k?: number;
}

/**
 * Web 搜索结果卡：
 * - 输入显示 query；
 * - 结果按 hit 列表渲染；点击外链走 PAL.shell.openExternal（Tauri 走系统浏览器）。
 */
export const WebSearchCard: React.FC<ToolCallProps> = ({ name, args, result, status, embedded }) => {
  const a = useParsed<SearchArgs>(args);
  const r = useParsed<SearchHit[] | { results?: SearchHit[]; items?: SearchHit[] }>(result);
  const hits: SearchHit[] = React.useMemo(() => {
    if (Array.isArray(r)) return r;
    if (r && typeof r === 'object') return (r.results ?? r.items ?? []) as SearchHit[];
    return [];
  }, [r]);

  const open = async (url: string) => {
    if (!url) return;
    await getPlatform().shell.openExternal(url);
  };

  const body = (
    <>
      {a?.query || a?.q ? (
        <div className="mb-1 truncate text-[11px] text-muted-foreground">查询: {a?.query ?? a?.q}</div>
      ) : null}
      {hits.length > 0 ? (
        <ul className="space-y-1">
          {hits.slice(0, 8).map((h, i) => (
            <li key={i} className="rounded bg-background/60 p-2">
              <button
                type="button"
                onClick={() => h.url && void open(h.url)}
                className="group flex items-center gap-1 text-sm font-medium text-foreground hover:text-primary"
              >
                <span className="truncate">{h.title || h.url}</span>
                {h.url && <ExternalLink className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100" />}
              </button>
              {h.snippet && <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">{h.snippet}</p>}
            </li>
          ))}
        </ul>
      ) : status === 'end' ? (
        <p className="text-muted-foreground">无结果</p>
      ) : null}
    </>
  );

  if (embedded) return <div>{body}</div>;

  return (
    <div className="rounded-lg border bg-muted/40 px-3 py-2 text-xs">
      <div className="mb-1 flex items-center gap-2">
        {status === 'end' ? <Search className="h-3.5 w-3.5 text-emerald-500" /> : <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />}
        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">{name}</span>
      </div>
      {body}
    </div>
  );
};

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
