/**
 * 主页"按模型类型开始对话"单卡组件 — ``React.memo`` 包,只在数据变化时重渲。
 *
 * 两种状态:
 *   - 已就绪(``modelCount >= 1``):整卡高亮 + 显示"x 个模型可用 · 点击新对话"
 *   - 未配置(``modelCount === 0``):整卡灰度 + 显示 empty_hint + "如何接入"按钮
 *
 * 颜色:取 ``getCapabilityMeta(cap).classes`` 的同一份 tailwind 调色板,跟
 * ChatComposer 顶部 mode badge / 选择器 chip 一致 — 用户在不同界面看到同一种色
 * 立刻能识别"这是文生图 / 语音合成"。
 *
 * 性能:
 *   - ``React.memo`` + props 简单值(cap / modelCount / disabled) → diff 廉价
 *   - onActivate / onShowHelp 必须由父级用 ``useCallback`` 稳定,否则 memo 失效
 *
 * 可访问性:
 *   - 整卡是 ``button``,Enter/Space 触发同 click
 *   - 未配置态 ``aria-disabled=true``,但仍可聚焦 + 触发 onShowHelp 拉帮助
 */

import * as React from 'react';
import { Sparkles } from 'lucide-react';
import { cn } from '@chayuan/ui';
import type { ModalityCapability } from '@chayuan/transport';
import {
  CapabilityChip,
  getCapabilityMeta,
} from '../../composer/modelCapabilityChip';
import type { CapabilityCardSpec } from './capabilityCardCatalog';
import { CardAura } from '../homeFx/CardAura';

export interface CapabilityCardProps {
  spec: CapabilityCardSpec;
  /** 当前 capability 下可用模型数;0 = 未配置 */
  modelCount: number;
  /** 已就绪时点击 — 父级保证 useCallback 稳定 */
  onActivate(cap: ModalityCapability): void;
  /** 未配置时点击 — 弹帮助面板并锚到对应 cap 段 */
  onShowHelp(cap: ModalityCapability): void;
}


const CapabilityCardBase: React.FC<CapabilityCardProps> = ({
  spec, modelCount, onActivate, onShowHelp,
}) => {
  const meta = getCapabilityMeta(spec.cap);
  const ready = modelCount > 0;

  const handleClick = React.useCallback(() => {
    if (ready) onActivate(spec.cap);
    else onShowHelp(spec.cap);
  }, [ready, onActivate, onShowHelp, spec.cap]);

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-disabled={!ready}
      title={
        ready
          ? `${meta.longLabel} — ${modelCount} 个模型可用 · 点击新建对话`
          : `${meta.longLabel} — 未配置 · 点击查看如何接入`
      }
      className={cn(
        'group relative flex h-36 flex-col gap-2 overflow-hidden rounded-2xl border p-4 text-left transition-all',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cy-brand-500)]',
        ready
          ? 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] hover:-translate-y-0.5 hover:shadow-[var(--cy-shadow-md)]'
          : 'border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] opacity-80 hover:opacity-100',
      )}
    >
      {/* 顶行:capability chip(色块) + 状态(可用模型数 / 未配置) */}
      <div className="flex items-center justify-between gap-2">
        <CapabilityChip cap={spec.cap} />
        {ready ? (
          <span className="text-[10px] text-[var(--cy-text-tertiary)]">
            {modelCount} 个模型
          </span>
        ) : (
          <span className="rounded-full bg-amber-50 px-2 py-px text-[10px] font-medium text-amber-700 ring-1 ring-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:ring-amber-800/40">
            未配置
          </span>
        )}
      </div>

      {/* 中间:标题 + 副标题 */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-semibold text-[var(--cy-text-primary)]">
          {spec.title}
        </p>
        <p className="mt-1 line-clamp-2 text-xs text-[var(--cy-text-secondary)]">
          {spec.subtitle}
        </p>
      </div>

      {/* 底行:hint / empty_hint */}
      <p
        className={cn(
          'line-clamp-1 text-[11px]',
          ready ? 'text-[var(--cy-text-tertiary)]' : 'text-amber-700/80 dark:text-amber-300/80',
        )}
      >
        {ready ? spec.hint : spec.empty_hint}
      </p>

      {/* 已就绪 hover 出现的"新对话"图标提示 */}
      {ready && (
        <Sparkles
          aria-hidden
          className="pointer-events-none absolute right-3 bottom-3 h-3.5 w-3.5 text-[var(--cy-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-80"
        />
      )}
      {/* 金线缠绕 — 只在已就绪态加,未配卡(灰、虚边)不打扰 */}
      {ready && <CardAura hue="gold" intensity="subtle" radius={16} />}
    </button>
  );
};

CapabilityCardBase.displayName = 'CapabilityCard';

export const CapabilityCard = React.memo(CapabilityCardBase);
