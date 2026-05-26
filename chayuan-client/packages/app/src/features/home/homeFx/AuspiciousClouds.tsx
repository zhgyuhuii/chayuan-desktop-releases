/**
 * 祥云飘动 — 3 朵金边卷云从右向左缓慢移动,衬底用。
 *
 * 设计
 * ----
 * 1. 中国传统祥云的"卷头如意"轮廓,SVG path 手工画。
 * 2. 三朵不同尺寸/位置/速度(45~70s 一程),错相位飘过整个屏幕。
 * 3. opacity 极低(0.12~0.2)+ blur(2px) → 不抢戏,仅作"东方氛围"
 *    打底。前景龙凤喜鹊路过时会盖在云之上。
 * 4. mix-blend-mode: screen → 在深色背景上呈现金光,在浅色背景上几乎透明。
 *
 * 性能:纯 CSS keyframe translateX,GPU,无 rAF。
 */

import * as React from 'react';
import { Keyframes } from './Keyframes';

export const AuspiciousClouds: React.FC = () => (
  <div
    aria-hidden
    style={{
      position: 'absolute',
      inset: 0,
      pointerEvents: 'none',
      overflow: 'hidden',
      mixBlendMode: 'screen',
    }}
  >
    <Cloud size={1.2} top="14%" duration={62} delay={0} opacity={0.18} />
    <Cloud size={0.85} top="38%" duration={48} delay={20} opacity={0.14} />
    <Cloud size={1.0} top="62%" duration={70} delay={42} opacity={0.16} />
    {/* @keyframes 走 CSSOM 注册(CSP 安全),不用运行时 <style> 元素。 */}
    <Keyframes cssText={CLOUD_KEYFRAMES} dedupeKey="cy-cloud-drift" />
  </div>
);

interface CloudProps {
  size: number;
  top: string;
  duration: number;
  delay: number;
  opacity: number;
}

const Cloud: React.FC<CloudProps> = ({ size, top, duration, delay, opacity }) => (
  <div
    style={{
      position: 'absolute',
      top,
      right: 0,
      width: 220 * size,
      height: 80 * size,
      opacity,
      animation: `cy-cloud-drift ${duration}s ${delay}s linear infinite`,
      filter: 'blur(1.5px) drop-shadow(0 0 8px rgba(255, 215, 90, 0.4))',
    }}
  >
    <svg viewBox="0 0 220 80" width="100%" height="100%">
      <defs>
        <linearGradient id={`cloud-${size}-${top}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#ffeaa0" stopOpacity="0.95" />
          <stop offset="60%" stopColor="#d49b1a" stopOpacity="0.85" />
          <stop offset="100%" stopColor="#7a4a06" stopOpacity="0.7" />
        </linearGradient>
      </defs>
      {/* 祥云轮廓:卷头如意 + 主体翻卷 + 尾部回钩
          手工 path,贴近清代云纹的笔意 */}
      <path
        d="
          M 20,50
          C 8,52 4,40 14,32
          C 8,18 22,8 36,16
          C 38,4 56,2 64,14
          C 76,4 96,10 96,26
          C 110,18 132,22 134,38
          C 152,30 174,38 174,54
          C 184,52 196,58 192,68
          C 184,76 168,72 158,66
          C 148,72 130,68 124,58
          C 108,64 88,60 84,48
          C 70,54 50,52 44,42
          C 34,52 24,54 20,50 Z
        "
        fill={`url(#cloud-${size}-${top})`}
        stroke="#ffd75a"
        strokeWidth="0.6"
        strokeOpacity="0.5"
      />
      {/* 中间点缀:小卷云 */}
      <path
        d="M 100,50 C 96,46 100,40 106,42 C 108,38 114,40 114,46 C 118,46 120,52 114,54 C 110,58 102,56 100,50 Z"
        fill={`url(#cloud-${size}-${top})`}
        opacity="0.75"
      />
    </svg>
  </div>
);

const CLOUD_KEYFRAMES = `
@keyframes cy-cloud-drift {
  0%   { transform: translateX(0); }
  100% { transform: translateX(calc(-100vw - 220px)); }
}
`;
