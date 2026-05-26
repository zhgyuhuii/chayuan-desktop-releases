/**
 * 金龙 — 全屏盘旋游动动画(写实级升级版,2026-05-20)。
 *
 * 升级要点
 * --------
 * 旧版:24 段离散椭圆,看起来像"串珠"。新版:
 *   1. **连续 body outline 路径**:36 段位置驱动 Catmull-Rom 平滑曲线,
 *      用 quadratic bezier 串成一条带 taper(头粗尾细)的封闭路径,无段感
 *   2. **金属鳞片簇**:每段贴 3 小片菱形鳞,头部密尾部稀,SVG `path` 模拟
 *      鎏金鳞甲反光
 *   3. **4 爪开张**:5、12、22、29 段挂腿,每爪 5 指(钩状)+ 关节,跟着身体
 *      扭动同步偏移
 *   4. **怒目张嘴**:DragonHead 重画,张嘴露獠牙,喉内能量光球(动态发光)
 *   5. **多束鬃毛 + 长须**:头后 5 缕鬃 + 6 根背鳍片状鬃毛(沿前 14 段),
 *      龙须 2 长 + 4 短
 *   6. **三层金色金属渐变**:深古铜 → 亮金 → 白金 高光,SMIL 流转 2.4s
 *      一圈,带"高光带"扫过整条龙的效果
 *   7. **辉光**:三层 feGaussianBlur(2/4/9 stdDev)叠加 → 龙身泛金雾 +
 *      外圈金红 halo
 *
 * follow-the-leader 物理引擎不变,只换渲染层。
 */

import * as React from 'react';
import { makeFlightPath, type FlightPath } from './dragonPath';

const SEG_COUNT = 48;
const SEG_LEN = 10; // 段间距 px;48×10 = 总长 ~480px(舒展) / 紧密盘旋时 ~380
const HEAD_WIDTH = 26;
const TRAIL_LEN = 16;
// 飞行结束后再拖 2s,让头沿出场方向匀速跑出屏外,身体节节跟着出去 —
// 否则 t=1 时头在屏边界、尾还在屏中,直接停掉会"残留半截龙"。
// 龙身加长后 EXIT 时间也要相应延长(本来 1.6s 够 36 段,48 段需要 ~2s)。
const EXIT_EXTENSION_MS = 2000;
const EXIT_VELOCITY_PXPS = 480;
// 四足挂点 — 前足在颈肩,后足在中后体
const LIMB_ANCHORS: ReadonlyArray<{ idx: number; side: 1 | -1 }> = [
  { idx: 6, side: 1 },   // 右前足
  { idx: 6, side: -1 },  // 左前足
  { idx: 28, side: 1 },  // 右后足
  { idx: 28, side: -1 }, // 左后足
];

// 段半径渐变:头(单独 SVG 渲染不计入 outline)→ 蛇颈细 → 肩部增粗 →
// 主体平直 → 长后段渐细 → 长尾稍尖
// 用户反馈"龙尾太短",这版把段数 36→48,多出的 12 段全分给后段+尾,
// 尾部从原 5 段拉到 14 段,长度翻倍,且渐变更细腻
function segRadius(i: number): number {
  if (i === 0) return HEAD_WIDTH;
  // 1~6 段蛇颈:13 → 11(细)
  if (i <= 6) return 13 - (i - 1) * 0.4;
  // 7~14 颈到身的过渡:11 → 20(快速增粗,肩部)
  if (i <= 14) return 11 + (i - 6) * 1.2;
  // 15~26 主体段:20 → 17(平缓收)— 比旧版多 2 段
  if (i <= 26) return 20 - (i - 14) * 0.25;
  // 27~36 长后段渐细:17 → 11 — 比旧版多 4 段
  if (i <= 36) return 17 - (i - 26) * 0.6;
  // 37~47 长尾段:11 → 2 — 比旧版尾尖 5 段加到 11 段,有真正的长尾甩动空间
  return Math.max(2, 11 - (i - 36) * 0.85);
}

interface SegPt {
  x: number;
  y: number;
  angle: number;
}

interface DragonState {
  segs: SegPt[];
  trail: Array<{ x: number; y: number; t: number; r: number }>;
  active: boolean;
}

export interface GoldDragonProps {
  flightKey: number;
  width: number;
  height: number;
  onFlightDone?: () => void;
}

