/**
 * 训练数据中心 —— 流程图 + 拓扑图。
 *
 * 流程图(标注流转):采样生成样本 → 领取标注任务 → 提交标注 → 复审(通过/驳回/
 *   冲突处理)→ 发布为数据集 / 问答挂载,带「质量反哺」回流。
 * 拓扑图(模块关系):训练数据中心前端 ↔ 样本采集 / 标注流转 / 数据集服务,
 *   样本来源(问答路由上下文、JSON/CSV 导入),挂载产物注入问答检索链路。
 *
 * 数据依据:chayuan-client AnnotationPage.tsx(pending/in_progress/submitted/
 *   approved/rejected/conflict 标注状态机,rag_relevance / office_action_review
 *   任务类型,模板样例 + 导入导出)、data-mounts(问答挂载发布)、
 *   单机版 onlineFeatures.ts annotation 段。
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

/** 训练数据中心业务流程:采集 → 标注 → 复审 → 发布的标注流转。 */
export const AnnotationFlowDiagram: React.FC = () => (
  <StepFlow
    caption="标注流转流程 · 采集到发布"
    steps={[
      { label: '采样生成', sub: '路由上下文 / 导入' },
      { label: '领取任务', sub: '待处理 → 处理中' },
      { label: '提交标注', sub: '相关性 / 动作复审' },
      { label: '复审裁决', sub: '通过 / 驳回 / 冲突' },
      { label: '发布挂载', sub: '数据集 / 问答挂载' },
    ]}
    loopBack={{ label: '质量反哺:挂载样本注入问答,持续提升回答质量' }}
  />
);

/** 训练数据中心拓扑:标注前端 / 数据服务 / 样本来源与挂载去向。 */
export const AnnotationTopologyDiagram: React.FC = () => (
  <svg {...svgProps(TOPO_VB_W, TOPO_VB_H, '训练数据中心模块拓扑示意图')}>
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

    {/* 左:样本来源 */}
    <Lane x={10} y={26} w={96} h={138} label="样本来源" />
    <Box x={20} y={44} w={76} h={30} label="问答路由" sub="上下文采样" tone="accent" />
    <Box x={20} y={82} w={76} h={30} label="JSON/CSV" sub="批量导入" tone="accent" />
    <Box x={20} y={120} w={76} h={30} label="模板样例" sub="规范结构" tone="accent" />

    {/* 中:训练数据中心服务 */}
    <Lane x={118} y={26} w={134} h={138} label="训练数据中心" />
    <Box x={128} y={42} w={114} h={26} label="训练数据中心前端" sub="标注台" tone="brand" />
    <Box x={128} y={78} w={114} h={26} label="样本池" sub="采集 / 去重" />
    <Box x={128} y={112} w={114} h={26} label="标注流转" sub="领取·提交·复审·冲突" />

    {/* 右:产物去向 */}
    <Lane x={264} y={26} w={86} h={138} label="产物去向" />
    <Box x={272} y={50} w={70} h={32} label="数据集" sub="导出 / 评测" tone="success" />
    <Box x={272} y={102} w={70} h={32} label="问答挂载" sub="偏好 / 排序" tone="success" />

    {/* 来源 → 样本池 */}
    <Link
      points={[
        [96, 59],
        [128, 88],
      ]}
    />
    <Link
      points={[
        [96, 97],
        [128, 91],
      ]}
    />
    <Link
      points={[
        [96, 135],
        [128, 94],
      ]}
    />
    {/* 样本池 → 标注流转 → 产物 */}
    <Link
      points={[
        [185, 104],
        [185, 112],
      ]}
      brand
    />
    <Link
      points={[
        [242, 120],
        [272, 70],
      ]}
      brand
    />
    <Link
      points={[
        [242, 124],
        [272, 116],
      ]}
      brand
    />

    {/* 问答挂载 → 问答检索链路(反哺) */}
    <Box x={96} y={188} w={168} h={24} label="问答检索链路(回答时自动注入)" tone="accent" />
    <Link
      points={[
        [307, 134],
        [307, 200],
        [264, 200],
      ]}
      dashed
    />

    <Caption x={150} y={224} text="实线 = 标注主流   虚线 = 标注成果反哺问答质量" anchor="middle" />
  </svg>
);
