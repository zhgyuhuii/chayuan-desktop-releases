/**
 * 主页特效层 — 全屏游动,但**叠在内容之下**不遮按钮。
 *
 * z 分层 (2026-05-20)
 * -------------------
 * 用户要求:特效全屏可见 + 不能盖住可点击的卡片/按钮。
 *
 * 实现:特效层**内嵌**在 HomePage 容器里(不 Portal),自身 ``position: fixed
 * inset:0`` 覆盖整个 viewport(摆脱 max-w-5xl 宽度夹持),但 ``z-index: 0``;
 * HomePage 容器加 Tailwind ``isolate`` 强制创建 stacking context — 这样:
 *   - 卡片/按钮 ``relative z-10`` 永远在 fx 上方 → 不被覆盖
 *   - fx 自身仍能横穿整个屏幕(fixed 定位逃出 max-w-5xl)
 *
 * 卡片本身有不透明背景,所以龙游过卡片下方时被"挡住"是预期效果 —
 * 用户在卡片间的空隙、页面边距、底部空白处看到龙金光闪过。
 *
 * 装载内容
 * -------
 * 1. AuspiciousClouds:祥云漂移
 * 2. AmbientParticles:背景金粒缓升
 * 3. LightSweep:斜光带 38s 周期
 * 4. MagpieFlock:喜鹊 V 字编队
 * 5. GoldDragon:中国龙穿屏盘旋
 * 6. Phoenix:凤凰螺旋上升
 *
 * 性能
 * ----
 * - 龙/凤凰的 rAF 只在 active 时启动;document.hidden 暂停
 * - prefers-reduced-motion 关全部
 */

import * as React from 'react';
import { GoldDragon } from './GoldDragon';
import { MagpieFlock } from './MagpieFlock';
import { Phoenix } from './Phoenix';
import { AuspiciousClouds } from './AuspiciousClouds';
import { StarrySky } from './StarrySky';
import { Keyframes } from './Keyframes';

export const HomeFxLayer: React.FC = () => {
  const [size, setSize] = React.useState(() => ({
    w: typeof window !== 'undefined' ? window.innerWidth : 0,
    h: typeof window !== 'undefined' ? window.innerHeight : 0,
  }));
  const [dragonKey, setDragonKey] = React.useState(0);
  const [phoenixKey, setPhoenixKey] = React.useState(0);

  // —— 视口尺寸:用 window 而非 ResizeObserver,确保跟 fixed inset:0 一致
  React.useEffect(() => {
    const onResize = () =>
      setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // —— 龙首飞 4s + 凤凰首飞 28s
  React.useEffect(() => {
    if (size.w <= 0 || size.h <= 0) return;
    const t1 = setTimeout(() => setDragonKey((n) => n + 1), 4000);
    const t2 = setTimeout(() => setPhoenixKey((n) => n + 1), 28000);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.w > 0 && size.h > 0]);

  const onDragonDone = React.useCallback(() => {
    const next = 60_000 + Math.random() * 50_000;
    setTimeout(() => setDragonKey((n) => n + 1), next);
  }, []);

  const onPhoenixDone = React.useCallback(() => {
    const next = 90_000 + Math.random() * 60_000;
    setTimeout(() => setPhoenixKey((n) => n + 1), next);
  }, []);

  return (
    <div
      aria-hidden
      style={{
        position: 'fixed',
        inset: 0,
        pointerEvents: 'none',
        overflow: 'hidden',
        // z:0 = HomePage 内嵌 isolate 上下文里的底层;卡片 relative z-10 永远
        // 浮在上面。卡片自身的不透明背景会"挡住"经过下方的龙身/凤翼,只在
        // 卡片之间的空隙和页面边距处看到金光闪过。
        zIndex: 0,
      }}
    >
      {/* 星空在最底层 — 满屏 4 角星 + 银河斜带,作"宇宙背景" */}
      <StarrySky />
      <AuspiciousClouds />
      <AmbientParticles />
      <LightSweep />
      <MagpieFlock />
      {size.w > 0 && size.h > 0 && (
        <>
          <GoldDragon
            flightKey={dragonKey}
            width={size.w}
            height={size.h}
            onFlightDone={onDragonDone}
          />
          <Phoenix
            flightKey={phoenixKey}
            width={size.w}
            height={size.h}
            onFlightDone={onPhoenixDone}
          />
        </>
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 光影 sweep — 一道斜光带 30~50s 扫过一次,4s 全程
// ──────────────────────────────────────────────────────────────

const LightSweep: React.FC = () => (
  <div
    aria-hidden
    style={{
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      height: '100%',
      pointerEvents: 'none',
      overflow: 'hidden',
    }}
  >
    <div
      style={{
        position: 'absolute',
        top: '-20%',
        left: '-30%',
        width: '40%',
        height: '160%',
        background:
          'linear-gradient(115deg, transparent 0%, rgba(255, 215, 90, 0.06) 35%, rgba(255, 235, 160, 0.18) 50%, rgba(255, 215, 90, 0.06) 65%, transparent 100%)',
        transform: 'rotate(15deg)',
        filter: 'blur(2px)',
        animation: 'cy-light-sweep 38s ease-in-out infinite',
        mixBlendMode: 'screen',
      }}
    />
    {/* @keyframes 走 CSSOM 注册(CSP 安全),不用运行时 <style> 元素。 */}
    <Keyframes cssText={LIGHT_SWEEP_KEYFRAMES} dedupeKey="cy-light-sweep" />
  </div>
);

const LIGHT_SWEEP_KEYFRAMES = `
@keyframes cy-light-sweep {
  0%, 88% { transform: translateX(-30%) rotate(15deg); opacity: 0; }
  90% { opacity: 1; }
  94% { transform: translateX(280%) rotate(15deg); opacity: 1; }
  96%, 100% { transform: translateX(330%) rotate(15deg); opacity: 0; }
}
@keyframes cy-particle-float {
  0% { transform: translateY(0) scale(1); opacity: 0; }
  20% { opacity: 0.8; }
  80% { opacity: 0.8; }
  100% { transform: translateY(-130vh) scale(0.4); opacity: 0; }
}
`;

// ──────────────────────────────────────────────────────────────
// 背景金色微粒 — 12 颗,从屏幕底部缓缓上升
// ──────────────────────────────────────────────────────────────

const AmbientParticles: React.FC = React.memo(() => {
  const particles = React.useMemo(
    () =>
      Array.from({ length: 12 }, (_, i) => ({
        left: `${5 + ((i * 8.3) % 90)}%`,
        size: 3 + (i % 3) * 1.5,
        delay: `${i * 1.6}s`,
        duration: `${22 + (i % 4) * 4}s`,
      })),
    [],
  );

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
      {particles.map((p, i) => (
        <span
          // biome-ignore lint/suspicious/noArrayIndexKey: 数量固定
          key={i}
          style={{
            position: 'absolute',
            bottom: 0,
            left: p.left,
            width: p.size,
            height: p.size,
            borderRadius: '50%',
            background: 'radial-gradient(circle, #fff5c2 0%, #ffd75a 50%, transparent 80%)',
            filter: 'blur(0.6px)',
            animation: `cy-particle-float ${p.duration} ${p.delay} linear infinite`,
            mixBlendMode: 'screen',
          }}
        />
      ))}
    </div>
  );
});
AmbientParticles.displayName = 'AmbientParticles';
