/**
 * 智灵鸟 — 金色中国凤凰盘旋上升(写实级,2026-05-20)。
 *
 * 视觉升级
 * --------
 * 1. **9 条长尾羽**:替代旧版 5 根简单 bezier,改 9 根"燃烧飘带"风格,
 *    每根末端有金色火星粒子粒子飘散,采样自 60 帧轨迹历史。
 * 2. **多层羽翼**:每翼 5 根主翎 + 2 根副翎(覆翼),SMIL animateTransform
 *    错相位拍翼,每根羽都是带羽轴(中线)+ 渐变的椭圆 path。
 * 3. **火焰金冠**:头顶 5 缕火焰状羽,SMIL scale + opacity 闪烁,模拟金冠
 *    跃动。
 * 4. **金光眼**:radial gradient 内核 + Gaussian blur 光晕,瞳孔金黄,虹膜
 *    呈金白色。
 * 5. **嘶鸣张喙**:上下颚分离,喙内显发光声波弧。
 *
 * 飞行路径
 * --------
 * 走 ``dragonPath`` 的 ``spiral-up`` 模式:屏底入场 → 椭圆螺旋 2~2.5 圈
 * 收紧上升 → 屏顶离场,9~16s 一程。每 90~150s 一次。
 */

import * as React from 'react';
import { makeFlightPath, type FlightPath } from './dragonPath';

interface Spark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  t: number;
  r: number;
}

interface PhoenixProps {
  flightKey: number;
  width: number;
  height: number;
  onFlightDone?: () => void;
}

// 尾羽扇角(9 根,弧度);中间 0,两侧 ±0.55
const TAIL_FAN_RAD = [-0.55, -0.42, -0.28, -0.15, 0, 0.15, 0.28, 0.42, 0.55];
// 主翼上下 5 根主翎的扇角(每翼)
const WING_FAN_RAD = [-0.35, -0.18, 0, 0.18, 0.35];

