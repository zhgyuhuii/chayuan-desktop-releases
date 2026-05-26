/**
 * 满屏四角星空 + 银河斜带。
 *
 * 视觉
 * ----
 * 1. **180 颗主星**:大小从 1px(碎钻)到 9px(主芒),每颗都是尖锐四角
 *    星形(CSS ``clip-path`` polygon),大星附带 drop-shadow 长芒辉光
 * 2. **130 颗银河星**:沿对角线斜带(135°方向)密集分布,小颗粒模拟星尘
 * 3. **银河 haze**:同方向 linear-gradient 软渐变打底,蓝-金-蓝过渡
 * 4. **冷暖混色**:55% 暖金(#fff5c2 / #ffd75a / #ffaa3a)、35% 冷冰蓝
 *    (#e0f0ff / #aaccff / #88aaff)、10% 纯白点缀
 * 5. **闪烁动画**:每颗 opacity twinkle 2~5s 周期,随机相位,自然错落
 *
 * 性能
 * ----
 * - 所有星都是 ``<div>`` + ``clip-path`` + CSS animation,无 rAF
 * - 位置用确定性 PRNG(mulberry32 + seed)生成,每次 mount 位置一致
 * - ``mix-blend-mode: screen`` 让星光跟下方背景自然叠加
 * - prefers-reduced-motion 时不闪烁(但星点保留)
 */

import * as React from 'react';
import { Keyframes } from './Keyframes';

interface Star {
  x: number;        // 0~100, vw 百分比
  y: number;        // 0~100, vh 百分比
  size: number;     // px
  color: string;
  twinkleDelay: number;
  twinkleDur: number;
  withRays: boolean;
}

// 8 角星 clip-path:4 主角(0/90/180/270)长 + 4 副角(45°)短 → 锐利四芒
const SHARP_STAR_CLIP =
  'polygon(50% 0%, 58% 42%, 100% 50%, 58% 58%, 50% 100%, 42% 58%, 0% 50%, 42% 42%)';

const COLORS_WARM = ['#fff5c2', '#ffd75a', '#ffaa3a'] as const;
const COLORS_COOL = ['#e0f0ff', '#aaccff', '#88aaff'] as const;
const COLORS_WHITE = ['#ffffff', '#f0f5ff'] as const;

