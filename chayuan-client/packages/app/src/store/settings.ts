/**
 * 应用级设置(与单条对话无关的偏好)。
 *
 * - 主题:light / dark / system;启动后由 ThemeBridge 应用到 <html data-theme=...>
 * - 字号:12-22 范围,直接写入 documentElement.style.fontSize 接管 root rem
 * - API base 覆盖:用户可临时切换连接的后端
 * - 上传遥测:关闭后 observability 不再发 Langfuse;本地仍保留 outbox
 * - locale:zh-CN / en / ja / ko / de / fr;首次默认由 @chayuan/i18n 的 detectInitialLocale() 决定;
 *          切换由 LocaleBridge 同步到 i18next + <html lang>
 * - windowDock:left / center / right(参考图"窗口位置";由 platform-tauri 在 M1 接通)
 */

import { type AppLocale, detectInitialLocale } from '@chayuan/i18n';
import { getPlatform } from '@chayuan/platform-shared';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export type { AppLocale } from '@chayuan/i18n';

export type ThemeMode = 'light' | 'dark' | 'system';
export type WindowDock = 'left' | 'center' | 'right';
/** 'system' 表示跟随 navigator.language;具体语言由 LocaleBridge 在运行时解析 */
export type LocalePreference = AppLocale | 'system';

interface SettingsState {
  theme: ThemeMode;
  fontSize: number;
  apiBaseOverride: string;
  telemetry: boolean;
  locale: LocalePreference;
  windowDock: WindowDock;
  /** 单机版的「本地用户」显示名;UserMenu 头像 + 设置页「名称设置」共用 */
  displayName: string;
  /** 是否在主界面底部显示状态栏(版权 + 6 个 capability 健康灯);默认 true */
  showAppFooter: boolean;

  setTheme(t: ThemeMode): void;
  setFontSize(n: number): void;
  setApiBaseOverride(v: string): void;
  setTelemetry(b: boolean): void;
  setLocale(l: LocalePreference): void;
  setWindowDock(d: WindowDock): void;
  setDisplayName(name: string): void;
  setShowAppFooter(b: boolean): void;
  reset(): void;
}

const FONT_MIN = 12;
const FONT_MAX = 22;
// 17px 是各显示密度下中英文混排都最舒服的折衷值,2026-05-09 用户体验调整。
const FONT_DEFAULT = 17;

const DEFAULTS: Pick<
  SettingsState,
  | 'theme'
  | 'fontSize'
  | 'apiBaseOverride'
  | 'telemetry'
  | 'locale'
  | 'windowDock'
  | 'displayName'
  | 'showAppFooter'
> = {
  theme: 'system',
  fontSize: FONT_DEFAULT,
  apiBaseOverride: '',
  telemetry: true,
  // 默认跟随系统;LocaleBridge 在运行时解析为具体 AppLocale
  locale: 'system',
  windowDock: 'center',
  displayName: '',
  showAppFooter: true,
};

/**
 * 把 LocalePreference 解析为具体 AppLocale。
 * - 'system' → 优先 PAL.runtime.systemLocale(Tauri 走 plugin-os.locale,
 *   解决 WebView2 navigator.language 恒为 en-US 的问题);否则 navigator.language
 * - 具体码 → 原样返回
 *
 * 在 SSR / 测试无 window 环境下,'system' 兜底到 'zh-CN'。
 */
export function resolveLocale(pref: LocalePreference): AppLocale {
  if (pref !== 'system') return pref;
  // Tauri 平台:WebView2 的 navigator.language 不可靠,用 plugin-os 拿真实系统语言
  try {
    const sys = getPlatform().runtime.systemLocale;
    if (sys) return mapBcp47(sys);
  } catch {
    /* platform 还没 setPlatform — 走 navigator 兜底 */
  }
  if (typeof window === 'undefined') return 'zh-CN';
  return detectInitialLocale();
}

function mapBcp47(lang: string): AppLocale {
  const l = lang.toLowerCase();
  if (l.startsWith('en')) return 'en';
  if (l.startsWith('ja')) return 'ja';
  if (l.startsWith('ko')) return 'ko';
  if (l.startsWith('de')) return 'de';
  if (l.startsWith('fr')) return 'fr';
  // 兜底中文:plugin-os 拿不到 / 拿到陌生语种时不要默默掉到英文。
  return 'zh-CN';
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      setTheme: (theme) => set({ theme }),
      setFontSize: (fontSize) => set({ fontSize: clamp(fontSize, FONT_MIN, FONT_MAX) }),
      setApiBaseOverride: (apiBaseOverride) => set({ apiBaseOverride }),
      setTelemetry: (telemetry) => set({ telemetry }),
      setLocale: (locale) => set({ locale }),
      setWindowDock: (windowDock) => set({ windowDock }),
      setDisplayName: (displayName) => set({ displayName: displayName.slice(0, 30) }),
      setShowAppFooter: (showAppFooter) => set({ showAppFooter }),
      reset: () => set(DEFAULTS),
    }),
    {
      name: 'cy.settings',
      storage: createJSONStorage(() => localStorage),
      version: 6,
      migrate: (state, fromVersion) => {
        if (state && typeof state === 'object') {
          const s = state as {
            locale?: string;
            windowDock?: WindowDock;
            marketplaceVerifyRequired?: boolean;
            fontSize?: number;
          };
          // v1 → v2: 老版 locale 'en-US' → 'en';补 windowDock 默认
          if (fromVersion < 2) {
            if (s.locale === 'en-US') s.locale = 'en';
            if (!s.windowDock) s.windowDock = 'center';
          }
          // v2 → v3: 引入 'system' 选项;首次升级用户大多没主动改过语言,
          // 先保留其原值,只在 locale 缺失或不识别时回 'system'。
          if (fromVersion < 3) {
            const valid: ReadonlyArray<string> = ['system', 'zh-CN', 'en', 'ja', 'ko', 'de', 'fr'];
            if (!s.locale || !valid.includes(s.locale)) s.locale = 'system';
          }
          // v4 → v5: 单机版重构后移除 marketplaceVerifyRequired 字段(不再有登录系统)。
          if (fromVersion < 5) {
            delete s.marketplaceVerifyRequired;
          }
          // v5 → v6: 默认字号从 16 → 17。仅当用户值正好停在 v5 默认 16 时升级;
          // 用户主动调过(<>16)的值原样保留。
          if (fromVersion < 6) {
            if (s.fontSize === 16) s.fontSize = 17;
          }
        }
        return state as SettingsState;
      },
    },
  ),
);

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
