/**
 * DetachedToolbar —— 独立窗口右上角浮动小工具栏。
 *
 * 仅在 ?detached=1 的窗口里出现:
 *   - "返回主窗"按钮:把当前 path 投回主窗 tabsStore + 自己关窗
 *   - 关闭(同浏览器关闭键)
 *
 * 同时挂一个 resize 监听,把当前尺寸写入 localStorage('cy.detached.size'),
 * 下次 openDetachedTab 时读取作为初始尺寸。
 */
import * as React from 'react';
import { ArrowLeftToLine } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { isDetachedWindow, writeDetachedSize } from './detachedWindow';
import { publishCrossWindow } from './useCrossWindowSync';

export const DetachedToolbar: React.FC = () => {
  const [active, setActive] = React.useState(() => isDetachedWindow());

  React.useEffect(() => {
    setActive(isDetachedWindow());
  }, []);

  // 记忆尺寸:每次窗口尺寸稳定 1s 后保存
  React.useEffect(() => {
    if (!active) return;
    let timer: number | null = null;
    const onResize = () => {
      if (timer != null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        writeDetachedSize(window.innerWidth, window.innerHeight);
      }, 1000);
    };
    window.addEventListener('resize', onResize);
    // 关窗前再保存一次
    const onBeforeUnload = () => writeDetachedSize(window.innerWidth, window.innerHeight);
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      if (timer != null) window.clearTimeout(timer);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [active]);

  if (!active) return null;

  const handleReturn = () => {
    // 当前 path:去掉 detached/label query
    const url = new URL(window.location.href);
    url.searchParams.delete('detached');
    url.searchParams.delete('label');
    const path = url.pathname + (url.search ? url.search : '');
    publishCrossWindow({ type: 'detach-return', path, title: document.title });
    // 给主窗一次 tick 接收消息,然后关自己
    window.setTimeout(() => {
      try {
        window.close();
      } catch {
        /* ignore */
      }
    }, 60);
  };

  return (
    <div
      className={cn(
        'fixed right-3 top-3 z-[60] flex items-center gap-1 rounded-full bg-[var(--cy-surface-1)]',
        'border border-[var(--cy-border-subtle)] px-1 py-1 shadow-[var(--cy-shadow-md)]',
      )}
    >
      <button
        type="button"
        onClick={handleReturn}
        className={cn(
          'flex h-7 items-center gap-1 rounded-full px-3 text-xs',
          'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
        )}
        aria-label="返回主窗"
      >
        <ArrowLeftToLine className="h-3.5 w-3.5" />
        返回主窗
      </button>
    </div>
  );
};