// 确定性 PRNG — 同 seed 出同样位置,避免 React 重渲染时星位置抖动
function makeRng(seed: number): () => number {
  let state = seed | 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pickColor(rand: () => number): string {
  const r = rand();
  if (r < 0.55) {
    return COLORS_WARM[Math.floor(rand() * COLORS_WARM.length)] as string;
  }
  if (r < 0.9) {
    return COLORS_COOL[Math.floor(rand() * COLORS_COOL.length)] as string;
  }
  return COLORS_WHITE[Math.floor(rand() * COLORS_WHITE.length)] as string;
}

function generateMainStars(count: number, seed: number): Star[] {
  const rand = makeRng(seed);
  const out: Star[] = [];
  for (let i = 0; i < count; i++) {
    // 大小分布:85% 小星(1~2.5px),15% 大星(4~9px)— 大星点缀,密集会糊
    const size = rand() < 0.85 ? 1 + rand() * 1.5 : 4 + rand() * 5;
    out.push({
      x: rand() * 100,
      y: rand() * 100,
      size,
      color: pickColor(rand),
      twinkleDelay: rand() * 5,
      twinkleDur: 2 + rand() * 3.5,
      withRays: size > 3.5,
    });
  }
  return out;
}

function generateGalaxyStars(count: number, seed: number): Star[] {
  const rand = makeRng(seed);
  const out: Star[] = [];
  // 银河带:从左上 (10, 15) 到右下 (90, 90),沿带散布,带宽 ±12%
  for (let i = 0; i < count; i++) {
    const t = rand();
    const baseX = 10 + t * 80;
    const baseY = 15 + t * 75;
    // Box-Muller 取近似高斯分布,做带宽散布
    const u1 = Math.max(rand(), 1e-6);
    const u2 = rand();
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const spread = z * 6.5; // ±~13%
    // 带方向 (1, 0.94),垂直方向归一 (-0.94, 1)/√(0.88+1)
    const denom = Math.sqrt(0.94 * 0.94 + 1);
    const perpX = -0.94 / denom;
    const perpY = 1 / denom;
    out.push({
      x: Math.max(0, Math.min(100, baseX + spread * perpX)),
      y: Math.max(0, Math.min(100, baseY + spread * perpY)),
      size: 0.8 + rand() * 1.4,
      color: rand() < 0.55 ? '#fff5c2' : '#e0f0ff',
      twinkleDelay: rand() * 5,
      twinkleDur: 2 + rand() * 3,
      withRays: false,
    });
  }
  return out;
}

export const StarrySky: React.FC = React.memo(() => {
  const mainStars = React.useMemo(() => generateMainStars(180, 42), []);
  const galaxyStars = React.useMemo(() => generateGalaxyStars(130, 7), []);

  return (
    <div
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        overflow: 'hidden',
      }}
    >
      {/* —— 银河 haze 软渐变(对角带,模拟银河尘埃) */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'linear-gradient(135deg, transparent 22%, rgba(170,200,255,0.07) 38%, rgba(255,230,160,0.07) 50%, rgba(170,200,255,0.07) 62%, transparent 78%)',
          filter: 'blur(4px)',
          mixBlendMode: 'screen',
        }}
      />

      {/* —— 银河带密集小星 */}
      {galaxyStars.map((s, i) => (
        <StarDot
          // biome-ignore lint/suspicious/noArrayIndexKey: 数量 + 顺序固定
          key={`g${i}`}
          star={s}
        />
      ))}

      {/* —— 主星(分布全屏,有大有小) */}
      {mainStars.map((s, i) => (
        <StarDot
          // biome-ignore lint/suspicious/noArrayIndexKey: 数量 + 顺序固定
          key={`m${i}`}
          star={s}
        />
      ))}

      {/* @keyframes 走 CSSOM 注册(CSP 安全),不用运行时 <style> 元素。 */}
      <Keyframes cssText={STAR_KEYFRAMES} dedupeKey="cy-star-twinkle" />
    </div>
  );
});
StarrySky.displayName = 'StarrySky';

// 单颗星 — 用 clip-path 切出四角星形,drop-shadow 辉光
const StarDot: React.FC<{ star: Star }> = React.memo(({ star }) => {
  // 大星 box-shadow 长芒:两层光晕 — 紧贴的强光 + 外圈柔光
  const glow = star.withRays
    ? `drop-shadow(0 0 ${star.size * 0.6}px ${star.color}) drop-shadow(0 0 ${star.size * 2.2}px ${star.color}aa)`
    : `drop-shadow(0 0 ${star.size * 0.5}px ${star.color}cc)`;

  return (
    <span
      style={{
        position: 'absolute',
        left: `${star.x}%`,
        top: `${star.y}%`,
        width: star.size,
        height: star.size,
        background: star.color,
        clipPath: SHARP_STAR_CLIP,
        WebkitClipPath: SHARP_STAR_CLIP,
        transform: 'translate(-50%, -50%)',
        filter: glow,
        animation: `cy-star-twinkle ${star.twinkleDur}s ${star.twinkleDelay}s ease-in-out infinite`,
        mixBlendMode: 'screen',
      }}
    />
  );
});
StarDot.displayName = 'StarDot';

const STAR_KEYFRAMES = `
@keyframes cy-star-twinkle {
  0%, 100% { opacity: 0.3; transform: translate(-50%, -50%) scale(0.92); }
  50%      { opacity: 1;   transform: translate(-50%, -50%) scale(1.08); }
}
@media (prefers-reduced-motion: reduce) {
  span[style*="cy-star-twinkle"] { animation: none !important; }
}
`;
