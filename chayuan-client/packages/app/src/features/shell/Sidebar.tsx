/**
 * Sidebar(M1 重排,对齐参考图 IA)。
 *
 * 段:
 *   1. 用户区:头像 + 昵称 + 编辑 icon(右) + 折叠键
 *   2. + 新建对话(Ctrl K)
 *   3. 一级入口 4 项:知识库 / 模型广场 / AI Space / 历史对话
 *   4. 二级:最近对话(展开后显示;折叠态隐藏)
 *   5. 底部:Admin(角色为 admin 时显示)
 *
 * 选中态:浅蓝 pill (Pill tone="soft" active);hover 文字加深 + 浅灰背景。
 */

import * as React from 'react';
import {
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  MoreHorizontal,
  Pencil,
  Trash2,
  MessageSquareText,
  Library,
  Box,
  Clock,
  Shield,
  Plug,
  Wrench,
  Briefcase,
  Grid3x3,
  Store,
  Bell,
  Tags,
  Lock,
} from 'lucide-react';
import { useNavigate, useLocation } from '@tanstack/react-router';
// uuid / useQueryClient / upsertConversation 之前用于 onNewChat 预建会话,
// 改为草稿模式后已不再使用,移除避免未引用 lint 噪声。
import {
  Avatar,
  Button,
  cn,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@chayuan/ui';
import {
  useConversationList,
  useDeleteConversation,
  useRenameConversation,
  type ConversationView,
} from '../conversations/useConversations';
import { useComposerStore } from '../../store/composer';
import { useAuthStore } from '../../store/auth';
import { useTabsStore } from '../../store/tabs';
import { useTranslation } from '../../i18n';
import { getPlatform } from '@chayuan/platform-shared';

export interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse(): void;
  /** 由 Chrome 透传,供 globals.css 在 cy-embed-mode / cy-fullscreen 下隐藏整列 */
  'data-shell'?: string;
}

interface NavItem {
  to: string;
  labelKey: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { to: '/kb', labelKey: 'nav.knowledge', icon: Library },
  { to: '/marketplace', labelKey: 'nav.modelMarket', icon: Box },
  { to: '/mcp', labelKey: 'nav.mcp', icon: Plug },
  { to: '/tools', labelKey: 'nav.tools', icon: Wrench },
  // 单机版移除训练数据中心(/annotation)
  { to: '/history', labelKey: 'nav.history', icon: Clock },
];

/**
 * 「在线办公」功能组 —— 单机版不支持,点击后进 OnlineFeaturePage 占位页。
 * 文案走 i18n(nav.online*);路由与 OnlineFeaturePage 的 feature key 对齐
 * (page-registry:/online/<key>)。
 */
interface OnlineNavItem {
  to: string;
  labelKey: string;
  icon: React.ComponentType<{ className?: string }>;
}

const ONLINE_NAV_ITEMS: ReadonlyArray<OnlineNavItem> = [
  { to: '/online/office', labelKey: 'nav.onlineOffice', icon: Briefcase },
  { to: '/online/space', labelKey: 'nav.onlineSpace', icon: Grid3x3 },
  { to: '/online/market', labelKey: 'nav.onlineMarket', icon: Store },
  { to: '/online/tasks', labelKey: 'nav.onlineTasks', icon: Bell },
  { to: '/online/annotation', labelKey: 'nav.onlineAnnotation', icon: Tags },
];