export const Phoenix: React.FC<PhoenixProps> = ({
  flightKey,
  width,
  height,
  onFlightDone,
}) => {
  const reducedMotion = useReducedMotion();
  const svgRef = React.useRef<SVGSVGElement>(null);
  const pathRef = React.useRef<FlightPath | null>(null);
  const startRef = React.useRef(0);
  const rafRef = React.useRef<number | null>(null);
  const tailHistRef = React.useRef<Array<{ x: number; y: number; angle: number }>>([]);
  const sparksRef = React.useRef<Spark[]>([]);

  React.useEffect(() => {
    if (reducedMotion) return;
    if (flightKey <= 0) return;
    if (width <= 0 || height <= 0) return;

    // 凤凰用盘旋上升路径
    pathRef.current = makeFlightPath(width, height, {
      mode: 'spiral-up',
      speedScale: 1.0,
    });
    startRef.current = performance.now();
    tailHistRef.current = [];
    sparksRef.current = [];

    const path = pathRef.current;
    const dur = path.durationSec * 1000;
    const exitState = path.at(1);
    const EXIT_MS = 1200;
    const EXIT_VEL = 600;

    let lastSpawnAt = 0;

    const loop = (now: number) => {
      if (document.hidden) {
        startRef.current = now - 0;
        rafRef.current = requestAnimationFrame(loop);
        return;
      }
      const elapsed = now - startRef.current;
      let head: { x: number; y: number; angle: number };

      if (elapsed < dur) {
        const t = elapsed / dur;
        head = path.at(t);
      } else {
        const extElapsed = elapsed - dur;
        if (extElapsed >= EXIT_MS) {
          clearPhoenixFromDom(svgRef.current);
          rafRef.current = null;
          onFlightDone?.();
          return;
        }
        const extSec = extElapsed / 1000;
        head = {
          x: exitState.x + Math.cos(exitState.angle) * EXIT_VEL * extSec,
          y: exitState.y + Math.sin(exitState.angle) * EXIT_VEL * extSec,
          angle: exitState.angle,
        };
      }

      tailHistRef.current.push(head);
      if (tailHistRef.current.length > 60) tailHistRef.current.shift();

      // —— 每 80ms 在 9 根尾羽末端随机一根产生 1~2 颗火星
      if (now - lastSpawnAt > 80 && tailHistRef.current.length > 30) {
        lastSpawnAt = now;
        const sampleIdx = Math.max(0, tailHistRef.current.length - 1 - 28);
        const tip = tailHistRef.current[sampleIdx];
        if (tip) {
          const sparkCount = 1 + Math.floor(Math.random() * 2);
          for (let k = 0; k < sparkCount; k++) {
            // 在 9 根尾羽末端随机选一根作为产生点
            const fanIdx = Math.floor(Math.random() * TAIL_FAN_RAD.length);
            const fan = TAIL_FAN_RAD[fanIdx]!;
            // 相对头反方向 + fan 偏角的方向射出
            const a = head.angle + Math.PI + fan;
            const dist = 60 + Math.random() * 40;
            const sx = head.x + Math.cos(a) * dist;
            const sy = head.y + Math.sin(a) * dist;
            sparksRef.current.push({
              x: sx,
              y: sy,
              vx: Math.cos(a) * (20 + Math.random() * 40),
              vy: Math.sin(a) * (20 + Math.random() * 40) + 30, // 略向下飘
              t: now,
              r: 1.5 + Math.random() * 2.5,
            });
          }
        }
      }

      // 火星位置推进 + 过期清理(寿命 900ms)
      const dt = 1 / 60; // 假设 60fps,粒子物理可容忍
      sparksRef.current = sparksRef.current.filter((s) => now - s.t < 900);
      for (const s of sparksRef.current) {
        s.x += s.vx * dt;
        s.y += s.vy * dt;
        s.vy += 50 * dt; // 重力,模拟火星往下飘
      }

      commitPhoenixToDom(
        svgRef.current,
        head,
        tailHistRef.current,
        sparksRef.current,
        now,
      );
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [flightKey, width, height, reducedMotion, onFlightDone]);

  if (reducedMotion) return null;

  return (
    <svg
      ref={svgRef}
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
        {/* —— 主体金属金渐变,SMIL stop-offset 流转 */}
        <linearGradient id="ph-body" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#fff8d0">
            <animate attributeName="offset" values="0%;30%;0%" dur="2.8s" repeatCount="indefinite" />
          </stop>
          <stop offset="35%" stopColor="#ffd75a">
            <animate attributeName="offset" values="35%;65%;35%" dur="2.8s" repeatCount="indefinite" />
          </stop>
          <stop offset="65%" stopColor="#d4a72c" />
          <stop offset="100%" stopColor="#5a3306" />
        </linearGradient>
        {/* 翎羽渐变 — 根部深,中段亮金,尾端金白 */}
        <linearGradient id="ph-feather" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#5a3306" stopOpacity="0.95" />
          <stop offset="30%" stopColor="#c08800" stopOpacity="0.92" />
          <stop offset="60%" stopColor="#ffd75a" stopOpacity="0.95" />
          <stop offset="100%" stopColor="#fff8d0" stopOpacity="0" />
        </linearGradient>
        {/* 长尾飘带渐变 — 火焰感,头深尾淡 */}
        <linearGradient id="ph-tail" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#ff9a1a" stopOpacity="0.95" />
          <stop offset="40%" stopColor="#ffd75a" stopOpacity="0.85" />
          <stop offset="80%" stopColor="#fff8d0" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#fff8d0" stopOpacity="0" />
        </linearGradient>
        {/* 金眼 radial */}
        <radialGradient id="ph-eye" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="30%" stopColor="#ffec80" />
          <stop offset="60%" stopColor="#ff9a1a" />
          <stop offset="100%" stopColor="#7a4a06" stopOpacity="0.6" />
        </radialGradient>
        {/* 火星粒子 */}
        <radialGradient id="ph-spark" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="40%" stopColor="#ffd75a" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#ff6a1a" stopOpacity="0" />
        </radialGradient>
        {/* 三层辉光 */}
        <filter id="ph-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.5" result="b1" />
          <feGaussianBlur stdDeviation="6" result="b2" />
          <feGaussianBlur stdDeviation="12" result="b3" />
          <feMerge>
            <feMergeNode in="b3" />
            <feMergeNode in="b2" />
            <feMergeNode in="b1" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* 9 条长尾飘带 — rAF 每帧重画 */}
      <g data-role="ph-tails" filter="url(#ph-glow)" />

      {/* 火星粒子层 */}
      <g data-role="ph-sparks" filter="url(#ph-glow)" />

      {/* 鸟体(头/身/翼/冠)— 初始 transform 挪到屏外,等首飞 rAF 第一帧
          才把它移到正确位置;否则首飞前用户会看到鸟体"卡"在左上角 (0,0) */}
      <g data-role="ph-body" transform="translate(-9999 -9999)" filter="url(#ph-glow)">
        <PhoenixBody />
      </g>
    </svg>
  );
};

// ──────────────────────────────────────────────────────────────
// 凤凰躯干 — 多层羽翼 + 火焰金冠 + 金光眼 + 嘶鸣张喙
// ──────────────────────────────────────────────────────────────

const PhoenixBody: React.FC = () => (
  <>
    {/* —— 上翼:5 根主翎 + 2 根副翎(覆翼) */}
    <g>
      <WingFeathers side={1} />
    </g>
    {/* —— 下翼:对称 */}
    <g>
      <WingFeathers side={-1} />
    </g>

    {/* —— 身体椭圆 */}
    <ellipse cx="0" cy="0" rx="14" ry="5.5" fill="url(#ph-body)" />
    {/* 胸前金光高光 */}
    <ellipse cx="-2" cy="2" rx="9" ry="2.5" fill="#fff8d0" opacity="0.55" />

    {/* —— 颈 + 头 */}
    <path d="M 11,-4 Q 22,-8 26,-4" stroke="#d4a72c" strokeWidth="3.5" fill="none" strokeLinecap="round" />
    <circle cx="26" cy="-4" r="5" fill="url(#ph-body)" />
    <circle cx="26" cy="-4" r="5" fill="none" stroke="#7a4a06" strokeWidth="0.6" opacity="0.7" />

    {/* —— 火焰金冠 5 缕(头顶向上,SMIL 闪烁) */}
    <FlameCrown />

    {/* —— 嘶鸣张喙(上下颚分离) */}
    <path
      d="M 30,-5 L 36,-3.5 L 32,-2.5 Z"
      fill="#d4a72c"
      stroke="#5a3306"
      strokeWidth="0.4"
    />
    <path
      d="M 30,-2 L 36,-0.5 L 32,0.5 Z"
      fill="#d4a72c"
      stroke="#5a3306"
      strokeWidth="0.4"
    >
      <animateTransform
        attributeName="transform"
        type="rotate"
        values="0 30 -2; 10 30 -2; 0 30 -2"
        dur="1.8s"
        repeatCount="indefinite"
      />
    </path>
    {/* 嘴内发光声波弧 */}
    <path
      d="M 32,-1.5 Q 38,-1 36,2"
      stroke="#fff8d0"
      strokeWidth="0.8"
      fill="none"
      opacity="0.5"
    >
      <animate attributeName="opacity" values="0;0.7;0" dur="1.8s" repeatCount="indefinite" />
    </path>

    {/* —— 金光眼:radial 内核 + 黑瞳点 + 高光 */}
    <circle cx="26.5" cy="-5.5" r="1.9" fill="url(#ph-eye)" />
    <circle cx="26.5" cy="-5.5" r="2.4" fill="url(#ph-eye)" opacity="0.55">
      <animate attributeName="opacity" values="0.4;0.8;0.4" dur="2s" repeatCount="indefinite" />
    </circle>
    <circle cx="26.5" cy="-5.5" r="0.8" fill="#1a0d00" />
    <circle cx="27" cy="-5.9" r="0.4" fill="#fff" />
  </>
);

// 单翼:5 根主翎 + 2 根副翎(覆翼根部小羽)
const WingFeathers: React.FC<{ side: 1 | -1 }> = ({ side }) => {
  const flapBegin = side === 1 ? '0s' : '0.08s';
  return (
    <g>
      {/* 拍翼整体动画 — 主翼根部为旋转中心,大幅扇动 */}
      <animateTransform
        attributeName="transform"
        type="rotate"
        values={side === 1 ? '-6;-30;-6' : '6;30;6'}
        keyTimes="0;0.5;1"
        dur="0.7s"
        repeatCount="indefinite"
        begin={flapBegin}
      />
      {/* 5 根主翎 — 长椭圆 + 中线羽轴 */}
      {WING_FAN_RAD.map((fan, i) => {
        // 翼根 (0, 0),朝身体上/下方按 side 散开
        // 每根羽长度 32~38,根据 fan 调整,中间最长
        const len = 38 - Math.abs(fan) * 14;
        const aa = side * (Math.PI / 2 + fan); // 上翼朝 -y,下翼朝 +y
        const tipX = Math.cos(aa) * len;
        const tipY = Math.sin(aa) * len;
        const midX = Math.cos(aa) * len * 0.55;
        const midY = Math.sin(aa) * len * 0.55;
        const ctrlOffset = side * 5;
        const perpA = aa + Math.PI / 2;
        const ctrlX = midX + Math.cos(perpA) * ctrlOffset;
        const ctrlY = midY + Math.sin(perpA) * ctrlOffset;
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: 数量固定
          <g key={i}>
            {/* 羽片(椭圆 path) */}
            <path
              d={`M 0,0 Q ${ctrlX.toFixed(1)},${ctrlY.toFixed(1)} ${tipX.toFixed(1)},${tipY.toFixed(1)}`}
              stroke="url(#ph-feather)"
              strokeWidth={6 - Math.abs(fan) * 2}
              strokeLinecap="round"
              fill="none"
            />
            {/* 羽轴中线 */}
            <path
              d={`M 0,0 L ${tipX.toFixed(1)},${tipY.toFixed(1)}`}
              stroke="#7a4a06"
              strokeWidth="0.4"
              opacity="0.85"
            />
          </g>
        );
      })}
      {/* 2 根副翎覆翼(根部短羽) */}
      {[-0.25, 0.25].map((fan, i) => {
        const len = 16;
        const aa = side * (Math.PI / 2 + fan);
        const tipX = Math.cos(aa) * len;
        const tipY = Math.sin(aa) * len;
        return (
          <path
            // biome-ignore lint/suspicious/noArrayIndexKey: 固定 2 根
            key={`cov-${i}`}
            d={`M ${(-Math.cos(aa) * 3).toFixed(1)},${(-Math.sin(aa) * 3).toFixed(1)} Q ${(tipX * 0.5).toFixed(1)},${(tipY * 0.5 + side * 3).toFixed(1)} ${tipX.toFixed(1)},${tipY.toFixed(1)}`}
            stroke="url(#ph-feather)"
            strokeWidth="3"
            strokeLinecap="round"
            fill="none"
            opacity="0.85"
          />
        );
      })}
    </g>
  );
};

