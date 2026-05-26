/**
 * TabHost —— 渲染所有打开的 Tab,非激活 Tab display:none(KeepAlive)。
 *
 * URL 同步策略(ADR-0001):
 *   - 单 BrowserHistory + 单 router;Tab 之间共享 history;
 *   - tabsStore.activeId 变化 → useNavigate 投影到 router.location.pathname;
 *   - router.location.pathname 变化 → 若不命中现有 Tab,自动 open() 新 Tab。
 *   这两个方向用 lastSyncedPath ref 防止互相触发循环。
 */

import * as React from 'react';
import { useLocation, useNavigate } from '@tanstack/react-router';
import { KeepAliveOutlet } from './KeepAliveOutlet';
import { useTabsStore, tabSelectors } from '../../store/tabs';
import { deriveIcon, deriveTitle, resolveRoute } from './page-registry';

type RouterLocation = ReturnType<typeof useLocation>;

function stringifySearch(search: unknown): string {
  if (!search) return '';
  if (typeof search === 'string') return search;
  if (typeof search !== 'object') return '';

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined || value === null) continue;
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item === undefined || item === null) continue;
      params.append(key, typeof item === 'object' ? JSON.stringify(item) : String(item));
    }
  }

  const query = params.toString();
  return query ? `?${query}` : '';
}

function routeLocationToPath(location: RouterLocation): string {
  const searchStr =
    'searchStr' in location && typeof location.searchStr === 'string'
      ? location.searchStr
      : stringifySearch(location.search);
  return `${location.pathname}${searchStr}`;
}

export const TabHost: React.FC = () => {
  const tabs = useTabsStore(tabSelectors.list);
  const activeId = useTabsStore(tabSelectors.activeId);
  const open = useTabsStore((s) => s.open);
  const activate = useTabsStore((s) => s.activate);
  const navigate = useNavigate();
  const location = useLocation();
  const currentPath = routeLocationToPath(location);

  const lastSyncedPath = React.useRef<string | null>(null);

  // 1) URL → tabsStore:URL 变化时,找到/创建对应 Tab 并激活
  React.useEffect(() => {
    const path = currentPath;
    if (path === lastSyncedPath.current) return;
    lastSyncedPath.current = path;

    const existing = tabs.find((t) => t.path === path);
    if (existing) {
      if (existing.id !== activeId) activate(existing.id);
      return;
    }
    // 不存在 → 解析路径合法性
    const resolved = resolveRoute(path);
    if (!resolved) return; // 不是 Tab 路径(如 /login),不开
    open(path, {
      title: deriveTitle(path),
      ...(deriveIcon(path) !== undefined ? { icon: deriveIcon(path) } : {}),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPath]);

  // 2) tabsStore.activeId → URL:激活变化时投影到 location。
  // 加 tab.path 依赖以处理"用户在同一 tab 内导航"(如 /chat → /chat/<id>)的情况:
  // updatePath 会改 tab.path,这里需要继续把 URL 拉齐。
  const activeTabPath = tabs.find((t) => t.id === activeId)?.path;
  React.useEffect(() => {
    if (!activeId || !activeTabPath) return;
    if (activeTabPath === currentPath) return;
    if (activeTabPath === lastSyncedPath.current) return;
    lastSyncedPath.current = activeTabPath;
    void navigate({ to: activeTabPath as never });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, activeTabPath]);

  // 3) 启动时若没有任何 Tab,且 URL 不是 Tab 路径,默认开 /home
  React.useEffect(() => {
    if (tabs.length > 0) return;
    const path = currentPath;
    const target = resolveRoute(path) ? path : '/home';
    open(target, { pinned: target === '/home' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="relative flex h-full w-full flex-1 overflow-hidden">
      {tabs.map((t) => (
        <div
          key={t.id}
          className="absolute inset-0"
          style={{ display: t.id === activeId ? 'block' : 'none' }}
        >
          <KeepAliveOutlet tab={t} active={t.id === activeId} />
        </div>
      ))}
    </div>
  );
};
