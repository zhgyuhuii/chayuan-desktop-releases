import * as React from 'react';
import { getPlatform } from '@chayuan/platform-shared';
import { useSettingsStore } from '../../store/settings';

/**
 * 把 settings.theme 映射到 <html data-theme=...> 与 .dark 类。
 * 监听 PAL.window.onThemeChange，与系统主题同步。
 */
export const ThemeBridge: React.FC = () => {
  const theme = useSettingsStore((s) => s.theme);
  const fontSize = useSettingsStore((s) => s.fontSize);

  const apply = React.useCallback((dark: boolean) => {
    const el = document.documentElement;
    el.setAttribute('data-theme', dark ? 'dark' : 'light');
    el.classList.toggle('dark', dark);
  }, []);

  React.useEffect(() => {
    const win = getPlatform().window;
    if (theme === 'system') {
      apply(win.isDarkSystem());
      return win.onThemeChange(apply);
    }
    apply(theme === 'dark');
    return undefined;
  }, [apply, theme]);

  React.useEffect(() => {
    document.documentElement.style.fontSize = `${fontSize}px`;
  }, [fontSize]);

  return null;
};
