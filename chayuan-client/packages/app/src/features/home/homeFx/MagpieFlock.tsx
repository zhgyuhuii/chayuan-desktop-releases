/**
 * 喜鹊群飞 — 3 只小鸟成 V 字编队,随机全屏穿越主页。
 *
 * 2026-05-20 改:旧版只在顶部 8~28% 区间横向直线掠过,被吐槽"局限于一个
 * 区域"。改为每次随机挑两个锚点(四角 / 四边中点 / 中心),沿一条带弧度的
 * 路径斜穿全屏 —— 左上→右下、右上→右下、右下→左上、中心→左下…各种方向
 * 都可能出现。机头朝飞行方向旋转,两端淡入淡出。
 *
 * 跟金龙的关系
 * ------------
 * 金龙是"主秀",占据视觉中心。喜鹊是"配菜",一掠而过,提供持续活力感。
 *
 * 实现
 * ----
 * - 3 只 SVG 鸟,V 字编队(领队居中,僚机左右后置)
 * - 翅膀 SVG `<animateTransform>` 拍频 0.18s/cycle
 * - 每次飞行随机生成 start/mid/end 三点 + 唯一 `@keyframes`,编队整体沿
 *   关键帧 translate + rotate(机头转向)
 * - 触发器:首次 12s,之后 26~46s 随机
 */

import * as React from 'react';
import { Keyframes } from './Keyframes';

