/**
 * 应用中心 —— 流程图 + 拓扑图。
 *
 * 流程图(发现复用流程):浏览公开应用市场 → 按分类/关键词检索 → 打开公开运行页
 *   直接体验 → 「复制为我的」一键 fork → 在智能空间继续改造,带「再发布回市场」回流。
 * 拓扑图(模块关系):应用中心前端 ↔ 市场目录 / 公开运行 / fork 复制服务,
 *   公开应用经鉴权后跑运行时,fork 产物落到本人的智能空间。
 *
 * 数据依据:chayuan-client AppGalleryPage.tsx(scope=public 应用市场 Tab,
 *   keyword 搜索)、PublicAppPage.tsx(走 slug 的公开运行页 + 鉴权)、
 *   WPS KbEmptyTopology「应用市场」节点(发布/订阅/复用)。
 */

import * as React from 'react';
import {
  ArrowDefs,
  Box,
  Caption,
  Lane,
  Link,
  StepFlow,
  TOPO_VB_H,
  TOPO_VB_W,
  svgProps,
} from './diagramKit';

/** 应用中心业务流程:发现 → 体验 → 复用的一条动线。 */
export const MarketFlowDiagram: React.FC = () => (
  <StepFlow
    caption="发现与复用流程 · 先逛后用"
    steps={[
      { label: '浏览市场', sub: '公开应用画廊' },
      { label: '分类检索', sub: '问答/智能体/流程' },
      { label: '公开页体验', sub: '直接试运行' },
      { label: '复制为我的', sub: '一键 fork' },
      { label: '继续改造', sub: '进我的智能空间' },
    ]}
    loopBack={{ label: '改造完成 → 可再发布回应用市场,沉淀团队最佳实践' }}
  />
);

/** 应用中心拓扑:发现侧前端 / 市场服务 / 与智能空间的衔接。 */
export const MarketTopologyDiagram: React.FC = () => (
  <svg {...svgProps(TOPO_VB_W, TOPO_VB_H, '应用中心模块拓扑示意图')}>
    <ArrowDefs />
    <rect
      x={0.5}
      y={0.5}
      width={TOPO_VB_W - 1}
      height={TOPO_VB_H - 1}
      rx={10}
      fill="var(--cy-surface-2)"
      stroke="var(--cy-border-subtle)"
    />

    <Lane x={10} y={24} w={340} h={46} label="客户端" />
    <Lane x={10} y={84} w={340} h={50} label="应用中心服务" />
    <Lane x={10} y={148} w={340} h={44} label="运行与衔接" />

    {/* 客户端 */}
    <Box x={120} y={34} w={120} h={26} label="应用中心前端" sub="浏览 · 搜索 · 体验" tone="brand" />

    {/* 应用中心服务 */}
    <Box x={28} y={94} w={92} h={30} label="市场目录" sub="公开应用陈列" />
    <Box x={134} y={94} w={92} h={30} label="检索 / 分类" sub="关键词 + 类型" />
    <Box x={240} y={94} w={100} h={30} label="fork 复制" sub="复制为我的" tone="accent" />

    {/* 运行与衔接 */}
    <Box x={36} y={158} w={96} h={26} label="公开运行页" sub="slug + 鉴权" />
    <Box x={148} y={158} w={84} h={26} label="应用运行时" sub="对话 / 流程" />
    <Box x={248} y={158} w={96} h={26} label="我的智能空间" sub="改造与再发布" tone="success" />

    {/* 前端 → 服务 */}
    <Link
      points={[
        [150, 60],
        [74, 94],
      ]}
      brand
    />
    <Link
      points={[
        [180, 60],
        [180, 94],
      ]}
      brand
    />
    <Link
      points={[
        [210, 60],
        [290, 94],
      ]}
      brand
    />

    {/* 目录/检索 → 公开运行;公开运行 → 运行时 */}
    <Link
      points={[
        [100, 124],
        [84, 158],
      ]}
    />
    <Link
      points={[
        [180, 124],
        [110, 158],
      ]}
    />
    <Link
      points={[
        [132, 171],
        [148, 171],
      ]}
    />
    {/* fork → 我的智能空间 */}
    <Link
      points={[
        [290, 124],
        [296, 158],
      ]}
      brand
    />

    <Caption
      x={150}
      y={206}
      text="公开应用「先体验、看中再 fork」到自己的空间继续改"
      anchor="middle"
    />
    <Caption x={150} y={219} text="实线 = 主数据流   品牌色 = fork 复用动线" anchor="middle" />
  </svg>
);
