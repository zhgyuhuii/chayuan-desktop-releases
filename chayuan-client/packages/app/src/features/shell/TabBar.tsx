/**
 * TabBar —— 浏览器式 Tab 条,顶部展示。
 *
 * 设计稿对应:参考图顶栏的"主页 / 设置 / AI 写作 ×" 三 Tab。
 *
 * 行为:
 *   - 点击切换激活
 *   - hover 出现 close X(pinned 隐藏)
 *   - 中键(button=1)关闭(浏览器一致)
 *   - 右键菜单:关闭其他 / 关闭全部
 *   - 长 title 截断(max-w + truncate)
 *
 * 不做(M1 范围):
 *   - 拖拽重排(reorder 已在 store,UI 留 M5)
 *   - 拖出 Tab → 独立窗口(M5)
 */

import * as React from 'react';
import { Home, Settings, MessageSquare, Library, Box, Grid3x3, Clock, Plug, Wrench, Shield, Briefcase, X } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { cn } from '@chayuan/ui';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@chayuan/ui';
import { useTabsStore, tabSelectors, type Tab } from '../../store/tabs';
import { useTranslation } from '../../i18n';

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  home: Home,
  settings: Settings,
  'message-square': MessageSquare,
  library: Library,
  box: Box,
  'grid-3x3': Grid3x3,
  clock: Clock,
  plug: Plug,
  wrench: Wrench,
  shield: Shield,
  briefcase: Briefcase,
};

export const TabBar: React.FC = () => {
  const tabs = useTabsStore(tabSelectors.list);
  const activeId = useTabsStore(tabSelectors.activeId);
  const activate = useTabsStore((s) => s.activate);
  const close = useTabsStore((s) => s.close);
  const closeOthers = useTabsStore((s) => s.closeOthers);
  const closeAll = useTabsStore((s) => s.closeAll);
  const navigate = useNavigate();

  if (tabs.length === 0) return null;

  return (
    <div
      role="tablist"
      data-shell="tab-bar"
      className={cn(
        'flex h-10 items-end gap-1 overflow-x-auto px-2',
        '[scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden',
      )}
    >
      {tabs.map((t) => (
        <TabItem
          key={t.id}
          tab={t}
          active={t.id === activeId}
          onSelect={() => {
            activate(t.id);
            void navigate({ to: t.path as never });
          }}
          onClose={() => close(t.id)}
          onCloseOthers={() => closeOthers(t.id)}
          onCloseAll={() => closeAll()}
        />
      ))}
    </div>
  );
};

interface TabItemProps {
  tab: Tab;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
  onCloseOthers: () => void;
  onCloseAll: () => void;
}

const TabItem: React.FC<TabItemProps> = ({
  tab,
  active,
  onSelect,
  onClose,
  onCloseOthers,
  onCloseAll,
}) => {
  const { t } = useTranslation();
  const Icon = (tab.icon && ICON_MAP[tab.icon]) || Home;
  const [menuOpen, setMenuOpen] = React.useState(false);
  const handleAuxClick = (e: React.MouseEvent) => {
    if (e.button === 1 && !tab.pinned) {
      e.preventDefault();
      onClose();
    }
  };
  // title 经 t() 兜底:若是 i18n key,翻译;否则原样
  const display = React.useMemo(() => {
    if (tab.title.includes('.')) {
      const v = t(tab.title);
      return v === tab.title ? tab.title : v;
    }
    return tab.title;
  }, [t, tab.title]);

  return (
    <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
      <DropdownMenuTrigger asChild>
        <div
          role="tab"
          aria-selected={active}
          onPointerDownCapture={(e) => {
            // 抑制 Radix DropdownMenuTrigger 在左键 pointer-down 时自动打开
            // 下拉菜单的默认行为 — 这里只允许「右键 onContextMenu → setMenuOpen」
            // 这一条路开菜单。少了这行,左键点 tab 会变成弹下拉,onClick 切页失效。
            if (e.button === 0) e.preventDefault();
          }}
          onClick={() => {
            setMenuOpen(false);
            onSelect();
          }}
          onAuxClick={handleAuxClick}
          onContextMenu={(e) => {
            e.preventDefault();
            setMenuOpen(true);
          }}
          className={cn(
            'group flex h-9 max-w-[200px] cursor-pointer select-none items-center gap-2 rounded-t-lg px-3 text-sm transition-colors',
            active
              ? 'bg-[var(--cy-surface-base)] text-[var(--cy-text-primary)] shadow-[var(--cy-shadow-sm)]'
              : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
          )}
        >
          <Icon className="h-4 w-4 flex-shrink-0" />
          <span className="truncate">{display}</span>
          {!tab.pinned && (
            <button
              type="button"
              aria-label="关闭"
              onClick={(e) => {
                e.stopPropagation();
                onClose();
              }}
              className={cn(
                'ml-1 flex h-5 w-5 items-center justify-center rounded transition-colors',
                'opacity-0 group-hover:opacity-100',
                'hover:bg-[var(--cy-surface-3)]',
                active && 'opacity-70',
              )}
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {!tab.pinned && (
          <DropdownMenuItem onClick={onClose}>{t('common.close')}</DropdownMenuItem>
        )}
        <DropdownMenuItem onClick={onCloseOthers}>关闭其他</DropdownMenuItem>
        <DropdownMenuItem onClick={onCloseAll}>关闭全部</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
