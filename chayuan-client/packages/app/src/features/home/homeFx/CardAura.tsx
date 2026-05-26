/**
 * 卡片缠绕光环 — 沿卡片矩形周长流动的金色虚线 + 角落的发光小点。
 *
 * 视觉
 * ----
 * "龙盘柱"的小型化实现:每张功能卡边框跑一条金色虚线,4 段 stroke-dasharray
 * 用 stroke-dashoffset 动画推着它绕一圈,看起来像金线沿卡片边缠绕。同时四个
 * 角落各有一颗呼吸的金色光点。卡片 hover 时整体亮度提升 1.6 倍。
 *
 * 用法
 * ----
 * ```tsx
 * <button className="relative ...">
 *   <CardAura />
 *   {content}
 * </button>
 * ```
 * 父容器必须 `position: relative`,本组件 absolute inset:0 自动贴合。
 * `pointer-events: none`,不挡点击。
 *
 * 性能:纯 CSS 动画(SVG stroke-dashoffset),60fps GPU 加速,无 rAF。
 */

import * as React from 'react';
import { Keyframes } from './Keyframes';

export interface CardAuraProps {
  /** 颜色基调 — 默认金色,卡片想要冷色调可传 ``#7dd3fc`` 之类 */
  hue?: 'gold' | 'cyan';
  /** 强度 — ``subtle`` 默认 opacity 0.35;``vivid`` 用 0.7。 */
  intensity?: 'subtle' | 'vivid';
  /** 圆角值 — 跟卡片本身 ``rounded-2xl`` (16px) 对齐;别的传具体 px */
  radius?: number;
}

const PALETTE = {
  gold: {
    stroke: '#ffd75a',
    glow: '#fff5c2',
    dot: '#ffeaa0',
  },
  cyan: {
    stroke: '#7dd3fc',
    glow: '#e0f2fe',
    dot: '#bae6fd',
  },
};

export const CardAura: React.FC<CardAuraProps> = React.memo(({
  hue = 'gold',
  intensity = 'subtle',
  radius = 16,
}) => {
  const c = PALETTE[hue];
  const baseOpacity = intensity === 'vivid' ? 0.7 : 0.35;

  return (
    <span
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        borderRadius: radius,
        overflow: 'hidden',
      }}
    >
      <svg
        width="100%"
        height="100%"
        style={{ position: 'absolute', inset: 0, overflow: 'visible' }}
        preserveAspectRatio="none"
      >
        <defs>
          <filter id={`cy-aura-glow-${hue}`} x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="2.5" />
          </filter>
        </defs>
        {/* 流动的金线 — width/height 100% 经 vector-effect 适配父尺寸 */}
        <rect
          x="1"
          y="1"
          width="calc(100% - 2px)"
          height="calc(100% - 2px)"
          rx={radius - 1}
          ry={radius - 1}
          fill="none"
          stroke={c.stroke}
          strokeWidth="1.4"
          strokeDasharray="22 180"
          opacity={baseOpacity}
          filter={`url(#cy-aura-glow-${hue})`}
          style={{
            animation: 'cy-aura-trace 4.5s linear infinite',
          }}
        />
        {/* 第二条反向流,错开相位,模拟 2 根金线缠绕 */}
        <rect
          x="1"
          y="1"
          width="calc(100% - 2px)"
          height="calc(100% - 2px)"
          rx={radius - 1}
          ry={radius - 1}
          fill="none"
          stroke={c.glow}
          strokeWidth="0.8"
          strokeDasharray="10 220"
          opacity={baseOpacity * 0.7}
          style={{
            animation: 'cy-aura-trace-rev 5.2s linear infinite',
          }}
        />
      </svg>
      {/* 四角呼吸光点 */}
      {[
        { top: 4, left: 4 },
        { top: 4, right: 4 },
        { bottom: 4, left: 4 },
        { bottom: 4, right: 4 },
      ].map((pos, i) => (
        <span
          // biome-ignore lint/suspicious/noArrayIndexKey: 固定 4 个
          key={i}
          style={{
            position: 'absolute',
            width: 4,
            height: 4,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${c.dot} 0%, ${c.stroke} 60%, transparent 90%)`,
            filter: 'blur(0.4px)',
            opacity: baseOpacity * 0.9,
            animation: `cy-aura-pulse 2.6s ${i * 0.6}s ease-in-out infinite`,
            ...pos,
          }}
        />
      ))}
      {/* @keyframes 走 CSSOM 注册(CSP 安全),不用运行时 <style> 元素。
          这里同时定义了 HomePage logo/图标用的 cy-badge-halo / cy-icon-float。 */}
      <Keyframes cssText={AURA_KEYFRAMES} dedupeKey="cy-card-aura" />
    </span>
  );
});
CardAura.displayName = 'CardAura';

const AURA_KEYFRAMES = `
@keyframes cy-aura-trace {
  0%   { stroke-dashoffset: 0; }
  100% { stroke-dashoffset: -202; }
}
@keyframes cy-aura-trace-rev {
  0%   { stroke-dashoffset: 0; }
  100% { stroke-dashoffset: 230; }
}
@keyframes cy-aura-pulse {
  0%, 100% { transform: scale(1); opacity: 0.35; }
  50%      { transform: scale(1.6); opacity: 0.9; }
}
/* 卡片图标的待机动画 — 轻浮 + 微旋 + 光晕呼吸,各卡用 animation-delay 错相 */
@keyframes cy-icon-float {
  0%, 100% { transform: translateY(0) rotate(0deg); filter: drop-shadow(0 0 0 transparent); }
  35%      { transform: translateY(-3px) rotate(-3deg); filter: drop-shadow(0 0 6px rgba(255, 215, 90, 0.55)); }
  65%      { transform: translateY(-2px) rotate(3deg); filter: drop-shadow(0 0 6px rgba(255, 215, 90, 0.45)); }
}
/* AI/logo 徽章光晕 — 圆形脉冲 box-shadow,3.6s 一拍,跟 icon-float 错相 */
@keyframes cy-badge-halo {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255, 215, 90, 0), 0 0 0 0 rgba(255, 215, 90, 0); }
  50%      { box-shadow: 0 0 18px 4px rgba(255, 215, 90, 0.55), 0 0 4px 1px rgba(255, 245, 194, 0.7); }
}
`;