export const Sidebar: React.FC<SidebarProps> = ({ collapsed, onToggleCollapse, ...rest }) => {
  const { t } = useTranslation();
  const list = useConversationList();
  const del = useDeleteConversation();
  const rename = useRenameConversation();
  const user = useAuthStore((s) => s.user);
  const setActive = useComposerStore((s) => s.setActive);
  const activeConvId = useComposerStore((s) => s.activeConversationId);
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();
  const location = useLocation();

  const onNavItem = (to: string, titleKey: string, icon: string) => {
    open(to, { title: titleKey, icon });
    void navigate({ to: to as never });
  };

  const onNewChat = () => {
    // 每次点击都新开一个 /chat 草稿 tab(forceNew:true),不复用、不影响现有 tab。
    // 草稿模式:不预先 upsertConversation,首发消息时 ChatPage.handleSend lazy 建会话。
    setActive(null);
    open('/chat', { title: t('chat.newConversation'), icon: 'message-square', forceNew: true });
    void navigate({ to: '/chat' as never });
  };

  const onSelectConv = (c: ConversationView) => {
    const cid = c.remote_id ?? c.id;
    setActive(cid);
    const path = `/chat/${encodeURIComponent(cid)}`;
    open(path, { title: c.title, icon: 'message-square' });
    void navigate({ to: path as never });
  };

  return (
    <aside
      {...rest}
      className={cn(
        'flex h-full min-h-0 flex-col border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] transition-[width] duration-200 ease-out',
        // 列宽由外层 grid 决定;width 仅在外层不接管时生效(向后兼容)
      )}
    >
      {/* 用户区 —— 单机版无登录,缺省显示 "察元用户" 而不是 "游客" */}
      <div className={cn('flex items-center gap-2 px-3 py-3', collapsed && 'justify-center px-2')}>
        <Avatar name={user?.username ?? '察'} size={32} />
        {!collapsed && (
          <>
            <p className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--cy-text-primary)]">
              {user?.username ?? '察元用户'}
            </p>
            <button
              type="button"
              className="flex h-6 w-6 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
              aria-label="编辑昵称"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          </>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onToggleCollapse}
          aria-label="切换侧栏"
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
      </div>

      {/* 新建对话 */}
      <div className="px-2">
        <Button
          variant="outline"
          className={cn(
            'w-full justify-start gap-2 rounded-xl border-[var(--cy-border-subtle)]',
            collapsed && 'justify-center px-0',
          )}
          onClick={() => void onNewChat()}
        >
          <Plus className="h-4 w-4" />
          {!collapsed && (
            <>
              <span className="flex-1 text-left">{t('nav.newChat')}</span>
              <kbd className="text-[11px] text-[var(--cy-text-tertiary)]">Ctrl K</kbd>
            </>
          )}
        </Button>
      </div>

      {/* 一级入口 */}
      {!collapsed && (
        <nav className="mt-3 flex flex-col gap-0.5 px-2 text-sm">
          {NAV_ITEMS.map((it) => {
            const Icon = it.icon;
            const active = location.pathname === it.to;
            return (
              <button
                key={it.to}
                type="button"
                onClick={() =>
                  onNavItem(it.to, it.labelKey, iconKeyFor(it.to))
                }
                className={cn(
                  'flex items-center gap-2 rounded-full px-3 py-1.5 text-left transition-colors',
                  active
                    ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
                    : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
                )}
              >
                <Icon className="h-4 w-4" />
                {t(it.labelKey)}
              </button>
            );
          })}
        </nav>
      )}

      {/* 最近对话 */}
      {!collapsed && (
        <div className="mt-4 flex-1 overflow-y-auto px-2 pb-2">
          <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
            {t('history.title')}
          </p>
          {list.isLoading && (
            <p className="px-2 text-xs text-[var(--cy-text-tertiary)]">{t('common.loading')}</p>
          )}
          {list.data?.slice(0, 20).map((c) => (
            <ConversationItem
              key={c.id}
              item={c}
              active={c.id === activeConvId || c.remote_id === activeConvId}
              onSelect={() => onSelectConv(c)}
              onRename={(name) => rename.mutate({ item: c, name })}
              onDelete={() => del.mutate(c)}
            />
          ))}
          {!list.isLoading && (list.data?.length ?? 0) === 0 && (
            <p className="px-2 text-xs text-[var(--cy-text-tertiary)]">{t('history.empty')}</p>
          )}
        </div>
      )}

      {/* 在线办公功能组 —— 单机版不支持,点击进占位页(流式介绍 + 联系商务) */}
      {!collapsed && (
        <div className="shrink-0 border-t border-[var(--cy-border-subtle)] px-2 py-2">
          <p className="mb-1 flex items-center gap-1 px-2 text-[11px] font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
            <Lock className="h-3 w-3" />
            {t('nav.onlineGroup')}
          </p>
          <nav className="flex flex-col gap-0.5 text-sm">
            {ONLINE_NAV_ITEMS.map((it) => {
              const Icon = it.icon;
              const active = location.pathname === it.to;
              return (
                <button
                  key={it.to}
                  type="button"
                  onClick={() => onNavItem(it.to, t(it.labelKey), 'lock')}
                  title={`${t(it.labelKey)} · ${t('nav.onlineHint')}`}
                  className={cn(
                    'flex items-center gap-2 rounded-full px-3 py-1.5 text-left transition-colors',
                    active
                      ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
                      : 'text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-secondary)]',
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="flex-1 truncate">{t(it.labelKey)}</span>
                  <span className="shrink-0 rounded-full bg-[var(--cy-surface-2)] px-1.5 py-0.5 text-[10px] font-normal text-[var(--cy-text-tertiary)]">
                    {t('nav.onlineBadge')}
                  </span>
                </button>
              );
            })}
          </nav>
        </div>
      )}

      {/* 底部:Admin */}
      {user?.role === 'admin' && (
        <div className="border-t border-[var(--cy-border-subtle)] p-2">
          <Button
            variant="ghost"
            className={cn('w-full justify-start gap-2', collapsed && 'justify-center px-0')}
            onClick={() => onNavItem('/admin/traces', 'admin.traces', 'shield')}
          >
            <Shield className="h-4 w-4" />
            {!collapsed && <span>Admin</span>}
          </Button>
        </div>
      )}
    </aside>
  );
};

function iconKeyFor(path: string): string {
  if (path === '/kb') return 'library';
  if (path === '/marketplace') return 'box';
  if (path === '/mcp') return 'plug';
  if (path === '/tools') return 'wrench';
  if (path === '/history') return 'clock';
  if (path === '/annotation') return 'tag';
  if (path.startsWith('/chat')) return 'message-square';
  if (path === '/settings') return 'settings';
  return 'home';
}

const ConversationItem: React.FC<{
  item: ConversationView;
  active: boolean;
  onSelect: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}> = ({ item, active, onSelect, onRename, onDelete }) => {
  return (
    <div
      className={cn(
        'group relative flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
        active
          ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
          : 'hover:bg-[var(--cy-surface-2)]',
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        className="flex flex-1 items-center gap-2 truncate text-left"
      >
        <MessageSquareText className="h-3.5 w-3.5 flex-shrink-0 text-[var(--cy-text-tertiary)]" />
        <span className="truncate">{item.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-6 w-6 items-center justify-center rounded opacity-0 transition-opacity hover:bg-[var(--cy-surface-3)] group-hover:opacity-100"
            aria-label="更多"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onClick={() => {
              const name = window.prompt('重命名为?', item.title);
              if (name && name.trim()) onRename(name.trim());
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
            重命名
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={async () => {
              // 走 platform.dialog.confirm(plugin-dialog.ask),不要裸 confirm():
              // Tauri 2 webview 把 window.confirm 重定向到 plugin:dialog|confirm,
              // 某些 Tauri 2.x 版本即使授权 dialog:allow-confirm 也会 ACL 拒。
              const ok = await getPlatform().dialog.confirm(
                `删除「${item.title}」?此操作不可恢复。`,
                { title: '删除会话' },
              );
              if (ok) onDelete();
            }}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
};
