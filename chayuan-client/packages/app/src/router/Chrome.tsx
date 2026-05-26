/**
 * Chrome —— 多 Tab 工作区外壳。
 *
 * 布局(Block A 后):
 *   ┌──────────┬───────────────────────────────────────────┐
 *   │          │  TopStrip(右上:登录/UserMenu/⛶/窗口键)  │
 *   │ Sidebar  ├───────────────────────────────────────────┤
 *   │ 上下     │  TabBar(仅 Main 区上方)                  │
 *   │ 占满     ├───────────────────────────────────────────┤
 *   │          │  Main (active Tab content)                │
 *   └──────────┴───────────────────────────────────────────┘
 *
 * 关键变化:
 *   - 之前是"顶栏全宽,下面 Sidebar | Main"。现在改为"Sidebar 全高 | 右栏(顶+主)",
 *     视觉上 Sidebar 是一条独立的纵向品牌区。
 *   - data-shell 标记沿用 (cy-embed-mode / cy-fullscreen 复用同一钩子)。
 *   - 全屏由 useFullscreenWorkspace 接管,F11 + 右上 ⛶ 触发。
 *   - 跨窗同步(独立窗 ↔ 主窗)由 useCrossWindowSync 接管。
 *
 * URL ↔ tabsStore 的同步在 TabHost 内部;Chrome 只关心布局。
 */

import * as React from 'react';
import { AppFooter } from '../features/shell/AppFooter';
import { Sidebar } from '../features/shell/Sidebar';
import { TabBar } from '../features/shell/TabBar';
import { TabHost } from '../features/shell/TabHost';
import { UserMenuPopover } from '../features/shell/UserMenuPopover';
// Tauri 2 配置 ``decorations: true`` 已经走 OS 原生 min/max/close 标题栏,
// 自定义 WindowControls 重复了。FullscreenButton 单机版用户场景用不到,移除。
// import { WindowControls } from '../features/shell/WindowControls';
// import { FullscreenButton } from '../features/shell/FullscreenButton';
import { useFullscreenWorkspace } from '../features/shell/useFullscreenWorkspace';
import { isSingleMachineMode } from '../features/first-run/useSingleMachineMode';
import { useSettingsStore } from '../store/settings';
import { useCrossWindowSync } from '../features/shell/useCrossWindowSync';
import { useGlobalDetachedLaneSync } from '../features/model-arena/DetachedLaneRoute';
import { DetachedToolbar } from '../features/shell/DetachedToolbar';
import { ArtifactPanel } from '../features/artifact/ArtifactPanel';
import { CommandPaletteRoot } from '../features/cmdk/CommandPalette';
import { useDesktopIntegrations } from '../features/desktop/useDesktopIntegrations';
import { ErrorDialogRoot } from '../features/shell/ErrorDialog';
import { ImageEmbedderInstallDialog } from '../features/aiPlatform/ImageEmbedderInstallDialog';
import { useAuthStore } from '../store/auth';
import { useLoginModalStore } from '../store/loginModal';
import { useTranslation } from '../i18n';
import { cn } from '@chayuan/ui';

export const Chrome: React.FC = () => {
  const [collapsed, setCollapsed] = React.useState(false);
  useDesktopIntegrations();
  useFullscreenWorkspace();
  useCrossWindowSync();
  // Chrome 是 Router Outlet 的父容器,挂这里 = 整个 app 生命周期都监听 lane:reattach
  useGlobalDetachedLaneSync();
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn);
  const showLogin = useLoginModalStore((s) => s.show);
  const showAppFooter = useSettingsStore((s) => s.showAppFooter);
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        'grid h-screen w-screen overflow-hidden bg-[var(--cy-surface-2)] text-[var(--cy-text-primary)]',
        // Sidebar 列宽随折叠态变化;右列自适应
        collapsed ? 'grid-cols-[64px_1fr]' : 'grid-cols-[256px_1fr]',
      )}
    >
      {/* 左:Sidebar 上下占满 */}
      <Sidebar
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((v) => !v)}
        data-shell="sidebar"
      />

      {/* 右:TabBar + Main + 底部状态栏(全局,可在设置中关闭) */}
      <div
        className={cn(
          'grid min-h-0 min-w-0',
          showAppFooter ? 'grid-rows-[40px_1fr_28px]' : 'grid-rows-[40px_1fr]',
        )}
        data-shell="main-column"
      >
        {/* 顶部标签条:紧贴左侧菜单面板起始,右侧保留账号/窗口按钮 */}
        <header
          className={cn(
            'flex min-w-0 items-stretch gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] [-webkit-app-region:drag]',
          )}
          data-shell="topbar"
        >
          <div className="min-w-0 flex-1 [-webkit-app-region:no-drag]">
            <TabBar />
          </div>
          <div className="flex items-center gap-1 px-3 [-webkit-app-region:no-drag]">
            {/* 单机版:无登录,直接展示 UserMenuPopover(后端注入 LocalUser);
                非单机版保留旧行为 —— 已登录用 UserMenu,未登录显示登录按钮。 */}
            {isSingleMachineMode() ? (
              <UserMenuPopover />
            ) : isLoggedIn ? (
              <UserMenuPopover />
            ) : (
              <button
                type="button"
                onClick={showLogin}
                className={cn(
                  'inline-flex h-7 items-center rounded-full px-4 text-xs font-medium',
                  'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)]',
                  'transition-opacity hover:opacity-90',
                )}
              >
                {t('auth.login')}
              </button>
            )}
          </div>
          {/* 自定义 min/max/close 已删 —— Tauri ``decorations: true`` 用 OS 原生标题栏 */}
        </header>

        {/* 主区 */}
        <main
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[var(--cy-surface-base)]"
          data-shell="main"
        >
          <TabHost />
        </main>

        {/* 底部全局状态栏:版权 + 6 个 capability 健康灯;可在设置中隐藏 */}
        {showAppFooter && <AppFooter />}
      </div>

      {/* 全局浮层 — 不参与 grid 布局 */}
      <ArtifactPanel />
      <CommandPaletteRoot />
      <ErrorDialogRoot />
      <DetachedToolbar />
      <ImageEmbedderInstallDialog />
    </div>
  );
};