export const GoldDragon: React.FC<GoldDragonProps> = ({
  flightKey,
  width,
  height,
  onFlightDone,
}) => {
  const reducedMotion = useReducedMotion();
  const containerRef = React.useRef<SVGSVGElement>(null);
  const stateRef = React.useRef<DragonState>({
    segs: Array.from({ length: SEG_COUNT }, () => ({ x: -200, y: -200, angle: 0 })),
    trail: [],
    active: false,
  });
  const pathRef = React.useRef<FlightPath | null>(null);
  const startTimeRef = React.useRef(0);
  const rafRef = React.useRef<number | null>(null);

  const [, forceRender] = React.useState(0);

  React.useEffect(() => {
    if (reducedMotion) return;
    if (flightKey <= 0) return;
    if (width <= 0 || height <= 0) return;

    pathRef.current = makeFlightPath(width, height);
    startTimeRef.current = performance.now();
    stateRef.current.active = true;
    stateRef.current.trail = [];

    const headInit = pathRef.current.at(0);
    const initSegs = stateRef.current.segs;
    for (let i = 0; i < SEG_COUNT; i++) {
      initSegs[i] = {
        x: headInit.x - i * SEG_LEN * Math.cos(headInit.angle),
        y: headInit.y - i * SEG_LEN * Math.sin(headInit.angle),
        angle: headInit.angle,
      };
    }
    forceRender((n) => n + 1);

    const path = pathRef.current;
    const dur = path.durationSec * 1000;
    // 缓存出场参数,避免 extension 阶段每帧重新算 path.at(1)
    const exitState = path.at(1);

    const loop = (now: number) => {
      if (document.hidden) {
        startTimeRef.current = now - (stateRef.current.segs[0]?.x ?? 0);
        rafRef.current = requestAnimationFrame(loop);
        return;
      }
      const elapsed = now - startTimeRef.current;
      const segs = stateRef.current.segs;

      let headX: number;
      let headY: number;
      let headAngle: number;

      if (elapsed < dur) {
        // 正常飞行段:沿 path 走
        const t = elapsed / dur;
        const head = path.at(t);
        headX = head.x;
        headY = head.y;
        headAngle = head.angle;
      } else {
        // 离场延长段:头继续沿出场方向匀速,身体 follow-the-leader 跟着出
        const extElapsed = elapsed - dur;
        if (extElapsed >= EXIT_EXTENSION_MS) {
          // 整条龙都飞出去了,彻底清掉
          clearDragonFromDom(containerRef.current);
          stateRef.current.active = false;
          rafRef.current = null;
          onFlightDone?.();
          return;
        }
        const extSec = extElapsed / 1000;
        headX = exitState.x + Math.cos(exitState.angle) * EXIT_VELOCITY_PXPS * extSec;
        headY = exitState.y + Math.sin(exitState.angle) * EXIT_VELOCITY_PXPS * extSec;
        headAngle = exitState.angle;
      }

      segs[0] = { x: headX, y: headY, angle: headAngle };

      // follow-the-leader 算后续段
      for (let i = 1; i < SEG_COUNT; i++) {
        const prev = segs[i - 1]!;
        const cur = segs[i]!;
        const dx = cur.x - prev.x;
        const dy = cur.y - prev.y;
        const d = Math.hypot(dx, dy);
        const newAngle = Math.atan2(prev.y - cur.y, prev.x - cur.x);
        if (d > SEG_LEN) {
          const ratio = (d - SEG_LEN) / d;
          cur.x -= dx * ratio;
          cur.y -= dy * ratio;
        }
        cur.angle = newAngle;
      }

      stateRef.current.trail.push({
        x: headX,
        y: headY,
        t: now,
        r: 4 + Math.random() * 3,
      });
      stateRef.current.trail = stateRef.current.trail.filter(
        (p) => now - p.t < 700,
      );
      if (stateRef.current.trail.length > TRAIL_LEN) {
        stateRef.current.trail.splice(0, stateRef.current.trail.length - TRAIL_LEN);
      }

      commitToDom(containerRef.current, stateRef.current, now);
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      stateRef.current.active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flightKey, width, height, reducedMotion]);

  if (reducedMotion) return null;

  return (
    <svg
      ref={containerRef}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        mixBlendMode: 'screen',
      }}
    >
      <defs>
        {/* 主金属金渐变 — 5 段,SMIL 流转产生"高光带" */}
        <linearGradient id="gd-body" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#5a3306">
            <animate attributeName="offset" values="0%;30%;0%" dur="2.4s" repeatCount="indefinite" />
          </stop>
          <stop offset="22%" stopColor="#d4a72c">
            <animate attributeName="offset" values="22%;55%;22%" dur="2.4s" repeatCount="indefinite" />
          </stop>
          <stop offset="50%" stopColor="#fff8d0">
            <animate attributeName="offset" values="50%;82%;50%" dur="2.4s" repeatCount="indefinite" />
          </stop>
          <stop offset="78%" stopColor="#d4a72c">
            <animate attributeName="offset" values="78%;100%;78%" dur="2.4s" repeatCount="indefinite" />
          </stop>
          <stop offset="100%" stopColor="#5a3306" />
        </linearGradient>
        {/* 鳞片专用渐变 — 更亮的金,反光强 */}
        <linearGradient id="gd-scale" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fff8d0" />
          <stop offset="55%" stopColor="#ffd75a" />
          <stop offset="100%" stopColor="#8a5a0a" />
        </linearGradient>
        {/* 龙腹色 — 更亮 */}
        <linearGradient id="gd-belly" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#ffd75a" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#fff8d0" stopOpacity="0.95" />
        </linearGradient>
        {/* 喉内能量球 — 红橙发光 */}
        <radialGradient id="gd-orb" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="35%" stopColor="#ffec80" />
          <stop offset="70%" stopColor="#ff6a1a" />
          <stop offset="100%" stopColor="#7a1208" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="gd-particle" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#fff5c2" stopOpacity="1" />
          <stop offset="50%" stopColor="#ffd75a" stopOpacity="0.6" />
          <stop offset="100%" stopColor="#a87209" stopOpacity="0" />
        </radialGradient>
        {/* 双层辉光 + 外圈金红 halo */}
        <filter id="gd-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2" result="b1" />
          <feGaussianBlur stdDeviation="5" result="b2" />
          <feGaussianBlur stdDeviation="11" result="b3" />
          <feMerge>
            <feMergeNode in="b3" />
            <feMergeNode in="b2" />
            <feMergeNode in="b1" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <g data-role="trail" filter="url(#gd-glow)" />

      {/* —— 主体外轮廓 + 高光腹线(rAF 每帧重写 d) */}
      <g data-role="body-shell" filter="url(#gd-glow)">
        <path data-role="body-outline" fill="url(#gd-body)" stroke="#5a3306" strokeWidth="1" strokeOpacity="0.85" />
        <path data-role="belly-line" fill="none" stroke="url(#gd-belly)" strokeWidth="3" strokeLinecap="round" opacity="0.85" />
        <path data-role="spine-highlight" fill="none" stroke="#fff8d0" strokeWidth="1.2" strokeLinecap="round" opacity="0.7" />
      </g>

      {/* —— 鳞片层(每段一簇 3 片菱形鳞)
           各段初始 transform 挪屏外,首飞 commit 才正位 */}
      <g data-role="scales" filter="url(#gd-glow)">
        {Array.from({ length: SEG_COUNT }).map((_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 段数固定
          <g key={i} data-scale={i} transform="translate(-9999 -9999)">
            {i > 0 && i < SEG_COUNT - 2 && (
              <>
                <ScaleDiamond r={Math.max(2, segRadius(i) * 0.42)} dy={-segRadius(i) * 0.25} />
                <ScaleDiamond r={Math.max(2, segRadius(i) * 0.34)} dy={0} />
                <ScaleDiamond r={Math.max(2, segRadius(i) * 0.42)} dy={segRadius(i) * 0.25} />
              </>
            )}
          </g>
        ))}
      </g>

      {/* —— 4 爪(每帧重画,跟挂载段同步) */}
      <g data-role="limbs" filter="url(#gd-glow)" />

      {/* —— 背鳍 + 鬃毛(每帧重画,沿身体上方) */}
      <g data-role="fins" filter="url(#gd-glow)" />

      {/* —— 龙头 — 初始挪到屏外,首飞 rAF 第一帧才正位 */}
      <g data-role="head" transform="translate(-9999 -9999)" filter="url(#gd-glow)">
        <DragonHead />
      </g>
    </svg>
  );
};

