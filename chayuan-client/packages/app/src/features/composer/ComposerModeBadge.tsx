/**
 * 一颗小 Pill,显示当前 composer 模式(对话/文生图/文生视频/语音合成 …)。
 *
 * 用户视角:模型一选,输入框上方立刻看到颜色 + 图标 + 中文标签 + 一句说明,
 * 不用读模型 ID 就能知道这个模型该怎么用、会输出什么。
 *
 * 故意做得**只读 + 信息密度高**,不接 onClick(模型切换走 ComposerModelPill)。
 */

import * as React from 'react';
import { cn } from '@chayuan/ui';
import type { ComposerModeConfig } from './composerMode';

export const ComposerModeBadge: React.FC<{
  mode: ComposerModeConfig;
  /** chat 默认模式可以选择隐藏(避免占据空间) */
  hideWhenChat?: boolean;
  className?: string;
}> = ({ mode, hideWhenChat = true, className }) => {
  if (hideWhenChat && mode.capability === 'chat') return null;
  const Icon = mode.icon;
  return (
    <div
      role="note"
      aria-label={`当前模式:${mode.label}`}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] leading-tight',
        mode.colorClass,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5 flex-shrink-0" />
      <span className="font-medium">{mode.label}</span>
      {mode.hint && (
        <span className="ml-1 truncate opacity-80" title={mode.hint}>
          · {mode.hint}
        </span>
      )}
    </div>
  );
};