// 头顶火焰金冠 — 5 缕火焰状羽,scale + opacity SMIL 跳动
const FlameCrown: React.FC = () => {
  const flames: Array<{ angle: number; len: number; w: number; delay: number }> = [
    { angle: -Math.PI * 0.6, len: 12, w: 3, delay: 0 },
    { angle: -Math.PI * 0.5, len: 16, w: 3.5, delay: 0.18 },
    { angle: -Math.PI * 0.42, len: 14, w: 3, delay: 0.36 },
    { angle: -Math.PI * 0.34, len: 10, w: 2.5, delay: 0.5 },
    { angle: -Math.PI * 0.7, len: 9, w: 2.3, delay: 0.62 },
  ];
  return (
    <g transform="translate(26 -4)">
      {flames.map((f, i) => {
        const tipX = Math.cos(f.angle) * f.len;
        const tipY = Math.sin(f.angle) * f.len;
        const midX = Math.cos(f.angle) * f.len * 0.55;
        const midY = Math.sin(f.angle) * f.len * 0.55;
        const perpA = f.angle + Math.PI / 2;
        const ctrlOff = 2;
        const cLX = midX + Math.cos(perpA) * ctrlOff;
        const cLY = midY + Math.sin(perpA) * ctrlOff;
        const cRX = midX - Math.cos(perpA) * ctrlOff;
        const cRY = midY - Math.sin(perpA) * ctrlOff;
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: 5 缕固定
          <g key={i}>
            <path
              d={`M ${(-Math.cos(perpA) * f.w).toFixed(1)},${(-Math.sin(perpA) * f.w).toFixed(1)} Q ${cLX.toFixed(1)},${cLY.toFixed(1)} ${tipX.toFixed(1)},${tipY.toFixed(1)} Q ${cRX.toFixed(1)},${cRY.toFixed(1)} ${(Math.cos(perpA) * f.w).toFixed(1)},${(Math.sin(perpA) * f.w).toFixed(1)} Z`}
              fill="url(#ph-tail)"
              opacity="0.9"
            >
              <animateTransform
                attributeName="transform"
                type="scale"
                values="1 0.85; 1 1.1; 1 0.92"
                keyTimes="0;0.5;1"
                dur="0.9s"
                begin={`${f.delay}s`}
                repeatCount="indefinite"
                additive="sum"
              />
              <animate
                attributeName="opacity"
                values="0.7;1;0.7"
                dur="0.9s"
                begin={`${f.delay}s`}
                repeatCount="indefinite"
              />
            </path>
          </g>
        );
      })}
    </g>
  );
};