// ──────────────────────────────────────────────────────────────
// 单片菱形鳞 — 金属反光,顶亮底暗
// ──────────────────────────────────────────────────────────────

const ScaleDiamond: React.FC<{ r: number; dy: number }> = ({ r, dy }) => (
  <path
    d={`M 0,${dy - r * 0.7} L ${r * 0.85},${dy} L 0,${dy + r * 0.6} L ${-r * 0.85},${dy} Z`}
    fill="url(#gd-scale)"
    stroke="#7a4a06"
    strokeWidth="0.4"
    opacity="0.92"
  />
);

// ──────────────────────────────────────────────────────────────
// 龙头 — 怒目张嘴 + 喉内能量球
// ──────────────────────────────────────────────────────────────

/**
 * 龙头 — 按"鹿角、驼头、兔眼、牛耳、狮鼻、虎口、蛇项、鲤须"九似复刻。
 *
 * 2026-05-20 重绘:旧版鬃毛是 10 簇放射尖刺 + 头颅轮廓单薄,观感"骨瘦
 * 嶙峋像鬼"。本版改为:
 *   - 鬃毛 → 层叠流焰般卷鬃(填充曲面,不再扎人尖刺)
 *   - 头颅 → 加厚驼峰额 + 饱满腮,补受光面,体积感足
 *   - 鹿角 → 实心立体角(主干 + 双杈 + 节棱 + 高光),不再是细线条
 *   - 鳞片 → 吻背 / 额 / 颊覆叠半圆鳞,金属反光
 *   - 獠牙 → 上下弯钩主獠 + 错位列齿
 *   - 胡须 → 2 长鲤须,填充式由粗到细,飘逸回扫,须尖回卷
 *
 * 头部坐标系
 * ----------
 * 原点 (0,0) = 颈接合点;+X = 运动方向(吻部);-Y = 顶;+Y = 颌下
 *
 * 渲染顺序(后 → 前):
 *   1. 鬃毛(顶鬃 5 + 颊鬃 2 + 颔髯 3)
 *   2. 鹿角(后角暗 / 前角亮,各主干 + 双杈)
 *   3. 牛耳
 *   4. 下颚
 *   5. 口腔深暗
 *   6. 喉珠
 *   7. 上头颅(驼峰额 + 阔吻)
 *   8. 头部鳞片(吻背 / 额 / 颊)
 *   9. 骨棱眉骨
 *   10. 额心宝珠
 *   11. 狮鼻 + 鼻孔 + 喷火
 *   12. 兔眼
 *   13. 獠牙(上下弯钩 + 列齿)
 *   14. 鲤须(最上层)
 */
// 单片头部覆叠鳞 — 朝上拱起的半圆,金属反光;吻背 / 额 / 颊铺成行
const HeadScale: React.FC<{ cx: number; cy: number; r: number }> = ({ cx, cy, r }) => (
  <path
    d={`M ${cx - r},${cy} A ${r},${r} 0 0 0 ${cx + r},${cy} Z`}
    fill="url(#gd-scale)"
    stroke="#7a4a06"
    strokeWidth="0.4"
    opacity="0.9"
  />
);

