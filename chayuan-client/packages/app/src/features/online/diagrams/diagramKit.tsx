/**
 * diagramKit —— 在线办公功能介绍页「流程图 / 拓扑图」的共享 SVG 画法。
 *
 * 为什么要这个 kit:
 *   5 个在线功能各要 1 张流程图 + 1 张拓扑图,共 10 张。如果每张从零雕 SVG
 *   会重复、风格漂移。这里把「方框 / 圆角节点 / 箭头 / 连线 / 文字 / 泳道」
 *   等基元抽成可复用组件,每张图只用这些基元拼装,风格天然统一。
 *
 * 约定:
 *   - 流程图(Flow):方框 + 箭头,表达「用户怎么一步步用、数据怎么流」。
 *   - 拓扑图(Topology):节点 + 连线,表达「前端 ↔ 服务端 ↔ 模型/存储」模块关系。
 *   - 所有上色只用 var(--cy-*) 主题变量,深浅色主题都能看;不引外链网图。
 *   - 画布统一 viewBox,组件按比例自适应容器宽度。
 *
 * 用法:见 office.tsx / space.tsx 等各功能图文件。
 */

import * as React from 'react';

/** 流程图画布:横向步骤流,16:7 左右比例,宽一点放得下 4~5 个步骤。 */
export const FLOW_VB_W = 360;
export const FLOW_VB_H = 188;

/** 拓扑图画布:接近正方偏宽,放得下分层节点网络。 */
export const TOPO_VB_W = 360;
export const TOPO_VB_H = 230;

/** SVG 根元素公共属性。 */
export function svgProps(w: number, h: number, label: string) {
  return {
    viewBox: `0 0 ${w} ${h}`,
    xmlns: 'http://www.w3.org/2000/svg',
    className: 'block h-auto w-full',
    role: 'img' as const,
    'aria-label': label,
  };
}

/** 箭头三角:连线两端共用的 marker。每张图的 <defs> 引一次。 */
export const ArrowDefs: React.FC<{ id?: string }> = ({ id = 'cyArrow' }) => (
  <defs>
    <marker
      id={id}
      viewBox="0 0 10 10"
      refX={8}
      refY={5}
      markerWidth={6}
      markerHeight={6}
      orient="auto-start-reverse"
    >
      <path d="M0 0L10 5L0 10z" fill="var(--cy-text-tertiary)" />
    </marker>
    <marker
      id={`${id}Brand`}
      viewBox="0 0 10 10"
      refX={8}
      refY={5}
      markerWidth={6}
      markerHeight={6}
      orient="auto-start-reverse"
    >
      <path d="M0 0L10 5L0 10z" fill="var(--cy-brand-500)" />
    </marker>
  </defs>
);

type Tone = 'plain' | 'brand' | 'accent' | 'danger' | 'success';

/** tone → 填充 / 描边 颜色。accent 用次要面色,danger/success 用语义色。 */
function toneColors(tone: Tone): { fill: string; stroke: string; text: string } {
  switch (tone) {
    case 'brand':
      return { fill: 'var(--cy-brand-500)', stroke: 'var(--cy-brand-600)', text: '#fff' };
    case 'accent':
      return {
        fill: 'var(--cy-surface-2)',
        stroke: 'var(--cy-border-default)',
        text: 'var(--cy-text-primary)',
      };
    case 'danger':
      return {
        fill: 'var(--cy-surface-1)',
        stroke: 'var(--cy-danger-500)',
        text: 'var(--cy-danger-600)',
      };
    case 'success':
      return {
        fill: 'var(--cy-surface-1)',
        stroke: 'var(--cy-success-500)',
        text: 'var(--cy-success-600)',
      };
    default:
      return {
        fill: 'var(--cy-surface-1)',
        stroke: 'var(--cy-border-default)',
        text: 'var(--cy-text-primary)',
      };
  }
}