// ──────────────────────────────────────────────────────────────
// DOM commit — 主体定位 + 9 尾羽 + 火星粒子
// ──────────────────────────────────────────────────────────────

function commitPhoenixToDom(
  svg: SVGSVGElement | null,
  head: { x: number; y: number; angle: number },
  history: Array<{ x: number; y: number; angle: number }>,
  sparks: Spark[],
  now: number,
): void {
  if (!svg) return;

  const bodyGroup = svg.querySelector<SVGGElement>('g[data-role="ph-body"]');
  if (bodyGroup) {
    bodyGroup.setAttribute(
      'transform',
      `translate(${head.x.toFixed(1)} ${head.y.toFixed(1)}) rotate(${((head.angle * 180) / Math.PI).toFixed(1)})`,
    );
  }

  // —— 9 条长尾飘带
  const tails = svg.querySelector<SVGGElement>('g[data-role="ph-tails"]');
  if (tails) {
    while (tails.firstChild) tails.removeChild(tails.firstChild);
    if (history.length >= 5) {
      const cos = Math.cos(head.angle);
      const sin = Math.sin(head.angle);
      // 尾根:身体尾部(-14 沿头方向,即 -tangent 14)
      const tailRootOffset = -14;
      const rootX = head.x + cos * tailRootOffset;
      const rootY = head.y + sin * tailRootOffset;

      for (let i = 0; i < TAIL_FAN_RAD.length; i++) {
        const fan = TAIL_FAN_RAD[i]!;
        // 9 根尾羽长度不一,中间最长,两侧短
        const lengthRatio = 1 - Math.abs(fan) / 0.7;
        const histIdx = Math.max(
          0,
          Math.floor(history.length - 1 - 8 - lengthRatio * (history.length - 12)),
        );
        const sample = history[histIdx];
        if (!sample) continue;

        // 中段控制点:沿头反方向 + fan 横偏,模拟羽中弯
        const ctrlOffset = 48 + lengthRatio * 30;
        const fa = head.angle + Math.PI + fan;
        const ctrlX = rootX + Math.cos(fa) * ctrlOffset;
        const ctrlY = rootY + Math.sin(fa) * ctrlOffset;
        // 第二段控制点:取历史更早的点,实现"尾飘带"二次弯
        const histIdx2 = Math.max(0, histIdx - 8);
        const sample2 = history[histIdx2] ?? sample;

        const d = `M ${rootX.toFixed(1)},${rootY.toFixed(1)} Q ${ctrlX.toFixed(1)},${ctrlY.toFixed(1)} ${sample2.x.toFixed(1)},${sample2.y.toFixed(1)} T ${sample.x.toFixed(1)},${sample.y.toFixed(1)}`;
        const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        p.setAttribute('d', d);
        p.setAttribute('stroke', 'url(#ph-tail)');
        p.setAttribute('stroke-width', String(3.5 + lengthRatio * 2));
        p.setAttribute('stroke-linecap', 'round');
        p.setAttribute('fill', 'none');
        p.setAttribute('opacity', String(0.6 + lengthRatio * 0.3));
        tails.appendChild(p);
      }
    }
  }

  // —— 火星粒子
  const sparksGroup = svg.querySelector<SVGGElement>('g[data-role="ph-sparks"]');
  if (sparksGroup) {
    while (sparksGroup.firstChild) sparksGroup.removeChild(sparksGroup.firstChild);
    for (const s of sparks) {
      const age = (now - s.t) / 900; // 0..1
      const op = (1 - age) * 0.95;
      const r = s.r * (1 - age * 0.3);
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', s.x.toFixed(1));
      c.setAttribute('cy', s.y.toFixed(1));
      c.setAttribute('r', r.toFixed(2));
      c.setAttribute('fill', 'url(#ph-spark)');
      c.setAttribute('opacity', op.toFixed(2));
      sparksGroup.appendChild(c);
    }
  }
}

function clearPhoenixFromDom(svg: SVGSVGElement | null): void {
  if (!svg) return;
  const tails = svg.querySelector<SVGGElement>('g[data-role="ph-tails"]');
  if (tails) while (tails.firstChild) tails.removeChild(tails.firstChild);
  const sparks = svg.querySelector<SVGGElement>('g[data-role="ph-sparks"]');
  if (sparks) while (sparks.firstChild) sparks.removeChild(sparks.firstChild);
  const body = svg.querySelector<SVGGElement>('g[data-role="ph-body"]');
  body?.setAttribute('transform', 'translate(-9999 -9999)');
}

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