const DragonHead: React.FC = () => (
  <>
    {/* ═══ 1. 鬃毛 — 层叠流焰卷鬃(填充曲面,取代旧版扎人尖刺)═══ */}
    <g data-part="mane">
      {/* 顶鬃 5 缕 — 沿后脑由下而上扇形铺开,火焰状回卷 */}
      <path d="M 2,-20 C -22,-26 -44,-28 -58,-24 C -42,-20 -22,-16 -6,-12 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 8,-26 C -16,-36 -40,-42 -60,-38 C -42,-30 -20,-24 0,-19 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 18,-32 C -4,-46 -28,-54 -50,-54 C -30,-44 -8,-36 10,-27 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 28,-38 C 10,-52 -12,-62 -32,-64 C -14,-54 6,-45 22,-34 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 38,-42 C 28,-55 14,-65 -2,-70 C 8,-58 22,-49 34,-39 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
      {/* 颊鬃 2 缕 — 腮后回扫 */}
      <path d="M -2,-8 C -24,-6 -46,0 -62,8 C -44,2 -22,-2 -4,-3 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 2,0 C -20,6 -40,12 -56,20 C -38,12 -18,6 0,4 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.6" />
      {/* 颔髯 3 缕 — 颌下飘髯 */}
      <path d="M 22,20 C 8,32 -6,42 -16,50 C -8,38 0,30 12,22 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 34,24 C 24,38 12,50 4,60 C 12,46 22,36 30,26 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.6" />
      <path d="M 46,24 C 42,38 36,52 32,64 C 38,48 44,36 48,26 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.6" />
    </g>

    {/* ═══ 2. 鹿角 — 实心立体角:主干 + 双杈 + 节棱 ═══ */}
    <g data-part="antlers">
      {/* 后角(暗,藏在前角之后,造出"两根角"纵深) */}
      <path d="M 40,-44 C 30,-58 18,-66 6,-70 C 14,-60 24,-52 36,-40 Z" fill="#6e4408" stroke="#3a1f00" strokeWidth="0.8" />
      <path d="M 26,-56 C 20,-64 12,-69 4,-70 C 10,-64 16,-58 22,-52 Z" fill="#6e4408" stroke="#3a1f00" strokeWidth="0.8" />
      {/* 前角:主干 + 上杈 + 下杈(实心填充,有体积)*/}
      <path d="M 52,-44 C 40,-60 24,-72 8,-78 C 18,-66 32,-54 46,-38 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.9" />
      <path d="M 30,-58 C 22,-68 12,-74 2,-76 C 10,-70 18,-63 26,-53 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.9" />
      <path d="M 44,-44 C 38,-54 30,-60 20,-62 C 28,-56 36,-50 42,-40 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.9" />
      {/* 节棱 — 主干上的横向骨节,鹿角质感 */}
      <path d="M 45,-41 L 49,-37 M 36,-50 L 40,-46 M 26,-60 L 30,-57 M 16,-68 L 20,-66" stroke="#3a1f00" strokeWidth="0.8" strokeLinecap="round" opacity="0.6" fill="none" />
      {/* 主干高光 */}
      <path d="M 50,-44 C 38,-59 24,-69 10,-75" stroke="#fff8d0" strokeWidth="1" fill="none" opacity="0.7" />
    </g>

    {/* ═══ 3. 牛耳 — 角根处一片小尖耳 ═══ */}
    <g data-part="ears">
      <path d="M 48,-40 Q 40,-50 30,-49 Q 38,-42 43,-35 Z" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="0.8" />
      <path d="M 45,-41 Q 39,-46 33,-46" stroke="#5a3306" strokeWidth="0.8" fill="none" opacity="0.7" />
    </g>

    {/* ═══ 4. 下颚 — 独立闭合,饱满有腮 ═══ */}
    <path
      d="M 28,0 C 34,4 44,8 58,10 C 70,11 79,11 85,10 C 89,9 91,12 88,16 C 84,21 72,25 56,26 C 44,27 32,26 24,22 C 18,19 15,14 18,9 C 21,4 25,1 28,0 Z"
      fill="url(#gd-body)"
      stroke="#3a1f00"
      strokeWidth="1.3"
    />

    {/* ═══ 5. 口腔深暗 — 上下颚之间的纵深阴影 ═══ */}
    <path d="M 27,1 C 42,-2 62,1 84,7 C 62,10 42,10 28,5 Z" fill="#0a0500" opacity="0.95" />

    {/* ═══ 6. 喉珠 — 口腔深处能量球 ═══ */}
    <circle cx="40" cy="4" r="6" fill="url(#gd-orb)">
      <animate attributeName="r" values="5;7.5;5" dur="1.6s" repeatCount="indefinite" />
    </circle>
    <circle cx="40" cy="4" r="10" fill="url(#gd-orb)" opacity="0.4">
      <animate attributeName="opacity" values="0.25;0.65;0.25" dur="1.6s" repeatCount="indefinite" />
    </circle>

    {/* ═══ 7. 上头颅 — 驼峰额 + 阔吻,体积饱满(取代旧版单薄轮廓)═══ */}
    <path
      d="M -8,-20 C -2,-33 8,-41 22,-45 C 36,-49 48,-48 56,-43 C 62,-39 65,-34 67,-29 C 76,-26 85,-21 91,-13 C 96,-6 96,1 91,5 C 88,7 84,6 81,5 C 68,1 54,-2 42,-3 C 36,-3 31,-2 28,0 C 23,-7 16,-13 8,-16 C 1,-19 -4,-19 -8,-20 Z"
      fill="url(#gd-body)"
      stroke="#3a1f00"
      strokeWidth="1.4"
    />
    {/* 颊部受光面 — 给头加体积,不显"扁" */}
    <path d="M 10,-14 C 24,-20 40,-22 52,-18 C 40,-10 24,-8 12,-9 Z" fill="#ffd75a" opacity="0.28" />

    {/* ═══ 8. 头部鳞片 — 吻背 / 额 / 颊覆叠半圆鳞 ═══ */}
    <g data-part="head-scales">
      {/* 吻背 2 行,近吻尖渐小 */}
      <HeadScale cx={68} cy={-15} r={4} />
      <HeadScale cx={75} cy={-16} r={4} />
      <HeadScale cx={82} cy={-15} r={3.6} />
      <HeadScale cx={88} cy={-12} r={3} />
      <HeadScale cx={72} cy={-10} r={3.4} />
      <HeadScale cx={79} cy={-10} r={3.2} />
      <HeadScale cx={85} cy={-8} r={2.8} />
      {/* 额顶一行 */}
      <HeadScale cx={26} cy={-34} r={4} />
      <HeadScale cx={34} cy={-36} r={4} />
      <HeadScale cx={43} cy={-35} r={3.6} />
      {/* 颊部 */}
      <HeadScale cx={20} cy={-8} r={3.6} />
      <HeadScale cx={28} cy={-6} r={3.6} />
      <HeadScale cx={36} cy={-6} r={3.4} />
    </g>

    {/* ═══ 9. 骨棱眉骨 — 眼眶上方厚压感 ═══ */}
    <path d="M 44,-30 Q 57,-38 70,-28 Q 58,-33 48,-27 Z" fill="#3a1f00" opacity="0.9" />
    <path d="M 46,-29 Q 57,-35 68,-28" stroke="#ffd75a" strokeWidth="0.8" fill="none" opacity="0.6" />

    {/* ═══ 10. 额心宝珠 — 中央菱形大鳞 ═══ */}
    <g transform="translate(38 -32)">
      <path d="M 0,-8 L 9,0 L 0,8 L -9,0 Z" fill="url(#gd-scale)" stroke="#3a1f00" strokeWidth="0.9" />
      <path d="M 0,-4.5 L 4.5,0 L 0,4.5 L -4.5,0 Z" fill="#fff8d0" opacity="0.75" />
      <circle cx="0" cy="0" r="1.6" fill="#ffffff" opacity="0.95" />
    </g>

    {/* ═══ 11. 狮鼻 — 阔鼻球 + 鼻孔 + 喷火 ═══ */}
    <g data-part="nose">
      <ellipse cx="86" cy="-6" rx="9" ry="7" fill="url(#gd-body)" stroke="#3a1f00" strokeWidth="1" />
      <path d="M 80,-9 Q 84,-14 91,-12 Q 92,-8 89,-6 Z" fill="#ffd75a" opacity="0.4" />
      <ellipse cx="84" cy="-5" rx="2.6" ry="1.8" fill="#0a0500" />
      <ellipse cx="83.4" cy="-6" rx="1" ry="0.6" fill="#fff8d0" opacity="0.6" />
      {/* 喷火 — SMIL 透明度脉动 */}
      <path d="M 82,-3 Q 74,-7 68,-3 Q 74,0 82,-1 Z" fill="url(#gd-orb)" opacity="0.7">
        <animate attributeName="opacity" values="0.3;0.9;0.3" dur="0.7s" repeatCount="indefinite" />
      </path>
    </g>

    {/* ═══ 12. 兔眼 — 大凸圆 + 竖瞳,怒目 ═══ */}
    <g data-part="eye">
      <ellipse cx="56" cy="-22" rx="10" ry="8.5" fill="#1a0d00" opacity="0.95" />
      <ellipse cx="57" cy="-23" rx="7.6" ry="6.4" fill="#fff8d0" />
      <circle cx="58" cy="-22" r="5.2" fill="#ffaa3a">
        <animate attributeName="r" values="4.6;5.5;4.6" dur="2.4s" repeatCount="indefinite" />
      </circle>
      <circle cx="58" cy="-22" r="5.2" fill="none" stroke="#7a4a06" strokeWidth="0.6" opacity="0.7" />
      <ellipse cx="58" cy="-22" rx="1" ry="4.8" fill="#0a0500" />
      <ellipse cx="59.8" cy="-24.4" rx="1.7" ry="1.2" fill="#ffffff" />
      <circle cx="56" cy="-19.5" r="0.6" fill="#ffffff" opacity="0.7" />
    </g>

    {/* ═══ 13. 獠牙 — 上下弯钩主獠 + 错位列齿 ═══ */}
    <g data-part="fangs">
      {/* 上主獠(前,弯钩下垂)*/}
      <path d="M 81,5 C 83,13 81,19 77,21 C 78,14 77,9 76,5 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.4" />
      {/* 上列齿 */}
      <path d="M 70,3 L 67,11 L 64,3 Z" fill="#ffffff" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 60,1 L 57,8 L 54,1 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 50,-1 L 47,5 L 44,-1 Z" fill="#ffffff" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 40,-2 L 38,3 L 35,-2 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.35" />
      {/* 下主獠(前,弯钩上挺)*/}
      <path d="M 75,10 C 77,3 75,-3 72,-4 C 72,3 72,7 71,10 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.4" />
      {/* 下列齿 — 在上列齿之间错位咬合 */}
      <path d="M 65,10 L 62,3 L 59,10 Z" fill="#ffffff" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 55,9 L 52,3 L 49,9 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 45,8 L 42,3 L 39,8 Z" fill="#ffffff" stroke="#3a1f00" strokeWidth="0.35" />
      <path d="M 36,6 L 34,2 L 31,6 Z" fill="#fff8d0" stroke="#3a1f00" strokeWidth="0.35" />
    </g>

    {/* ═══ 14. 鲤须 — 2 长须,填充式由粗到细,飘逸回扫,须尖回卷 ═══ */}
    <g data-part="whiskers">
      <path d="M 85,-2 C 54,-16 14,-22 -46,-30 C 14,-14 54,-6 85,3 Z" fill="#fff8d0" stroke="#d4a72c" strokeWidth="0.5" opacity="0.96" />
      <path d="M 82,4 C 48,12 6,16 -42,30 C 8,10 50,1 82,9 Z" fill="#fff8d0" stroke="#d4a72c" strokeWidth="0.5" opacity="0.96" />
      {/* 须尖回卷小钩 */}
      <path d="M -46,-30 Q -54,-30 -54,-24" stroke="#fff8d0" strokeWidth="1.4" fill="none" strokeLinecap="round" opacity="0.9" />
      <path d="M -42,30 Q -50,32 -50,38" stroke="#fff8d0" strokeWidth="1.4" fill="none" strokeLinecap="round" opacity="0.9" />
    </g>
  </>
);

