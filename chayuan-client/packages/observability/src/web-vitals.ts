/**
 * Web Vitals → Langfuse score(按指标名分桶)。
 *
 * - LCP / CLS / INP / TTFB / FCP;
 * - 每条 metric 起一个 short trace 写 score;用 Langfuse 看面板做 P75 分布。
 *
 * 实现说明:
 *   web-vitals 是可选 runtime;packages.json 不强制依赖,
 *   未安装时静默 noop(import 失败被 catch),不影响构建。
 *   类型用本地 minimal Metric 接口,避免依赖 @types/web-vitals。
 */

import { logScore } from './langfuse';

interface Metric {
  name: 'LCP' | 'INP' | 'CLS' | 'TTFB' | 'FCP' | string;
  value: number;
  id: string;
}

interface WebVitalsModule {
  onLCP: (cb: (m: Metric) => void) => void;
  onINP: (cb: (m: Metric) => void) => void;
  onCLS: (cb: (m: Metric) => void) => void;
  onTTFB: (cb: (m: Metric) => void) => void;
  onFCP: (cb: (m: Metric) => void) => void;
}

const RUM_TRACE = (() => {
  if (typeof crypto !== 'undefined' && (crypto as Crypto).randomUUID)
    return (crypto as Crypto).randomUUID();
  return `rum-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
})();

let started = false;

export async function startWebVitals(): Promise<void> {
  if (started) return;
  started = true;
  try {
    // 拼成运行时字符串绕过 Vite/Rollup 的静态依赖解析;
    // 仅 `as unknown as string` 类型断言会被 esbuild 擦掉,
    // Rollup 仍按字面量去解析,导致未安装时构建失败。
    const modName = 'web-' + 'vitals';
    const mod = (await import(/* @vite-ignore */ modName).catch(
      () => null,
    )) as WebVitalsModule | null;
    if (!mod) return;
    const report = (m: Metric) =>
      void logScore({
        traceId: RUM_TRACE,
        name: `vitals.${m.name.toLowerCase()}`,
        value: m.value,
        comment: m.id,
      }).catch(() => undefined);
    mod.onLCP(report);
    mod.onINP(report);
    mod.onCLS(report);
    mod.onTTFB(report);
    mod.onFCP(report);
  } catch {
    /* noop */
  }
}
