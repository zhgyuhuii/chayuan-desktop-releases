import * as React from 'react';
import { setLocale } from '@chayuan/i18n';
import { useSettingsStore, resolveLocale } from '../../store/settings';

/**
 * 把 settings.locale 同步到 i18next + <html lang>。
 * initI18n 已在 Shell 启动时跑过一次;这里只负责"用户切换"。
 *
 * locale === 'system' 时:
 *   - 当前值取 navigator.language;
 *   - 监听 languagechange 事件,系统语言变了立即跟随。
 */
export const LocaleBridge: React.FC = () => {
  const pref = useSettingsStore((s) => s.locale);

  // 把当前偏好(可能是 'system')应用到 i18next
  React.useEffect(() => {
    void setLocale(resolveLocale(pref));
  }, [pref]);

  // 仅 'system' 模式下挂监听:用户切换 OS / 浏览器语言时实时跟随
  React.useEffect(() => {
    if (pref !== 'system' || typeof window === 'undefined') return undefined;
    const onChange = () => void setLocale(resolveLocale('system'));
    window.addEventListener('languagechange', onChange);
    return () => window.removeEventListener('languagechange', onChange);
  }, [pref]);

  return null;
};
