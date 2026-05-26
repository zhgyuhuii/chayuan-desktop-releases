/**
 * <Keyframes> — CSP 安全地把一段 @keyframes CSS 注册进文档。
 *
 * 用来替换 homeFx 里 ``<style>{KEYFRAMES}</style>`` 这种运行时内联 <style>
 * 注入。详细原因见同目录 ``cssKeyframes.ts`` 顶部注释:打包后 Tauri 给
 * style-src 注 nonce,运行时 JS 创建的 <style> 被 CSP 拦 → 关键帧失效。
 *
 * 行为
 * ----
 * - 优先走 CSSOM(``document.adoptedStyleSheets``),不创建 <style> 元素,
 *   不受 CSP style-src 限制 —— 打包版动画因此恢复。
 * - CSSOM 不可用的环境回退渲染一个普通 <style>(dev / 老 WebView 兜底)。
 * - ``cssText`` 相同(同 ``dedupeKey``)只注册一次。
 *
 * 注意:CSSOM 注册是"只增不删" —— homeFx 的关键帧都是页面级常驻动画定义,
 * 注册一次长期有效即可;不为它做卸载清理(也避免多实例互相删规则)。
 * 喜鹊每次飞行生成的**唯一命名**关键帧也走这里:名字带递增 id 不会冲突,
 * 量极小(一次飞行一条),常驻无妨。
 */

import * as React from 'react';
import { registerKeyframes } from './cssKeyframes';

export interface KeyframesProps {
  /** 一段 CSS 文本,可含多条 @keyframes / @media 规则。 */
  cssText: string;
  /** 去重 key:同 key 只注册一次。喜鹊动态关键帧用带 id 的唯一 key。 */
  dedupeKey: string;
}

export const Keyframes: React.FC<KeyframesProps> = ({ cssText, dedupeKey }) => {
  // 初次渲染就尝试 CSSOM 注册;成功则不渲染任何 <style> 元素。
  // useState 惰性初始化保证每个 (cssText,dedupeKey) 只 register 一次。
  const [viaCssom] = React.useState(() => registerKeyframes(cssText, dedupeKey));
  if (viaCssom) return null;
  // 回退:CSSOM 不可用(理论上打包用的 WebView 都支持,这里仅兜底)。
  return <style>{cssText}</style>;
};