/** 把一段文字按字数粗略折行(SVG <text> 不自动换行)。 */
function wrapText(text: string, perLine: number): string[] {
  if (text.length <= perLine) return [text];
  const lines: string[] = [];
  for (let i = 0; i < text.length; i += perLine) {
    lines.push(text.slice(i, i + perLine));
  }
  return lines;
}

/**
 * 圆角方框节点(流程图步骤 / 拓扑图节点共用)。
 * label 居中两行内自动折行;sub 是更小的副标题。
 */
export const Box: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub?: string;
  tone?: Tone;
  /** label 每行字数,默认按框宽估算 */
  perLine?: number;
  rx?: number;
}> = ({ x, y, w, h, label, sub, tone = 'plain', perLine, rx = 8 }) => {
  const c = toneColors(tone);
  const lines = wrapText(label, perLine ?? Math.max(4, Math.floor(w / 9)));
  const cx = x + w / 2;
  // 文字块整体在框内垂直居中;有 sub 时上移给 sub 留位。
  const lineH = 11;
  const blockH = lines.length * lineH + (sub ? 9 : 0);
  let ty = y + (h - blockH) / 2 + 8;
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={rx}
        fill={c.fill}
        stroke={c.stroke}
        strokeWidth={tone === 'brand' ? 0 : 1.2}
      />
      {lines.map((ln, i) => {
        const yy = ty + i * lineH;
        return (
          <text
            key={i}
            x={cx}
            y={yy}
            textAnchor="middle"
            fontSize={9}
            fontWeight={600}
            fill={c.text}
          >
            {ln}
          </text>
        );
      })}
      {sub ? (
        <text
          x={cx}
          y={ty + lines.length * lineH + 1}
          textAnchor="middle"
          fontSize={7}
          fill={tone === 'brand' ? 'rgba(255,255,255,0.82)' : 'var(--cy-text-tertiary)'}
        >
          {sub}
        </text>
      ) : null}
    </g>
  );
};

/**
 * 直线 / 折线连接箭头。
 * points 至少 2 个点;dashed 表示反馈 / 旁路;brand 用品牌色强调主流。
 */
export const Link: React.FC<{
  points: ReadonlyArray<[number, number]>;
  dashed?: boolean;
  brand?: boolean;
  /** 是否画箭头(默认画),拓扑里某些对等连线可关掉 */
  arrow?: boolean;
  arrowId?: string;
}> = ({ points, dashed = false, brand = false, arrow = true, arrowId = 'cyArrow' }) => {
  const d = points.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px} ${py}`).join(' ');
  return (
    <path
      d={d}
      fill="none"
      stroke={brand ? 'var(--cy-brand-500)' : 'var(--cy-text-tertiary)'}
      strokeWidth={1.5}
      strokeOpacity={brand ? 0.9 : 0.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray={dashed ? '4 3' : undefined}
      markerEnd={arrow ? `url(#${brand ? `${arrowId}Brand` : arrowId})` : undefined}
    />
  );
};

/** 连线中点上的小文字标签(说明这条边代表什么)。 */
export const EdgeLabel: React.FC<{
  x: number;
  y: number;
  text: string;
  tone?: 'plain' | 'brand';
}> = ({ x, y, text, tone = 'plain' }) => (
  <text
    x={x}
    y={y}
    textAnchor="middle"
    fontSize={6.5}
    fill={tone === 'brand' ? 'var(--cy-brand-600)' : 'var(--cy-text-tertiary)'}
  >
    {text}
  </text>
);

/** 图内小标题 / 分区标签。 */
export const Caption: React.FC<{
  x: number;
  y: number;
  text: string;
  anchor?: 'start' | 'middle' | 'end';
  tone?: 'plain' | 'brand' | 'danger';
}> = ({ x, y, text, anchor = 'start', tone = 'plain' }) => (
  <text
    x={x}
    y={y}
    textAnchor={anchor}
    fontSize={7}
    fontWeight={600}
    letterSpacing={0.3}
    fill={
      tone === 'brand'
        ? 'var(--cy-brand-600)'
        : tone === 'danger'
          ? 'var(--cy-danger-600)'
          : 'var(--cy-text-tertiary)'
    }
  >
    {text}
  </text>
);

