/**
 * Sentry 集成（可选）。
 *
 * - 仅当 VITE_SENTRY_DSN 配置且 settings.telemetry=true 时初始化。
 * - 与 Langfuse 共用 trace_id：beforeSend 把 trace_id 加入 tags，便于互链。
 * - 失败软降级：Sentry 加载失败完全 noop。
 */

let inited = false;
let sentry: any = null;

export interface SentryConfig {
  enabled: boolean;
  dsn: string;
  release: string;
  env: 'dev' | 'staging' | 'prod';
}

export async function initSentry(cfg: SentryConfig): Promise<void> {
  if (inited || !cfg.enabled || !cfg.dsn) return;
  inited = true;
  try {
    const modName = '@sentry' + '/react';
    const mod = (await import(/* @vite-ignore */ modName).catch(() => null)) as any;
    if (!mod) return;
    sentry = mod;
    mod.init({
      dsn: cfg.dsn,
      release: cfg.release,
      environment: cfg.env,
      integrations: [mod.browserTracingIntegration?.()].filter(Boolean),
      tracesSampleRate: cfg.env === 'prod' ? 0.1 : 1.0,
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: cfg.env === 'prod' ? 0.1 : 0,
    });
  } catch {
    /* 软降级 */
  }
}

export function captureError(err: unknown, context?: { traceId?: string; extra?: Record<string, unknown> }): void {
  if (!sentry) return;
  try {
    sentry.captureException(err, {
      tags: context?.traceId ? { trace_id: context.traceId } : undefined,
      extra: context?.extra,
    });
  } catch {
    /* noop */
  }
}

export function setSentryUser(id?: string): void {
  if (!sentry) return;
  try {
    sentry.setUser(id ? { id } : null);
  } catch {
    /* noop */
  }
}
