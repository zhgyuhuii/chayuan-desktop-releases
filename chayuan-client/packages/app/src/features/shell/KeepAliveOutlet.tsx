/**
 * KeepAliveOutlet —— 一个 Tab 一份组件实例,激活时显示,非激活 display:none。
 *
 * 不依赖 router.params,完全由 tab.path 驱动:
 *   - 内部 useMemo 解析 path → 得到 RouteEntry + params
 *   - 渲染对应 PageComponent
 *   - Tab 首次激活时调用 entry.onActivate(同步外部副作用,如 composerStore.setActive)
 *
 * 滚动还原:
 *   - 内部 ref 监听 scroll,在切走前 saveScroll(id, y)
 *   - 切回时把 scrollTop 还原(下一帧执行,避免布局未完成)
 *
 * 性能:
 *   - 非激活的 Tab 仍挂载,但 display:none 不参与 layout 计算
 *   - 流式 / 计时器等仍在执行(用户预期);若需要"睡眠"需在页面内通过 visibility API 处理
 */

import * as React from 'react';
import { resolveRoute } from './page-registry';
import { useTabsStore, type Tab } from '../../store/tabs';
import { ArenaScopeProvider } from '../../store/arenaScope';

export interface KeepAliveOutletProps {
  tab: Tab;
  active: boolean;
}

export const KeepAliveOutlet: React.FC<KeepAliveOutletProps> = ({ tab, active }) => {
  const saveScroll = useTabsStore((s) => s.saveScroll);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const wasActive = React.useRef(false);

  const resolved = React.useMemo(() => resolveRoute(tab.path), [tab.path]);

  // 激活时执行副作用
  React.useEffect(() => {
    if (!active || !resolved) return;
    resolved.entry.onActivate?.(resolved.params, tab.path);
  }, [active, resolved, tab.path]);

  // 切走前保存 scrollTop
  React.useEffect(() => {
    if (active) {
      wasActive.current = true;
      // 还原滚动位置(下一帧)
      const y = tab.scrollY ?? 0;
      const el = containerRef.current;
      if (el) {
        requestAnimationFrame(() => {
          el.scrollTop = y;
        });
      }
      return undefined;
    }
    if (wasActive.current && containerRef.current) {
      saveScroll(tab.id, containerRef.current.scrollTop);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (!resolved) {
    return active ? (
      <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
        未识别的路径:{tab.path}
      </div>
    ) : null;
  }

  return (
    <div
      ref={containerRef}
      // 用 hidden 属性 + display:none 双保险;非 active 不参与 layout
      hidden={!active}
      style={{ display: active ? undefined : 'none' }}
      className="h-full overflow-y-auto"
      data-tab-id={tab.id}
      data-tab-active={active ? 'true' : 'false'}
    >
      <TabErrorBoundary path={tab.path}>
        <React.Suspense fallback={<TabFallback />}>
          <ArenaScopeProvider scope={tab.id}>
            {resolved.entry.render(resolved.params, tab.path)}
          </ArenaScopeProvider>
        </React.Suspense>
      </TabErrorBoundary>
    </div>
  );
};

const TabFallback: React.FC = () => (
  <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
    载入中…
  </div>
);

/**
 * Tab 级错误边界——任何 page 组件 render 抛错都在这里捕住，避免出现整页空白。
 * 历史教训：page 组件若 render 期未捕异常，KeepAliveOutlet 仍会渲染但子树
 * 卸载，用户感知就是"点了菜单页面空白没反应"。
 */
class TabErrorBoundary extends React.Component<
  { path: string; children: React.ReactNode },
  { error: Error | null }
> {
  override state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  override componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[TabErrorBoundary]', this.props.path, error, info.componentStack);
  }

  override render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 py-10 text-center">
        <p className="text-base font-semibold text-[var(--cy-text-primary)]">
          页面渲染出错
        </p>
        <p className="text-xs font-mono text-[var(--cy-text-tertiary)]">
          路径：{this.props.path}
        </p>
        <p className="max-w-2xl break-all text-sm text-destructive">
          {this.state.error.name}: {this.state.error.message}
        </p>
        <p className="text-xs text-[var(--cy-text-tertiary)]">
          完整堆栈见 DevTools Console（搜 [TabErrorBoundary]）
        </p>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-2 rounded-md border border-[var(--cy-border-subtle)] px-3 py-1 text-xs hover:bg-[var(--cy-surface-2)]"
        >
          重新渲染
        </button>
      </div>
    );
  }
}
