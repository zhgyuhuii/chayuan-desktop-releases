/**
 * FullscreenButton —— TabBar 右侧的"⛶ 专注模式"图标按钮。
 *
 * 第一次点 / 按 F11:tooltip 显示一次"专注模式 (F11):隐藏侧栏与标签条,
 * 让当前页占满。再按 F11 或 Esc 退出。"
 *
 * 实现细节:hint 通过 useFullscreenStore.hintShown 持久化,只弹一次。
 */
import * as React from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  cn,
} from '@chayuan/ui';
import { useFullscreenStore } from './useFullscreenWorkspace';

export const FullscreenButton: React.FC<{ className?: string }> = ({ className }) => {
  const active = useFullscreenStore((s) => s.active);
  const toggle = useFullscreenStore((s) => s.toggle);
  const hintShown = useFullscreenStore((s) => s.hintShown);
  const markHintShown = useFullscreenStore((s) => s.markHintShown);

  // 首次见到此按钮时主动弹一次 tooltip
  const [hintOpen, setHintOpen] = React.useState(false);
  React.useEffect(() => {
    if (hintShown) return;
    const t = window.setTimeout(() => {
      setHintOpen(true);
      markHintShown();
      // 5s 后自动关
      window.setTimeout(() => setHintOpen(false), 5000);
    }, 800);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const Icon = active ? Minimize2 : Maximize2;
  const label = active ? '退出专注模式 (F11)' : '专注模式 (F11)';

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip open={hintOpen || undefined} onOpenChange={(o) => !o && setHintOpen(false)}>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => {
              setHintOpen(false);
              toggle();
            }}
            aria-label={label}
            className={cn(
              'flex h-7 w-7 items-center justify-center rounded text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
              className,
            )}
          >
            <Icon className="h-4 w-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-[260px] text-xs leading-relaxed">
          {hintShown ? (
            label
          ) : (
            <>
              <strong className="block">专注模式 (F11)</strong>
              隐藏侧栏与标签条,让当前页占满。再按 F11 或 Esc 退出。
            </>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
