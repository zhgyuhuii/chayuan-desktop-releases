/**
 * 历史对话(参考图"历史聊天")。
 *
 * 功能:
 *  - 分组:今天 / 昨天 / 上周 / 更早(基于 updated_at)
 *  - 每行:点击进入会话;hover 显示 ⋯ 菜单;**右键**也弹同一份菜单(rename / delete)
 *  - 顶部右侧:**一键清空**(确认对话框 + 并发 delete + 即时清空 UI)
 *
 * 实现:
 *  - 右键菜单:轻量自实现(fixed position + outside click + Esc 关闭),不引入新依赖
 *  - 一键清空:走 useClearAllConversations,Promise.allSettled 容错单条失败
 */

import * as React from 'react';
import { Loader2, MessageSquareText, Pencil, Trash2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { cn } from '@chayuan/ui';
import { getPlatform } from '@chayuan/platform-shared';
import {
  useClearAllConversations,
  useConversationList,
  useDeleteConversation,
  useRenameConversation,
  type ConversationView,
} from '../conversations/useConversations';
import { useComposerStore } from '../../store/composer';
import { useTabsStore } from '../../store/tabs';
import { useTranslation } from '../../i18n';

interface Group {
  labelKey: string;
  items: ConversationView[];
}

const DAY = 24 * 60 * 60 * 1000;

interface CtxMenuState {
  x: number;
  y: number;
  item: ConversationView;
}

export const HistoryPage: React.FC = () => {
  const { t } = useTranslation();
  const list = useConversationList();
  const del = useDeleteConversation();
  const rename = useRenameConversation();
  const clearAll = useClearAllConversations();
  const setActive = useComposerStore((s) => s.setActive);
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();
  const [ctxMenu, setCtxMenu] = React.useState<CtxMenuState | null>(null);

  // 全局 outside click / Esc 关闭右键菜单
  React.useEffect(() => {
    if (!ctxMenu) return;
    const onClick = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCtxMenu(null);
    };
    // 用捕获 + 微延迟,避免触发同一帧的 onClick 立刻关闭
    const onDown = (e: MouseEvent) => {
      // 让按钮自己的 onClick 先执行
      window.requestAnimationFrame(() => {
        // 如果点击在菜单内则不关
        const tgt = e.target as Element | null;
        if (tgt?.closest('[data-history-ctx-menu]')) return;
        setCtxMenu(null);
      });
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('blur', onClick);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('blur', onClick);
    };
  }, [ctxMenu]);

  const groups = React.useMemo<Group[]>(() => {
    const items = (list.data ?? []).slice().sort((a, b) => b.updated_at - a.updated_at);
    const now = Date.now();
    const startOfToday = new Date(now).setHours(0, 0, 0, 0);
    const startOfYesterday = startOfToday - DAY;
    const startOfLastWeek = startOfToday - 7 * DAY;

    const today: ConversationView[] = [];
    const yesterday: ConversationView[] = [];
    const lastWeek: ConversationView[] = [];
    const earlier: ConversationView[] = [];

    for (const c of items) {
      if (c.updated_at >= startOfToday) today.push(c);
      else if (c.updated_at >= startOfYesterday) yesterday.push(c);
      else if (c.updated_at >= startOfLastWeek) lastWeek.push(c);
      else earlier.push(c);
    }
    return [
      { labelKey: 'history.today', items: today },
      { labelKey: 'history.yesterday', items: yesterday },
      { labelKey: 'history.lastWeek', items: lastWeek },
      { labelKey: 'history.earlier', items: earlier },
    ].filter((g) => g.items.length > 0);
  }, [list.data]);

  const onSelect = (c: ConversationView) => {
    const id = c.remote_id ?? c.id;
    setActive(id);
    const path = `/chat/${encodeURIComponent(id)}`;
    open(path, { title: c.title, icon: 'message-square' });
    void navigate({ to: path as never });
  };

  const onRename = async (c: ConversationView) => {
    setCtxMenu(null);
    // platform.dialog.prompt:Tauri webview 下兜底原生 prompt;Web 端直接 window.prompt
    const name = await getPlatform().dialog.prompt('重命名为?', { defaultValue: c.title });
    if (name && name.trim()) rename.mutate({ item: c, name: name.trim() });
  };

  const onDelete = async (c: ConversationView) => {
    setCtxMenu(null);
    // 之前用裸 ``confirm()``,Tauri webview 把它重定向到 plugin-dialog,某些版本下报
    // "dialog.confirm not allowed. Command not found"。改走 platform.dialog.confirm
    // 在 Tauri 端用 plugin-dialog.ask(已显式授权 dialog:allow-ask),Web 端走原生 confirm。
    const ok = await getPlatform().dialog.confirm(t('history.deleteConfirm'), { title: '删除会话' });
    if (ok) del.mutate(c);
  };

  const onContextMenu = (e: React.MouseEvent, c: ConversationView) => {
    e.preventDefault();
    e.stopPropagation();
    // 简单避让屏幕边缘:菜单宽 ~140px / 高 ~76px
    const x = Math.min(e.clientX, window.innerWidth - 160);
    const y = Math.min(e.clientY, window.innerHeight - 96);
    setCtxMenu({ x, y, item: c });
  };

  const totalCount = (list.data ?? []).length;
  const onClearAll = async () => {
    if (totalCount === 0) return;
    // 之前用裸 ``confirm()``,Tauri webview 把它重定向到 plugin-dialog,某些版本下报
    // "dialog.confirm not allowed. Command not found"。改走 platform.dialog.confirm。
    const ok = await getPlatform().dialog.confirm(
      `确定要清空全部 ${totalCount} 个会话吗?\n本地与已登录端的会话都会被删除,且无法恢复。`,
      { title: '清空全部历史' },
    );
    if (!ok) return;
    clearAll.mutate(list.data ?? []);
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-[var(--cy-text-primary)]">{t('history.title')}</h1>
        <button
          type="button"
          onClick={() => void onClearAll()}
          disabled={totalCount === 0 || clearAll.isPending}
          className={cn(
            'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors',
            totalCount === 0
              ? 'border-[var(--cy-border-subtle)] text-[var(--cy-text-tertiary)] opacity-60 cursor-not-allowed'
              : 'border-red-200 bg-white text-red-600 hover:bg-red-50',
          )}
          title="清空全部历史"
        >
          {clearAll.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
          清空全部
          {totalCount > 0 && (
            <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">
              {totalCount}
            </span>
          )}
        </button>
      </header>

      {list.isLoading && (
        <p className="mt-6 text-sm text-[var(--cy-text-tertiary)]">{t('common.loading')}</p>
      )}
      {!list.isLoading && groups.length === 0 && (
        <p className="mt-12 text-center text-sm text-[var(--cy-text-tertiary)]">
          {t('history.empty')}
        </p>
      )}

      <div className="mt-6 flex flex-col gap-6">
        {groups.map((g) => (
          <section key={g.labelKey}>
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
              {t(g.labelKey)}
            </h2>
            <div className="flex flex-col gap-2">
              {g.items.map((c) => (
                <Row
                  key={c.id}
                  item={c}
                  onClick={() => onSelect(c)}
                  onContextMenu={(e) => onContextMenu(e, c)}
                  onRename={() => onRename(c)}
                  onDelete={() => onDelete(c)}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      {/* 右键菜单 */}
      {ctxMenu && (
        <div
          data-history-ctx-menu
          role="menu"
          style={{ position: 'fixed', left: ctxMenu.x, top: ctxMenu.y, zIndex: 1000 }}
          className="min-w-[140px] overflow-hidden rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] py-1 shadow-[var(--cy-shadow-lg)]"
        >
          <CtxItem icon={Pencil} onClick={() => onRename(ctxMenu.item)}>
            重命名
          </CtxItem>
          <CtxItem
            icon={Trash2}
            danger
            onClick={() => onDelete(ctxMenu.item)}
          >
            删除
          </CtxItem>
        </div>
      )}
    </div>
  );
};

const CtxItem: React.FC<{
  icon: React.ComponentType<{ className?: string }>;
  danger?: boolean;
  onClick(): void;
  children: React.ReactNode;
}> = ({ icon: Icon, danger, onClick, children }) => (
  <button
    type="button"
    role="menuitem"
    onClick={onClick}
    className={cn(
      'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors',
      danger
        ? 'text-red-600 hover:bg-red-50'
        : 'text-[var(--cy-text-primary)] hover:bg-[var(--cy-surface-1)]',
    )}
  >
    <Icon className="h-3.5 w-3.5" />
    {children}
  </button>
);

const Row: React.FC<{
  item: ConversationView;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onRename: () => void;
  onDelete: () => void;
}> = ({ item, onClick, onContextMenu, onRename, onDelete }) => {
  const time = new Date(item.updated_at);
  const timeLabel = `${time.getHours().toString().padStart(2, '0')}:${time.getMinutes().toString().padStart(2, '0')}`;
  const [hoverMenuOpen, setHoverMenuOpen] = React.useState(false);
  return (
    <div
      onContextMenu={onContextMenu}
      className={cn(
        'group relative flex items-center gap-3 rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-3',
        'transition-colors hover:bg-[var(--cy-surface-1)]',
      )}
    >
      <button
        type="button"
        onClick={onClick}
        className="flex flex-1 items-center gap-3 text-left"
      >
        <MessageSquareText className="h-4 w-4 flex-shrink-0 text-[var(--cy-text-tertiary)]" />
        <span className="flex-1 truncate text-sm text-[var(--cy-text-primary)]">{item.title}</span>
        <span className="text-xs text-[var(--cy-text-tertiary)]">{timeLabel}</span>
      </button>
      {/* 悬停 ⋯ 菜单 */}
      <div className="relative">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setHoverMenuOpen((v) => !v);
          }}
          onBlur={() => window.setTimeout(() => setHoverMenuOpen(false), 120)}
          className="flex h-7 w-7 items-center justify-center rounded text-[var(--cy-text-tertiary)] opacity-0 transition-opacity hover:bg-[var(--cy-surface-2)] group-hover:opacity-100"
          aria-label="更多"
          aria-haspopup="menu"
          aria-expanded={hoverMenuOpen}
        >
          ⋯
        </button>
        {hoverMenuOpen && (
          <div
            role="menu"
            className="absolute right-0 top-full z-30 mt-1 min-w-[140px] overflow-hidden rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] py-1 shadow-[var(--cy-shadow-lg)]"
          >
            <CtxItem
              icon={Pencil}
              onClick={() => {
                setHoverMenuOpen(false);
                onRename();
              }}
            >
              重命名
            </CtxItem>
            <CtxItem
              icon={Trash2}
              danger
              onClick={() => {
                setHoverMenuOpen(false);
                onDelete();
              }}
            >
              删除
            </CtxItem>
          </div>
        )}
      </div>
    </div>
  );
};