// ──────────────────────────────────────────────────────────────
// DOM commit — 每帧重画 outline path / 鳞片定位 / 4 爪 / 鬃毛 / 龙头
// ──────────────────────────────────────────────────────────────

function commitToDom(
  svg: SVGSVGElement | null,
  state: DragonState,
  now: number,
): void {
  if (!svg) return;

  const segs = state.segs;
  const N = segs.length;

  // —— 1. 主体外轮廓(taper polygon)
  // **跳过 segs[0]** — 头部用独立 SVG 渲染,outline 从颈部 segs[1] 开始
  // 否则巨大头部会跟连续 outline 撞色 + 形状对不上
  const topPts: Array<[number, number]> = [];
  const botPts: Array<[number, number]> = [];
  for (let i = 1; i < N; i++) {
    const s = segs[i]!;
    const r = segRadius(i);
    const perpX = -Math.sin(s.angle);
    const perpY = Math.cos(s.angle);
    topPts.push([s.x + perpX * r, s.y + perpY * r]);
    botPts.push([s.x - perpX * r, s.y - perpY * r]);
  }

  const outline = svg.querySelector<SVGPathElement>('path[data-role="body-outline"]');
  if (outline && topPts.length >= 2) {
    // topPts / botPts 长度 = N-1(对应 segs[1..N-1])
    const M = topPts.length;
    let d = '';
    // 上边:沿 topPts 头→尾
    d += `M ${topPts[0]![0].toFixed(1)},${topPts[0]![1].toFixed(1)}`;
    for (let i = 1; i < M; i++) {
      const a = topPts[i - 1]!;
      const b = topPts[i]!;
      const mx = (a[0] + b[0]) / 2;
      const my = (a[1] + b[1]) / 2;
      d += ` Q ${a[0].toFixed(1)},${a[1].toFixed(1)} ${mx.toFixed(1)},${my.toFixed(1)}`;
    }
    // 尾部 — 收尖
    const tailTop = topPts[M - 1]!;
    const tailBot = botPts[M - 1]!;
    const tailMid: [number, number] = [(tailTop[0] + tailBot[0]) / 2, (tailTop[1] + tailBot[1]) / 2];
    // 尾鳍:从尾收尖向外延伸一个小三角(火苗状)
    const tailAngle = segs[N - 1]!.angle + Math.PI;
    const tailExtX = tailMid[0] + Math.cos(tailAngle) * 14;
    const tailExtY = tailMid[1] + Math.sin(tailAngle) * 14;
    d += ` L ${tailExtX.toFixed(1)},${tailExtY.toFixed(1)}`;
    // 下边:沿 botPts 尾→头
    for (let i = M - 1; i >= 1; i--) {
      const a = botPts[i]!;
      const b = botPts[i - 1]!;
      const mx = (a[0] + b[0]) / 2;
      const my = (a[1] + b[1]) / 2;
      d += ` Q ${a[0].toFixed(1)},${a[1].toFixed(1)} ${mx.toFixed(1)},${my.toFixed(1)}`;
    }
    d += ' Z';
    outline.setAttribute('d', d);
  }

  // —— 1.1 腹线高光(沿身体中线偏下,从头到尾)
  const belly = svg.querySelector<SVGPathElement>('path[data-role="belly-line"]');
  if (belly) {
    let d = '';
    for (let i = 1; i < N; i++) {
      const s = segs[i]!;
      const perpX = -Math.sin(s.angle);
      const perpY = Math.cos(s.angle);
      // 腹部偏下 — perp 反向 0.4r
      const bx = s.x - perpX * segRadius(i) * 0.4;
      const by = s.y - perpY * segRadius(i) * 0.4;
      d += i === 1 ? `M ${bx.toFixed(1)},${by.toFixed(1)}` : ` L ${bx.toFixed(1)},${by.toFixed(1)}`;
    }
    belly.setAttribute('d', d);
  }
  // 脊线高光(身体上方)
  const spine = svg.querySelector<SVGPathElement>('path[data-role="spine-highlight"]');
  if (spine) {
    let d = '';
    for (let i = 1; i < N - 2; i++) {
      const s = segs[i]!;
      const perpX = -Math.sin(s.angle);
      const perpY = Math.cos(s.angle);
      const sx = s.x + perpX * segRadius(i) * 0.55;
      const sy = s.y + perpY * segRadius(i) * 0.55;
      d += i === 1 ? `M ${sx.toFixed(1)},${sy.toFixed(1)}` : ` L ${sx.toFixed(1)},${sy.toFixed(1)}`;
    }
    spine.setAttribute('d', d);
  }

  // —— 2. 鳞片簇 — 每个 <g data-scale={i}> 跟随段位置 + 旋转
  const scalesGroup = svg.querySelector<SVGGElement>('g[data-role="scales"]');
  if (scalesGroup) {
    const children = scalesGroup.children;
    for (let i = 0; i < N && i < children.length; i++) {
      const seg = segs[i];
      const g = children[i] as SVGGElement | undefined;
      if (!seg || !g) continue;
      g.setAttribute(
        'transform',
        `translate(${seg.x.toFixed(1)} ${seg.y.toFixed(1)}) rotate(${((seg.angle * 180) / Math.PI).toFixed(1)})`,
      );
    }
  }

  // —— 3. 4 爪 — 每帧重画
  const limbsGroup = svg.querySelector<SVGGElement>('g[data-role="limbs"]');
  if (limbsGroup) {
    while (limbsGroup.firstChild) limbsGroup.removeChild(limbsGroup.firstChild);
    for (const anchor of LIMB_ANCHORS) {
      const seg = segs[anchor.idx];
      if (!seg) continue;
      drawLimb(limbsGroup, seg, anchor.side, anchor.idx);
    }
  }

  // —— 4. 背鳍 + 鬃毛(前 26 段,跟着加长的龙身延伸更远)
  const fins = svg.querySelector<SVGGElement>('g[data-role="fins"]');
  if (fins) {
    while (fins.firstChild) fins.removeChild(fins.firstChild);
    for (let i = 1; i < 26; i++) {
      const s = segs[i];
      if (!s) continue;
      const r = segRadius(i);
      const perpX = -Math.sin(s.angle);
      const perpY = Math.cos(s.angle);
      const tipX = s.x + perpX * (r + 10);
      const tipY = s.y + perpY * (r + 10);
      const baseLX = s.x + Math.cos(s.angle) * r * 0.5 + perpX * r * 0.4;
      const baseLY = s.y + Math.sin(s.angle) * r * 0.5 + perpY * r * 0.4;
      const baseRX = s.x - Math.cos(s.angle) * r * 0.5 + perpX * r * 0.4;
      const baseRY = s.y - Math.sin(s.angle) * r * 0.5 + perpY * r * 0.4;
      const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      // 前几段背鳍更高更尖,模拟鬃毛;后段渐变成普通鳍
      const tall = i < 6 ? r * 0.6 : 0; // 前 6 段加高
      const finTipX = tipX + perpX * tall;
      const finTipY = tipY + perpY * tall;
      p.setAttribute(
        'd',
        `M ${baseLX.toFixed(1)},${baseLY.toFixed(1)} L ${finTipX.toFixed(1)},${finTipY.toFixed(1)} L ${baseRX.toFixed(1)},${baseRY.toFixed(1)} Z`,
      );
      p.setAttribute('fill', 'url(#gd-scale)');
      p.setAttribute('stroke', '#5a3306');
      p.setAttribute('stroke-width', '0.4');
      p.setAttribute('opacity', '0.92');
      fins.appendChild(p);
    }
  }

  // —— 5. 龙头
  const headGroup = svg.querySelector<SVGGElement>('g[data-role="head"]');
  const head = segs[0];
  if (headGroup && head) {
    headGroup.setAttribute(
      'transform',
      `translate(${head.x.toFixed(1)} ${head.y.toFixed(1)}) rotate(${((head.angle * 180) / Math.PI).toFixed(1)})`,
    );
  }

  // —— 6. 拖尾粒子
  const trail = svg.querySelector<SVGGElement>('g[data-role="trail"]');
  if (trail) {
    while (trail.firstChild) trail.removeChild(trail.firstChild);
    for (const p of state.trail) {
      const age = (now - p.t) / 700;
      const op = (1 - age) * 0.85;
      const r = p.r * (1 + age * 1.2);
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', p.x.toFixed(1));
      c.setAttribute('cy', p.y.toFixed(1));
      c.setAttribute('r', r.toFixed(1));
      c.setAttribute('fill', 'url(#gd-particle)');
      c.setAttribute('opacity', op.toFixed(2));
      trail.appendChild(c);
    }
  }
}

