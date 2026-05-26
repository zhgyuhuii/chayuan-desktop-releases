/**
 * 龙 / 凤凰的飞行路径生成器 — 一次飞行就是一条参数曲线 (t∈[0,1] → [x,y])。
 *
 * 设计要点
 * --------
 * 1. 随机锚点:每次在 9 个锚点(4 角 / 4 边中点 / 中心)里随机挑「起点 →
 *    终点」(够远),整体航向随机 —— 左上→右下、右下→左上、中心→右上…
 *    跟 MagpieFlock 的 makeFlightParams 同一套思路。角 / 边锚点放屏外一截
 *    让飞行物飞入飞出,中心锚点在屏内,对应「从中间突然出现」。
 * 2. 主轨迹:由 5~7 个航点 Catmull-Rom 插值,自带平滑切线,头不会"啪"地拐
 *    死弯。中段航点沿「主轴法向」用两个不同周期的 sin 叠加做蜿蜒。
 * 3. 盘旋:中段一定概率插入一个紧螺旋(0.5~1.5 圈),做成"龙在屏幕中央盘了
 *    一下又游走"的效果 — 这是用户原话"盘旋游动"的核心动作。
 * 4. 出场:沿主轴方向继续延伸到屏幕外,头的切线方向保持连贯,出场航向 =
 *    实际终点切线方向(at(1).angle),消费方据此把尾巴拖出屏外。
 *
 * 不依赖具体视口尺寸,生成时传入 (vw, vh),内部归一化。
 */

export interface FlightPath {
  /** 计算时刻 t∈[0,1] 时头的位置和切线方向(弧度)。 */
  at(t: number): { x: number; y: number; angle: number };
  /** 一次飞行的视觉时长(秒),路径越曲折越长。 */
  durationSec: number;
}

interface Pt {
  x: number;
  y: number;
}

