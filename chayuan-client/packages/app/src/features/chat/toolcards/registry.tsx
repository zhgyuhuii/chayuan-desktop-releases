/**
 * 工具卡注册中心。
 *
 * 思路：所有工具卡都是 React 组件，按 `tool.name` 注册到一个 map。
 * MessageBubble 渲染时按名查找；找不到 → fallback。
 *
 * 一致性：每张卡都接受 `<ToolCallProps>`：
 *   { name, args, result, status }
 * 由调用方解析（`args` 已 JSON.parse 失败就保留 string）。
 *
 * 组件按需懒加载，避免首屏拉取所有卡片代码。
 */

import * as React from 'react';
import { GenericToolCard } from './GenericToolCard';
import { WebSearchCard } from './WebSearchCard';
import { WeatherCard } from './WeatherCard';
import { CalculatorCard } from './CalculatorCard';

export interface ToolCallProps {
  name: string;
  args?: unknown;
  result?: unknown;
  status?: 'start' | 'delta' | 'end';
  /**
   * 嵌入模式 — 由外层 ToolCallsRow 提供 chevron + 名称 + 状态时,
   * 卡片自己跳过外层圆角壳和重复的 name/status 头,只渲染主体内容。
   * 默认 false:仍按独立卡片渲染,保留对老调用点的零回归。
   */
  embedded?: boolean;
}

export type ToolCard = React.FC<ToolCallProps>;

const REGISTRY = new Map<string, ToolCard>();

/** 注册一张卡，name 不区分大小写、支持精确名 */
export function registerToolCard(name: string, card: ToolCard): void {
  REGISTRY.set(name.toLowerCase(), card);
}

/** 模糊匹配（包含子串）；用于 web_search / amap_search 这类家族 */
const PATTERNS: Array<{ test: (name: string) => boolean; card: ToolCard }> = [];
export function registerToolCardPattern(pattern: RegExp, card: ToolCard): void {
  PATTERNS.push({ test: (name) => pattern.test(name), card });
}

export function resolveToolCard(name: string): ToolCard {
  const exact = REGISTRY.get(name.toLowerCase());
  if (exact) return exact;
  for (const p of PATTERNS) {
    if (p.test(name)) return p.card;
  }
  return GenericToolCard;
}

// ── 内置注册 ────────────────────────────────────────────────────
registerToolCardPattern(/(^|_)(web_)?search($|_)/i, WebSearchCard);
registerToolCardPattern(/(^|_)weather($|_)/i, WeatherCard);
registerToolCardPattern(/(^|_)(calculate|calculator|calc|math)($|_)/i, CalculatorCard);

export const ToolCallCard: React.FC<ToolCallProps> = (props) => {
  const Card = resolveToolCard(props.name);
  return <Card {...props} />;
};