// ──────────────────────────────────────────────────────────────
// 一次飞行结束后清场 — 把 path d 清空 + 动态层全删,避免"半截龙残留"
// ──────────────────────────────────────────────────────────────

function clearDragonFromDom(svg: SVGSVGElement | null): void {
  if (!svg) return;
  // 主体三条 path:body-outline / belly-line / spine-highlight
  svg.querySelectorAll<SVGPathElement>('path[data-role]').forEach((p) => {
    p.setAttribute('d', '');
  });
  // 鳞片层每段往屏外挪 — 下次飞行 commitToDom 第一帧会重新就位
  const scales = svg.querySelector<SVGGElement>('g[data-role="scales"]');
  if (scales) {
    for (let i = 0; i < scales.children.length; i++) {
      (scales.children[i] as SVGGElement).setAttribute(
        'transform',
        'translate(-9999 -9999)',
      );
    }
  }
  // 头同样挪走
  const head = svg.querySelector<SVGGElement>('g[data-role="head"]');
  head?.setAttribute('transform', 'translate(-9999 -9999)');
  // limbs / fins / trail 是 commitToDom 每帧动态生成,直接全清
  ['limbs', 'fins', 'trail'].forEach((role) => {
    const g = svg.querySelector<SVGGElement>(`g[data-role="${role}"]`);
    if (!g) return;
    while (g.firstChild) g.removeChild(g.firstChild);
  });
}

