/**
 * 察元办公 —— 流程图 + 拓扑图。
 *
 * 流程图(业务流程):把文档收进云端库 → 一键向量化入库 → 在底部对话框对当前
 *   目录范围做检索 → AI 定位 / 打开 / 跳转 → 选多篇跑批量助手(脱密 / 摘要 / 语法)。
 * 拓扑图(模块关系):察元办公前端 ↔ 服务端办公库 / 向量化服务 / 统一 KB 查询 ↔
 *   对话与嵌入模型、office 知识源、对象存储。
 *
 * 数据依据:chayuan-client OfficePage.tsx(三库归一 / 嵌套分组 / 批量助手 /
 *   向量化 batch-status / 知识对话模式)与 WPS KbEmptyTopology「察元办公」节点。
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

/** 办公业务流程:从收文到批量治理的一条主链。 */
export const OfficeFlowDiagram: React.FC = () => (
  <StepFlow
    caption="文档治理流程 · 从收文到批量处理"
    steps={[
      { label: '上传归档', sub: '我的 / 共享 / 回收' },
      { label: '向量化入库', sub: '一键建索引' },
      { label: '目录内检索', sub: '文件名+语义融合' },
      { label: 'AI 定位跳转', sub: '打开到具体位置' },
      { label: '批量助手', sub: '脱密/摘要/语法' },
    ]}
  />
);

/** 办公拓扑:前端 / 服务端 / 模型与存储 三层。 */
export const OfficeTopologyDiagram: React.FC = () => (
  <svg {...svgProps(TOPO_VB_W, TOPO_VB_H, '察元办公模块拓扑示意图')}>
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

    {/* 三个分层泳道 */}
    <Lane x={10} y={26} w={340} h={52} label="客户端" />
    <Lane x={10} y={92} w={340} h={56} label="服务端" />
    <Lane x={10} y={162} w={340} h={58} label="模型与存储" />

    {/* 客户端层:察元办公前端 */}
    <Box x={120} y={40} w={120} h={28} label="察元办公前端" sub="文档库 · 知识对话" tone="brand" />

    {/* 服务端层:三个服务 */}
    <Box x={26} y={106} w={94} h={30} label="办公文档服务" sub="三库 / 分组 / 批量" />
    <Box x={133} y={106} w={94} h={30} label="文档向量化" sub="解析 / 切片 / 嵌入" />
    <Box x={240} y={106} w={100} h={30} label="统一 KB 查询" sub="kb-query/search" tone="accent" />

    {/* 模型与存储层 */}
    <Box x={26} y={176} w={86} h={30} label="对话模型" sub="改写 / 摘要" />
    <Box x={124} y={176} w={86} h={30} label="嵌入模型" sub="文本向量" />
    <Box x={222} y={176} w={56} h={30} label="office 库" sub="向量索引" tone="accent" />
    <Box x={288} y={176} w={52} h={30} label="对象存储" sub="原文件" tone="accent" />

    {/* 连线:前端 → 三个服务 */}
    <Link
      points={[
        [150, 68],
        [73, 106],
      ]}
      brand
    />
    <Link
      points={[
        [180, 68],
        [180, 106],
      ]}
      brand
    />
    <Link
      points={[
        [210, 68],
        [290, 106],
      ]}
      brand
    />

    {/* 服务端内部:向量化 → office 库;查询 → office 库 */}
    <Link
      points={[
        [180, 136],
        [250, 176],
      ]}
    />
    <Link
      points={[
        [290, 136],
        [255, 176],
      ]}
    />
    {/* 文档服务 → 对象存储 */}
    <Link
      points={[
        [73, 136],
        [69, 176],
      ]}
    />
    {/* 向量化 → 嵌入模型 */}
    <Link
      points={[
        [160, 136],
        [160, 176],
      ]}
    />
    {/* 查询 → 对话模型(生成答案) */}
    <Link
      points={[
        [260, 136],
        [80, 172],
      ]}
      dashed
    />

    <Caption x={150} y={219} text="实线 = 主数据流   虚线 = 答案生成回链" anchor="middle" />
  </svg>
);