/** 拓扑分层 / 流程泳道的浅色底框。 */
export const Lane: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  label?: string;
}> = ({ x, y, w, h, label }) => (
  <g>
    <rect
      x={x}
      y={y}
      width={w}
      height={h}
      rx={9}
      fill="var(--cy-surface-base)"
      stroke="var(--cy-border-subtle)"
      strokeDasharray="3 3"
    />
    {label ? (
      <text x={x + 6} y={y + 11} fontSize={6.5} fontWeight={600} fill="var(--cy-text-tertiary)">
        {label}
      </text>
    ) : null}
  </g>
);

/**
 * 流程图通用骨架:横向均匀排布的步骤方框 + 步骤间箭头。
 * 多数功能的「业务流程」就是一条顺序链,直接用它即可。
 */
export const StepFlow: React.FC<{
  steps: ReadonlyArray<{ label: string; sub?: string; tone?: Tone }>;
  /** 顶部说明文字 */
  caption?: string;
  /** 末步回到首步的反馈虚线(闭环) */
  loopBack?: { label?: string };
}> = ({ steps, caption, loopBack }) => {
  const n = steps.length;
  const padX = 12;
  const gap = 12;
  const rowY = loopBack ? 70 : 78;
  const boxH = 56;
  const boxW = (FLOW_VB_W - padX * 2 - gap * (n - 1)) / n;
  return (
    <svg {...svgProps(FLOW_VB_W, FLOW_VB_H, caption ?? '业务流程示意图')}>
      <ArrowDefs />
      <rect
        x={0.5}
        y={0.5}
        width={FLOW_VB_W - 1}
        height={FLOW_VB_H - 1}
        rx={10}
        fill="var(--cy-surface-2)"
        stroke="var(--cy-border-subtle)"
      />
      {caption ? <Caption x={padX} y={22} text={caption} tone="brand" /> : null}
      {steps.map((s, i) => {
        const x = padX + i * (boxW + gap);
        return (
          <g key={i}>
            <Box
              x={x}
              y={rowY}
              w={boxW}
              h={boxH}
              label={s.label}
              sub={s.sub}
              tone={s.tone ?? (i === 0 ? 'brand' : 'plain')}
              perLine={Math.max(4, Math.floor(boxW / 9))}
            />
            {/* 步骤序号小圆点 */}
            <circle
              cx={x + 9}
              cy={rowY - 6}
              r={7}
              fill="var(--cy-brand-500)"
              opacity={s.tone === 'accent' ? 0.4 : 1}
            />
            <text
              x={x + 9}
              y={rowY - 3.2}
              textAnchor="middle"
              fontSize={7.5}
              fontWeight={700}
              fill="#fff"
            >
              {i + 1}
            </text>
            {i < n - 1 ? (
              <Link
                points={[
                  [x + boxW, rowY + boxH / 2],
                  [x + boxW + gap, rowY + boxH / 2],
                ]}
                brand
              />
            ) : null}
          </g>
        );
      })}
      {loopBack ? (
        <g>
          {/* 末步 → 首步 的反馈回流虚线,从底部绕回 */}
          <Link
            points={[
              [padX + (n - 1) * (boxW + gap) + boxW / 2, rowY + boxH],
              [padX + (n - 1) * (boxW + gap) + boxW / 2, rowY + boxH + 30],
              [padX + boxW / 2, rowY + boxH + 30],
              [padX + boxW / 2, rowY + boxH],
            ]}
            dashed
          />
          {loopBack.label ? (
            <EdgeLabel x={FLOW_VB_W / 2} y={rowY + boxH + 27} text={loopBack.label} />
          ) : null}
        </g>
      ) : null}
    </svg>
  );
};
