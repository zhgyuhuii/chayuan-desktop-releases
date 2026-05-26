/**
 * 察元 i18n —— i18next + ICU + react-i18next。
 *
 * 设计:
 *   - 6 套内置 locale(zh-CN / en / ja / ko / fr / de),迁自 chayuan-server-frontend(ADR-02)。
 *   - ICU 占位语法:`{model}` `{count, plural, one {# item} other {# items}}`。
 *   - 启动时 initI18n({ locale }):同步初始化(无网络),立即可用。
 *   - 运行时切换 locale:setLocale(code),同时写入 <html lang>。
 *   - useTranslation() 由 react-i18next 提供,等价于 useT。
 *
 * 与设置存储解耦:具体的 locale 持久化 / 系统语言探测在 app 层,
 * i18n 包只关心"给我一个 locale,我返回 t()"。
 */

import i18next, { type i18n, type InitOptions } from 'i18next';
import ICU from 'i18next-icu';
import { initReactI18next } from 'react-i18next';

import zhCN from './locales/zh-CN';
import en from './locales/en';
import ja from './locales/ja';
import ko from './locales/ko';
import de from './locales/de';
import fr from './locales/fr';

export type AppLocale = 'zh-CN' | 'en' | 'ja' | 'ko' | 'de' | 'fr';

export const SUPPORTED_LOCALES: ReadonlyArray<{
  code: AppLocale;
  label: string;
  /** Intl 格式化用 */
  bcp47: string;
}> = [
  { code: 'zh-CN', label: '中文(简体)', bcp47: 'zh-CN' },
  { code: 'en', label: 'English', bcp47: 'en-US' },
  { code: 'ja', label: '日本語', bcp47: 'ja-JP' },
  { code: 'ko', label: '한국어', bcp47: 'ko-KR' },
  { code: 'de', label: 'Deutsch', bcp47: 'de-DE' },
  { code: 'fr', label: 'Français', bcp47: 'fr-FR' },
];

const RESOURCES = {
  'zh-CN': { translation: zhCN as Record<string, unknown> },
  en: { translation: en as Record<string, unknown> },
  ja: { translation: ja as Record<string, unknown> },
  ko: { translation: ko as Record<string, unknown> },
  de: { translation: de as Record<string, unknown> },
  fr: { translation: fr as Record<string, unknown> },
} as const;

let initialized = false;

export interface InitI18nOptions {
  /** 初始 locale;不传则按 detectInitialLocale() 选择 */
  locale?: AppLocale;
  /** 关闭 console missing 警告(测试环境用) */
  silent?: boolean;
}

/**
 * 从 navigator.language / localStorage 推导初始 locale。
 *
 * 默认回退到 `zh-CN`(察元主用户语种);只在浏览器明确给出 en/ja/ko/de/fr
 * 前缀时才落到对应语言。避免 Tauri WebView2 在 navigator.language 拿到
 * 'en-US' 兜底值时把中国用户挤到英文界面。
 */
export function detectInitialLocale(): AppLocale {
  if (typeof window === 'undefined') return 'zh-CN';
  const stored = window.localStorage.getItem('cy.locale') as AppLocale | null;
  if (stored && RESOURCES[stored]) return stored;
  const lang = window.navigator.language?.toLowerCase() ?? 'zh-cn';
  if (lang.startsWith('en')) return 'en';
  if (lang.startsWith('ja')) return 'ja';
  if (lang.startsWith('ko')) return 'ko';
  if (lang.startsWith('de')) return 'de';
  if (lang.startsWith('fr')) return 'fr';
  return 'zh-CN';
}

/**
 * 同步初始化(useSuspense=false,启动时不阻塞);
 * 必须在 <RouterProvider> 之前调用一次。
 */
export async function initI18n(opts: InitI18nOptions = {}): Promise<i18n> {
  if (initialized) {
    if (opts.locale && i18next.language !== opts.locale) {
      await i18next.changeLanguage(opts.locale);
    }
    return i18next;
  }

  const lng = opts.locale ?? detectInitialLocale();

  const initOptions: InitOptions = {
    lng,
    fallbackLng: 'zh-CN',
    supportedLngs: SUPPORTED_LOCALES.map((l) => l.code),
    resources: RESOURCES,
    interpolation: { escapeValue: false }, // ICU 接管,无 XSS 风险
    react: { useSuspense: false },
    returnNull: false,
    saveMissing: false,
  };
  if (opts.silent) {
    initOptions.missingKeyHandler = () => {};
  }

  await i18next.use(ICU as never).use(initReactI18next).init(initOptions);

  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', lng);
  }

  initialized = true;
  return i18next;
}

/** 切换 locale + 持久化 + 同步 <html lang> */
export async function setLocale(code: AppLocale): Promise<void> {
  if (!initialized) await initI18n({ locale: code });
  else await i18next.changeLanguage(code);
  if (typeof window !== 'undefined') {
    window.localStorage.setItem('cy.locale', code);
  }
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', code);
  }
}

export function getCurrentLocale(): AppLocale {
  return (i18next.language as AppLocale) ?? 'zh-CN';
}

/** 静态调用(路由守卫 / 通知 / 不能用 hook 的地方);initI18n 之前回退到 key */
export function t(key: string, vars?: Record<string, unknown>): string {
  if (!initialized) return key;
  return i18next.t(key, vars) as string;
}

export { i18next };
export { useTranslation, Trans } from 'react-i18next';
