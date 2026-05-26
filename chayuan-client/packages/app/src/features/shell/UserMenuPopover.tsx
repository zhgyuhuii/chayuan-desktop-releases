/**
 * UserMenuPopover —— 头像下拉菜单(参考图"菜单"页)。
 *
 * 内容:
 *   - 头像 + 昵称 / 未登录 + 签到 / 登录 按钮
 *   - 窗口位置(WindowDock,仅桌面)
 *   - 主题(浅 / 深 / 跟随系统)
 *   - 字号(Slider)
 *   - 设置 / 帮助中心 / 反馈 / 关于
 *
 * 与 SettingsAsPage 重复处:菜单是"快捷面板",仅暴露最常用设置;
 * 复杂选项(后端 API base、telemetry)放设置页。
 *
 * 单机版红线(2026-05-09):移除「能量中心 / 消息中心 / 检测更新」三项 —
 * 它们都是 SaaS 形态遗留(余额、推送、auto-update);单机离线产品里没有
 * 对应的服务端,展示出来反而误导用户。
 */

import { isDesktop } from '@chayuan/platform-shared';
import {
  Avatar,
  Button,
  Pill,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Slider,
  cn,
} from '@chayuan/ui';
import { useNavigate } from '@tanstack/react-router';
import {
  ChevronDown,
  HelpCircle,
  Info,
  MessageCircle,
  Monitor,
  Moon,
  Settings,
  Sun,
} from 'lucide-react';
import * as React from 'react';
import { useTranslation } from '../../i18n';
import { useAuthStore } from '../../store/auth';
import { useLoginModalStore } from '../../store/loginModal';
import { type ThemeMode, useSettingsStore } from '../../store/settings';
import { useTabsStore } from '../../store/tabs';
import { useSingleMachineMode } from '../first-run/useSingleMachineMode';
import { AboutDialog } from '../help/AboutDialog';
import { FeedbackDialog } from '../help/FeedbackDialog';
import { WindowDock } from './WindowDock';

const THEMES: Array<{
  value: ThemeMode;
  icon: React.ComponentType<{ className?: string }>;
  labelKey: string;
}> = [
  { value: 'system', icon: Monitor, labelKey: 'settings.themeSystem' },
  { value: 'light', icon: Sun, labelKey: 'settings.themeLight' },
  { value: 'dark', icon: Moon, labelKey: 'settings.themeDark' },
];

export const UserMenuPopover: React.FC = () => {
  const { t } = useTranslation();
  const auth = useAuthStore();
  const settings = useSettingsStore();
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();
  const showLogin = useLoginModalStore((s) => s.show);
  const [popOpen, setPopOpen] = React.useState(false);
  const [aboutOpen, setAboutOpen] = React.useState(false);
  const [feedbackOpen, setFeedbackOpen] = React.useState(false);

  const username = auth.user?.username;
  const singleMachine = useSingleMachineMode();
  const openSettings = () => {
    open('/settings', { title: 'nav.settings', icon: 'settings' });
    void navigate({ to: '/settings' as never });
    setPopOpen(false);
  };
  const openHelp = () => {
    open('/help', { title: 'settings.helpCenter', icon: 'help-circle' });
    void navigate({ to: '/help' as never });
    setPopOpen(false);
  };
  const openFeedback = () => {
    setPopOpen(false);
    setFeedbackOpen(true);
  };
  const openAbout = () => {
    setPopOpen(false);
    setAboutOpen(true);
  };

  return (
    <Popover open={popOpen} onOpenChange={setPopOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            'inline-flex items-center gap-1 rounded-full p-1 transition-colors',
            'hover:bg-[var(--cy-surface-2)]',
          )}
          aria-label="user menu"
        >
          <Avatar name={username} size={28} />
          <ChevronDown className="h-3 w-3 text-[var(--cy-text-tertiary)]" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="w-80">
        {/* Profile row.
              单机模式(``singleMachine``):隐藏「登录 / 退出」按钮 + 把"未登录"
              文案换成"本地用户",符合 CLAUDE.md §3.4 的红线(不暴露登录概念)。 */}
        <div className="flex items-center gap-3 rounded-xl bg-[var(--cy-surface-1)] p-3">
          <Avatar name={singleMachine ? settings.displayName || '本' : username} size={36} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-[var(--cy-text-primary)]">
              {singleMachine
                ? settings.displayName || '本地用户'
                : (username ?? t('settings.notLoggedIn'))}
            </p>
            {!singleMachine && !auth.user && (
              <p className="truncate text-xs text-[var(--cy-text-tertiary)]">
                {t('settings.loginHint')}
              </p>
            )}
            {singleMachine && (
              <p className="truncate text-xs text-[var(--cy-text-tertiary)]">数据保存在本机</p>
            )}
          </div>
          {!singleMachine &&
            (auth.user ? (
              <Button size="sm" variant="ghost" onClick={() => void auth.logout()}>
                {t('auth.logout')}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => {
                  setPopOpen(false);
                  showLogin();
                }}
              >
                {t('auth.login')}
              </Button>
            ))}
        </div>

        {/* 窗口位置(仅桌面) */}
        {isDesktop() && (
          <Section label={t('settings.windowLayout')}>
            <WindowDock />
          </Section>
        )}

        {/* 主题 */}
        <Section label={t('settings.theme')}>
          <div className="flex gap-1">
            {THEMES.map((th) => {
              const Icon = th.icon;
              const active = settings.theme === th.value;
              return (
                <Pill
                  key={th.value}
                  size="sm"
                  tone={active ? 'ink' : 'outline'}
                  active={active}
                  onClick={() => settings.setTheme(th.value)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t(th.labelKey)}
                </Pill>
              );
            })}
          </div>
        </Section>

        {/* 字号 */}
        <Section label={`${t('settings.fontSize')} (${settings.fontSize}px)`}>
          <Slider
            min={12}
            max={22}
            step={1}
            value={[settings.fontSize]}
            onValueChange={([v]) => v !== undefined && settings.setFontSize(v)}
          />
        </Section>

        {/* 菜单项 */}
        <div className="mt-2 border-t border-[var(--cy-border-subtle)] pt-2">
          <MenuItem icon={Settings} label={t('nav.settings')} onClick={openSettings} />
          <MenuItem icon={HelpCircle} label={t('settings.helpCenter')} onClick={openHelp} />
          <MenuItem icon={MessageCircle} label={t('settings.feedback')} onClick={openFeedback} />
          <MenuItem icon={Info} label={t('settings.about')} onClick={openAbout} />
        </div>
      </PopoverContent>
      <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
      <FeedbackDialog open={feedbackOpen} onOpenChange={setFeedbackOpen} />
    </Popover>
  );
};

const Section: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="mt-3 px-2">
    <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
      {label}
    </p>
    {children}
  </div>
);

const MenuItem: React.FC<{
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
  trailing?: React.ReactNode;
}> = ({ icon: Icon, label, onClick, trailing }) => (
  <button
    type="button"
    onClick={onClick}
    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-[var(--cy-text-primary)] transition-colors hover:bg-[var(--cy-surface-2)]"
  >
    <Icon className="h-4 w-4 text-[var(--cy-text-secondary)]" />
    <span className="flex-1 text-left">{label}</span>
    {trailing}
  </button>
);
