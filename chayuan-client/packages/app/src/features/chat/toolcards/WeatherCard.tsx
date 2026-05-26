import * as React from 'react';
import { Cloud, Loader2 } from 'lucide-react';
import type { ToolCallProps } from './registry';

interface WeatherArgs { city?: string; location?: string }
interface WeatherResult {
  city?: string;
  weather?: string;
  temperature?: string | number;
  windDirection?: string;
  wind_direction?: string;
  windPower?: string;
  wind_power?: string;
  humidity?: string | number;
  reportTime?: string;
  report_time?: string;
}

export const WeatherCard: React.FC<ToolCallProps> = ({ name, args, result, status, embedded }) => {
  const a = useParsed<WeatherArgs>(args);
  const r = useParsed<WeatherResult | WeatherResult[]>(result);
  const list: WeatherResult[] = Array.isArray(r) ? r : r ? [r] : [];
  const city = list[0]?.city ?? a?.city ?? a?.location ?? '';

  const body =
    list.length > 0 ? (
      <ul className="space-y-1">
        {list.slice(0, 5).map((w, i) => (
          <li key={i} className="flex items-center justify-between gap-3 rounded bg-background/60 px-2 py-1">
            <div>
              <p className="font-medium">{w.weather ?? '—'}</p>
              <p className="text-[11px] text-muted-foreground">
                {(w.windDirection ?? w.wind_direction) ?? ''} {(w.windPower ?? w.wind_power) ?? ''} · 湿度 {w.humidity ?? '—'}%
              </p>
            </div>
            <div className="text-2xl font-bold text-sky-600">{w.temperature ?? '—'}°</div>
          </li>
        ))}
      </ul>
    ) : status === 'end' ? (
      <p className="text-muted-foreground">无天气数据</p>
    ) : null;

  if (embedded) {
    return (
      <div>
        {city ? <div className="mb-1 text-[11px] text-muted-foreground">城市: {city}</div> : null}
        {body}
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-gradient-to-br from-sky-50 to-blue-100 px-3 py-2 text-xs dark:from-sky-950 dark:to-blue-950">
      <div className="mb-1 flex items-center gap-2">
        {status === 'end' ? <Cloud className="h-3.5 w-3.5 text-sky-500" /> : <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-500" />}
        <span className="font-medium">{name}</span>
        {city && <span className="text-muted-foreground">· {city}</span>}
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