export const MagpieFlock: React.FC = () => {
  const reducedMotion = useReducedMotion();
  const [flight, setFlight] = React.useState<FlightParams | null>(null);

  React.useEffect(() => {
    if (reducedMotion) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let id = 0;
    const schedule = (delayMs: number) => {
      timer = setTimeout(() => {
        id += 1;
        setFlight(makeFlightParams(id));
        // 安排下一次
        schedule(26_000 + Math.random() * 20_000);
      }, delayMs);
    };
    schedule(12_000); // 首飞延迟 12s,跟龙错开
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [reducedMotion]);

  if (reducedMotion || !flight) return null;

  return (
    // 编队容器从 (0,0) 出发,完全靠关键帧 translate 把它送到随机起点 → 终点。
    // transformOrigin 设在领队鸟身中心,rotate 时僚机自然绕领队甩动。
    <div
      key={flight.id}
      aria-hidden
      style={{
        position: 'absolute',
        left: 0,
        top: 0,
        width: 80,
        height: 60,
        transformOrigin: '18px 10px',
        pointerEvents: 'none',
        animation: `${flight.animName} ${flight.durationSec}s ease-in-out forwards`,
      }}
    >
      {/* V 字编队:领队、左僚机、右僚机 */}
      <Magpie size={1.0} dx={0} dy={0} flapDelay="0s" />
      <Magpie size={0.78} dx={-22} dy={14} flapDelay="0.06s" />
      <Magpie size={0.78} dx={-22} dy={-14} flapDelay="0.12s" />
      {/* 每次飞行的唯一命名 @keyframes — 走 CSSOM 注册(CSP 安全)。
          dedupeKey 带 flight.id,每程一条互不冲突。 */}
      <Keyframes cssText={flight.keyframes} dedupeKey={`cy-magpie-fly-${flight.id}`} />
    </div>
  );
};

interface FlightParams {
  id: number;
  animName: string;
  keyframes: string;
  durationSec: number;
}

/**
 * 随机生成一次全屏飞行:四角 / 四边中点 / 中心 里随机挑起点 + 终点(够远),
 * 中段加垂直偏移做出弧线,机头按两段朝向旋转,两端 opacity 淡入淡出。
 */
function makeFlightParams(id: number): FlightParams {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;
  const M = 160; // 屏外余量 — 角 / 边锚点放屏外,鸟从屏外飞入飞出

  const anchors: ReadonlyArray<{ x: number; y: number }> = [
    { x: -M,     y: -M     }, // 左上
    { x: vw + M, y: -M     }, // 右上
    { x: -M,     y: vh + M }, // 左下
    { x: vw + M, y: vh + M }, // 右下
    { x: vw / 2, y: -M     }, // 上中
    { x: vw / 2, y: vh + M }, // 下中
    { x: -M,     y: vh / 2 }, // 左中
    { x: vw + M, y: vh / 2 }, // 右中
    { x: vw / 2, y: vh / 2 }, // 中心
  ];
  const pick = () => anchors[Math.floor(Math.random() * anchors.length)]!;

  const s = pick();
  let e = pick();
  // 起终点要够远(至少跨过大半屏),否则重挑
  const minDist = Math.max(vw, vh) * 0.55;
  for (let guard = 0; guard < 12; guard += 1) {
    if (Math.hypot(e.x - s.x, e.y - s.y) >= minDist) break;
    e = pick();
  }

  const dx = e.x - s.x;
  const dy = e.y - s.y;
  const len = Math.hypot(dx, dy) || 1;
  // 中段沿垂直方向偏移 → 弧线;偏移量随路径长度,封顶 260px
  const arc = (Math.random() - 0.5) * Math.min(len * 0.3, 260);
  const midX = (s.x + e.x) / 2 + (-dy / len) * arc;
  const midY = (s.y + e.y) / 2 + (dx / len) * arc;

  // 两段朝向(度);机头按此旋转 —— 鸟 SVG 默认朝 +X
  const h1 = (Math.atan2(midY - s.y, midX - s.x) * 180) / Math.PI;
  let h2 = (Math.atan2(e.y - midY, e.x - midX) * 180) / Math.PI;
  // 防止 rotate 插值绕远路(如 170° ↔ -170°)
  while (h2 - h1 > 180) h2 -= 360;
  while (h2 - h1 < -180) h2 += 360;
  const hMid = (h1 + h2) / 2;

  const durationSec = Math.min(13, Math.max(6, len / 230));
  const animName = `cy-magpie-fly-${id}`;
  const keyframes = `
@keyframes ${animName} {
  0%   { transform: translate(${s.x.toFixed(0)}px, ${s.y.toFixed(0)}px) rotate(${h1.toFixed(1)}deg); opacity: 0; }
  10%  { opacity: 1; }
  50%  { transform: translate(${midX.toFixed(0)}px, ${midY.toFixed(0)}px) rotate(${hMid.toFixed(1)}deg); }
  90%  { opacity: 1; }
  100% { transform: translate(${e.x.toFixed(0)}px, ${e.y.toFixed(0)}px) rotate(${h2.toFixed(1)}deg); opacity: 0; }
}
`;
  return { id, animName, keyframes, durationSec };
}

interface MagpieProps {
  size: number;
  dx: number;
  dy: number;
  flapDelay: string;
}

const Magpie: React.FC<MagpieProps> = ({ size, dx, dy, flapDelay }) => {
  // 喜鹊配色:近黑头身 + 白腹 + 长尾。视口里只有几像素大,细节弱化。
  return (
    <svg
      width={36 * size}
      height={24 * size}
      viewBox="-18 -10 36 24"
      style={{
        position: 'absolute',
        left: dx,
        top: dy,
        filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.25))',
      }}
    >
      {/* 身体 */}
      <ellipse cx="-2" cy="0" rx="8" ry="3" fill="#1a1a2e" />
      {/* 白腹 */}
      <ellipse cx="-3" cy="1.5" rx="5" ry="1.6" fill="#f5f5f5" opacity="0.92" />
      {/* 头 */}
      <circle cx="6" cy="-1.5" r="2.6" fill="#1a1a2e" />
      {/* 喙 */}
      <path d="M 8,-1.5 L 11,-1 L 8,-0.2 Z" fill="#d49b1a" />
      {/* 眼 */}
      <circle cx="6.5" cy="-2" r="0.5" fill="#fff5c2" />
      {/* 尾 */}
      <path d="M -10,0 L -16,-2 L -16,2 Z" fill="#1a1a2e" />
      {/* 翅膀 — 拍翼用 animateTransform 在原点旋转 */}
      <g transform="translate(0, -2)">
        <path d="M 0,0 Q -5,-8 -12,-8 Q -8,-3 -2,-1 Z" fill="#2a2a4e">
          <animateTransform
            attributeName="transform"
            type="rotate"
            values="-15;-55;-15"
            keyTimes="0;0.5;1"
            dur="0.18s"
            begin={flapDelay}
            repeatCount="indefinite"
          />
        </path>
      </g>
    </svg>
  );
};

function useReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mql.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener?.('change', handler);
    return () => mql.removeEventListener?.('change', handler);
  }, []);
  return reduced;
}