/** Catmull-Rom 插值 — 4 个控制点之间用 t∈[0,1] 平滑插值。 */
function catmullRom(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt {
  const t2 = t * t;
  const t3 = t2 * t;
  return {
    x:
      0.5 *
      (2 * p1.x +
        (-p0.x + p2.x) * t +
        (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 +
        (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
    y:
      0.5 *
      (2 * p1.y +
        (-p0.y + p2.y) * t +
        (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 +
        (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3),
  };
}

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

/**
 * 随机锚点对 — 跟 MagpieFlock 的 makeFlightParams 同一套思路。
 *
 * 9 个锚点:4 角 + 4 边中点 + 中心。
 *   - 角 / 边锚点放屏外一截(余量 margin),让飞行物从屏外飞入、飞出屏外。
 *   - 中心锚点落在屏内,对应「从中间突然出现」。
 * 起点 / 终点要求够远(至少跨过大半屏),否则重挑,保证大方向够随机:
 * 左上→右下、右上→左下、左下→右上、中心→右下…各种组合都可能出现。
 *
 * @param vw 视口宽
 * @param vh 视口高
 * @param margin 角 / 边锚点放到屏外的余量(默认 200)
 */
function pickAnchorPair(
  vw: number,
  vh: number,
  margin = 200,
): { start: Pt; end: Pt } {
  const m = margin;
  const anchors: ReadonlyArray<Pt> = [
    { x: -m, y: -m }, // 左上(屏外)
    { x: vw + m, y: -m }, // 右上(屏外)
    { x: -m, y: vh + m }, // 左下(屏外)
    { x: vw + m, y: vh + m }, // 右下(屏外)
    { x: vw / 2, y: -m }, // 上中(屏外)
    { x: vw / 2, y: vh + m }, // 下中(屏外)
    { x: -m, y: vh / 2 }, // 左中(屏外)
    { x: vw + m, y: vh / 2 }, // 右中(屏外)
    { x: vw / 2, y: vh / 2 }, // 中心(屏内 —「从中间突然出现」)
  ];
  const pick = (): Pt => anchors[Math.floor(Math.random() * anchors.length)]!;

  const start = pick();
  let end = pick();
  // 起终点至少跨过大半屏,否则重挑(最多 12 次,够用)
  const minDist = Math.max(vw, vh) * 0.55;
  for (let guard = 0; guard < 12; guard += 1) {
    if (Math.hypot(end.x - start.x, end.y - start.y) >= minDist) break;
    end = pick();
  }
  return { start, end };
}

/**
 * 螺旋飞行路径 — 给凤凰用。
 *
 * 2026-05-20 改:旧版写死「从屏底中段盘旋上升、屏顶离场」,大方向永远朝上。
 * 改为先随机挑「起点锚点 → 终点锚点」(4 角 / 4 边中点 / 中心,够远),
 * 整条主轴随机化 —— 左上→右下、右下→左上、中心→右上…各种航向都可能。
 * 凤凰本身的「盘旋姿态」保留:沿这条随机主轴前进的同时,绕主轴做 2~2.5 圈
 * 螺旋,半径从大收紧到小,所以仍是「一边盘旋一边前进」的凤凰,不是直线平移。
 *
 * 实现:
 *   - 主轴 = start→end 的直线;axisDir 单位向量,perp 为其法向。
 *   - 位置 = 主轴上的线性插值 + 沿 perp 的 sin 偏移(螺旋的「横摆」)。
 *   - 螺旋幅度(横摆半径)沿程从 rMax 收紧到 rMin,首尾再各用一段
 *     ease 把幅度压到 0,保证从锚点干净飞入 / 飞出,不甩出大圈。
 */
function makeSpiralUpPath(vw: number, vh: number, speedScale: number): FlightPath {
  const { start, end } = pickAnchorPair(vw, vh);

  // 主轴向量 + 单位法向
  const axisX = end.x - start.x;
  const axisY = end.y - start.y;
  const axisLen = Math.hypot(axisX, axisY) || 1;
  const perpX = -axisY / axisLen;
  const perpY = axisX / axisLen;

  // 随机给螺旋一个起始相位角,避免每次进入姿态一样
  const phase0 = rand(0, Math.PI * 2);
  // 螺旋方向随机左右
  const dir = Math.random() < 0.5 ? 1 : -1;
  const turns = rand(2.0, 2.6);
  // 螺旋横摆幅度:随屏取一个相对值,沿程从大收紧到小
  const rMax = Math.min(vw, vh) * rand(0.16, 0.22);
  const rMin = Math.min(vw, vh) * 0.05;
  const ellipse = 0.62; // 副振幅压扁比,让螺旋看着有「前后透视」

  const sampler = (t: number): Pt => {
    // 主轴线性推进
    const baseX = start.x + axisX * t;
    const baseY = start.y + axisY * t;
    // 螺旋横摆幅度沿程收紧;首尾各 12% 用 ease 压到 0(干净飞入飞出)
    let ampScale = 1;
    if (t < 0.12) ampScale = t / 0.12;
    else if (t > 0.88) ampScale = (1 - t) / 0.12;
    const r = (rMax - t * (rMax - rMin)) * ampScale;
    const angle = phase0 + t * Math.PI * 2 * turns * dir;
    // 沿主轴法向做主振幅,沿主轴方向做副振幅(压扁) → 椭圆螺旋
    const offMain = Math.cos(angle) * r;
    const offSub = Math.sin(angle) * r * ellipse;
    return {
      x: baseX + perpX * offMain + (axisX / axisLen) * offSub,
      y: baseY + perpY * offMain + (axisY / axisLen) * offSub,
    };
  };

  const { table, totalLength } = buildArcLengthTable(sampler, 240);
  const speed = rand(420, 540) * speedScale;
  const durationSec = Math.max(9, Math.min(16, totalLength / speed));

  return {
    durationSec,
    at(t: number) {
      const tt = arcToT(table, totalLength, Math.max(0, Math.min(1, t)));
      const p = sampler(tt);
      const eps = 0.003;
      const a = sampler(Math.max(0, tt - eps));
      const b = sampler(Math.min(1, tt + eps));
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      return { x: p.x, y: p.y, angle };
    },
  };
}

/** 给路径采样并预算长度,便于 t 重映射成等速运动(避免速度忽快忽慢)。 */
function buildArcLengthTable(
  sampler: (t: number) => Pt,
  steps: number,
): { table: number[]; totalLength: number } {
  const table = [0];
  let prev = sampler(0);
  let total = 0;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const cur = sampler(t);
    total += Math.hypot(cur.x - prev.x, cur.y - prev.y);
    table.push(total);
    prev = cur;
  }
  return { table, totalLength: total };
}

/** 二分查 arc-length 反向映射:给定走过的弧长比例,返回原 t。 */
function arcToT(table: number[], totalLength: number, s: number): number {
  const target = s * totalLength;
  let lo = 0;
  let hi = table.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    // table 由 buildArcLengthTable 生成,长度固定 = steps+1,索引一定有值
    if ((table[mid] as number) < target) lo = mid + 1;
    else hi = mid;
  }
  // 线性插值到段内
  if (lo === 0) return 0;
  const a = table[lo - 1] as number;
  const b = table[lo] as number;
  const u = b - a > 0 ? (target - a) / (b - a) : 0;
  return (lo - 1 + u) / (table.length - 1);
}

export interface FlightOpts {
  /** 是否允许中段盘旋 — 龙=true,凤凰=false(凤凰滑翔不盘) */
  allowCoil?: boolean;
  /**
   * @deprecated 2026-05-20 起入 / 出锚点改为随机锚点对,y 不再由偏置决定。
   * 字段保留只为向后兼容,传值已无效果。
   */
  yBias?: number;
  /** 速度乘子,默认 1.0;凤凰用 1.15 略快 */
  speedScale?: number;
  /**
   * 路径模式:
   *   - 'sweep'(默认):随机锚点对之间斜穿全屏 + 可选中段盘旋
   *   - 'spiral-up':沿随机锚点对主轴前进,过程中绕主轴螺旋(凤凰盘旋姿态)
   */
  mode?: 'sweep' | 'spiral-up';
}

/**
 * 生成一条飞行路径。默认为龙的"穿越 + 中段盘旋"。
 *
 * @param vw 视口宽
 * @param vh 视口高
 * @param opts FlightOpts;不传走默认龙参数。
 */
export function makeFlightPath(
  vw: number,
  vh: number,
  opts: FlightOpts = {},
): FlightPath {
  const allowCoil = opts.allowCoil ?? true;
  const speedScale = opts.speedScale ?? 1.0;
  const mode = opts.mode ?? 'sweep';

  if (mode === 'spiral-up') {
    return makeSpiralUpPath(vw, vh, speedScale);
  }
  // —— 1. 主航点(屏幕坐标系,原点左上)
  // 2026-05-20 改:旧版起点写死 x:-200(屏左)、终点写死 x:vw+200(屏右),
  // 龙永远「左→右」横穿。改为在 9 个锚点(4 角 / 4 边中点 / 中心)里随机挑
  // 起点 + 终点(够远),整体航向随机 —— 左上→右下、右下→左上、中心→右上…
  // 龙身的蜿蜒(中段正弦航点)+ 中段盘旋下面照旧保留,只是两端锚点随机化,
  // 不会退化成直线平移。yBias 不再决定 y 分布,锚点已含屏外余量。
  const { start, end } = pickAnchorPair(vw, vh);
  // 主轴单位法向 — 中段正弦起伏沿「垂直于主轴」方向叠加,这样不管龙朝哪个
  // 方向飞,蜿蜒都横切航向(而不是只在 y 上摆),盘旋游动感跟旧版一致。
  const axisDX = end.x - start.x;
  const axisDY = end.y - start.y;
  const axisLen = Math.hypot(axisDX, axisDY) || 1;
  const nperpX = -axisDY / axisLen;
  const nperpY = axisDX / axisLen;

  // 中段 3~4 个航点,沿主轴法向做正弦叠加 → 蜿蜒
  const midCount = Math.floor(rand(3, 5));
  const mids: Pt[] = [];
  for (let i = 1; i <= midCount; i++) {
    const u = i / (midCount + 1);
    const baseX = start.x + axisDX * u;
    const baseY = start.y + axisDY * u;
    const span = Math.min(vw, vh);
    const wave = Math.sin(u * Math.PI * rand(1.4, 2.6)) * span * rand(0.12, 0.22);
    const wave2 = Math.cos(u * Math.PI * rand(2.0, 3.4)) * span * 0.06;
    const off = wave + wave2;
    mids.push({ x: baseX + nperpX * off, y: baseY + nperpY * off });
  }

  // 盘旋(只在 allowCoil 且 55% 概率)— 在第二个航点附近插入一个螺旋段
  const coil = allowCoil && Math.random() < 0.55;
  const midIdx = Math.floor(midCount / 2);
  const midPt = mids[midIdx] ?? mids[0] ?? { x: vw / 2, y: vh / 2 };
  const coilCenter: Pt | null = coil ? { x: midPt.x, y: midPt.y } : null;
  const coilTurns = coil ? rand(0.8, 1.6) : 0;
  const coilRadius = coil ? rand(70, 140) : 0;

  // —— 2. Catmull-Rom 主曲线(无盘旋部分)
  const allPts: Pt[] = [start, ...mids, end];
  // 首尾各加一个延伸虚拟点,避免端点切线偏。
  // 沿主轴方向延伸(不再写死 x±100),保证任意航向下端点切线都连贯,
  // 龙尾出场也就能沿真实终点航向干净离场,不会留在屏内。
  const axisUX = axisDX / axisLen;
  const axisUY = axisDY / axisLen;
  const phantomStart: Pt = { x: start.x - axisUX * 100, y: start.y - axisUY * 100 };
  const phantomEnd: Pt = { x: end.x + axisUX * 100, y: end.y + axisUY * 100 };
  const cps: Pt[] = [phantomStart, ...allPts, phantomEnd];

  // 主曲线采样函数:t∈[0,1] 映射到 Catmull-Rom 总参数空间
  const segCount = cps.length - 3;
  const mainAt = (t: number): Pt => {
    const u = Math.max(0, Math.min(0.9999, t)) * segCount;
    const idx = Math.floor(u);
    const local = u - idx;
    // idx 取值范围保证 idx, idx+1, idx+2, idx+3 都在 cps 范围内
    return catmullRom(
      cps[idx] as Pt,
      cps[idx + 1] as Pt,
      cps[idx + 2] as Pt,
      cps[idx + 3] as Pt,
      local,
    );
  };

  // —— 3. 盘旋叠加:在 t∈[coilT0, coilT1] 区段把主轨迹平滑替换为螺旋绕 center
  // 实现:在该区间把 main 位置和螺旋位置按 ease in-out 加权混合,避免突然抖动
  const coilT0 = 0.38;
  const coilT1 = 0.62;
  const sampler = (t: number): Pt => {
    const main = mainAt(t);
    if (!coil || !coilCenter || t < coilT0 || t > coilT1) return main;
    const u = (t - coilT0) / (coilT1 - coilT0); // 0..1 段内
    // ease in-out:进入慢慢混入螺旋,中段全螺旋,退出再混回主轨
    const w =
      u < 0.5
        ? 4 * u * u * u
        : 1 - Math.pow(-2 * u + 2, 3) / 2;
    const angle = u * Math.PI * 2 * coilTurns;
    const r = coilRadius * (1 - 0.4 * Math.abs(u - 0.5) * 2);
    const sx = coilCenter.x + Math.cos(angle) * r;
    const sy = coilCenter.y + Math.sin(angle) * r * 0.85; // 略压扁,视觉更像"在空中盘旋"
    return {
      x: main.x * (1 - w) + sx * w,
      y: main.y * (1 - w) + sy * w,
    };
  };

  // —— 4. 等速重参数化
  const { table, totalLength } = buildArcLengthTable(sampler, 200);
  // 把弧长除速度算出时长 — 屏幕越大、路径越绕,飞越久
  const speed = rand(380, 520) * speedScale; // px/sec
  const durationSec = Math.max(7, Math.min(14, totalLength / speed));

  // —— 5. 暴露 at()
  return {
    durationSec,
    at(t: number) {
      const tt = arcToT(table, totalLength, Math.max(0, Math.min(1, t)));
      const p = sampler(tt);
      // 切线方向用前后差分
      const eps = 0.003;
      const a = sampler(Math.max(0, tt - eps));
      const b = sampler(Math.min(1, tt + eps));
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      return { x: p.x, y: p.y, angle };
    },
  };
}
