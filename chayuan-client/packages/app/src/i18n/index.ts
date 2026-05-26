/**
 * App 层 i18n 兼容壳。
 *
 * 实际实现在 @chayuan/i18n(i18next + ICU + 6 套 locale,迁自 server-frontend, ADR-0002)。
 * 这里 re-export 常用 API,并保留 useT / t() 老签名给历史代码(目前无 caller)。
 * 新代码请用:
 *   import { useTranslation } from '../i18n';
 *   const { t } = useTranslation();
 */

import { t as i18nT, useTranslation as useI18nTranslation } from '@chayuan/i18n';

export {
  useTranslation,
  Trans,
  setLocale,
  getCurrentLocale,
  SUPPORTED_LOCALES,
  initI18n,
  detectInitialLocale,
} from '@chayuan/i18n';
export type { AppLocale } from '@chayuan/i18n';

/** 老接口兼容:useT() → 返回 t 函数 */
export function useT(): (key: string, vars?: Record<string, unknown>) => string {
  const { t } = useI18nTranslation();
  return t as (key: string, vars?: Record<string, unknown>) => string;
}

/** 静态 t(无 hook 上下文,如路由守卫 / 通知) */
export function t(key: string, vars?: Record<string, unknown>): string {
  return i18nT(key, vars);
}