// ──────────────────────────────────────────────────────────────
// 单条爪子 — 大腿 + 小腿 + 5 钩状指
// ──────────────────────────────────────────────────────────────

function drawLimb(
  group: SVGGElement,
  anchor: SegPt,
  side: 1 | -1,
  segIdx: number,
): void {
  // 跟身体的偏移角度 — 沿 perp,加上轻微往后甩(模拟扑爪)
  const perpX = -Math.sin(anchor.angle);
  const perpY = Math.cos(anchor.angle);
  // 大腿根部:身体外侧 0.7r
  const r = segRadius(segIdx);
  const hipX = anchor.x + perpX * side * r * 0.5;
  const hipY = anchor.y + perpY * side * r * 0.5;

  // 大腿外伸:朝身体外 + 轻微向后(沿 -tangent)
  const tangentX = Math.cos(anchor.angle);
  const tangentY = Math.sin(anchor.angle);
  const upperLen = 26;
  const kneeX = hipX + perpX * side * upperLen * 0.6 - tangentX * upperLen * 0.3;
  const kneeY = hipY + perpY * side * upperLen * 0.6 - tangentY * upperLen * 0.3;

  // 小腿:从膝向外向前甩(扑爪动作)
  const lowerLen = 22;
  const wristX = kneeX + perpX * side * lowerLen * 0.4 + tangentX * lowerLen * 0.5;
  const wristY = kneeY + perpY * side * lowerLen * 0.4 + tangentY * lowerLen * 0.5;

  // 大腿(粗 path,带渐变填充)
  const thigh = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  thigh.setAttribute(
    'd',
    `M ${hipX.toFixed(1)},${hipY.toFixed(1)} Q ${(hipX + kneeX) / 2 + perpX * side * 5},${(hipY + kneeY) / 2 + perpY * side * 5} ${kneeX.toFixed(1)},${kneeY.toFixed(1)}`,
  );
  thigh.setAttribute('stroke', 'url(#gd-scale)');
  thigh.setAttribute('stroke-width', '11');
  thigh.setAttribute('stroke-linecap', 'round');
  thigh.setAttribute('fill', 'none');
  group.appendChild(thigh);

  // 小腿
  const shin = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  shin.setAttribute(
    'd',
    `M ${kneeX.toFixed(1)},${kneeY.toFixed(1)} Q ${(kneeX + wristX) / 2 + tangentX * 3},${(kneeY + wristY) / 2 + tangentY * 3} ${wristX.toFixed(1)},${wristY.toFixed(1)}`,
  );
  shin.setAttribute('stroke', 'url(#gd-scale)');
  shin.setAttribute('stroke-width', '8');
  shin.setAttribute('stroke-linecap', 'round');
  shin.setAttribute('fill', 'none');
  group.appendChild(shin);

  // 5 钩状指 — 从腕部向前扇形展开,每根弯钩
  const fanAngles = [-0.5, -0.25, 0, 0.25, 0.5];
  const fingerBaseAngle = anchor.angle; // 朝身体前方
  for (const fan of fanAngles) {
    const a = fingerBaseAngle + fan;
    const len = 13;
    const midX = wristX + Math.cos(a) * len * 0.55 + perpX * side * 2;
    const midY = wristY + Math.sin(a) * len * 0.55 + perpY * side * 2;
    // 指尖:再前移并下勾(钩状)
    const tipX = midX + Math.cos(a) * len * 0.55 + perpX * side * 2.5;
    const tipY = midY + Math.sin(a) * len * 0.55 + perpY * side * 2.5;
    const claw = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    claw.setAttribute(
      'd',
      `M ${wristX.toFixed(1)},${wristY.toFixed(1)} Q ${midX.toFixed(1)},${midY.toFixed(1)} ${tipX.toFixed(1)},${tipY.toFixed(1)}`,
    );
    claw.setAttribute('stroke', '#fff8d0');
    claw.setAttribute('stroke-width', '2.6');
    claw.setAttribute('stroke-linecap', 'round');
    claw.setAttribute('fill', 'none');
    group.appendChild(claw);
    // 黑色钩尖
    const tipDot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    tipDot.setAttribute('cx', tipX.toFixed(1));
    tipDot.setAttribute('cy', tipY.toFixed(1));
    tipDot.setAttribute('r', '1.2');
    tipDot.setAttribute('fill', '#1a0d00');
    group.appendChild(tipDot);
  }
}

// ──────────────────────────────────────────────────────────────

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
