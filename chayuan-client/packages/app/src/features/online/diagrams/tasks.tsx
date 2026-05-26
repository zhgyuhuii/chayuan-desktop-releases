/**
 * 我的待办 —— 流程图 + 拓扑图。
 *
 * 流程图(人工节点流转):AI 流程跑到人工节点 → 挂起持久化并派单 → 站内/邮件通知
 *   到人 → 人对话式处理并提交 → 表单数据回注、流程从断点续跑。
 * 拓扑图(模块关系):我的待办前端 ↔ 流程引擎 / 人工任务服务 / 通知服务,
 *   人工节点来自智能空间应用,处理时调对话模型抽取表单字段。
 *
 * 数据依据:单机版 onlineFeatures.ts tasks 段(human_task 挂起持久化、指派策略、
 *   对话式表单、四视图、截止置顶)、TasklistMockups.tsx 已有的三张界面示意图。
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

/** 我的待办业务流程:人工节点挂起 → 派单 → 处理 → 续跑。 */
export const TasksFlowDiagram: React.FC = () => (
  <StepFlow
    caption="人机协同流程 · 人工节点处理链路"
    steps={[
      { label: '流程到人工节点', sub: 'human_task' },
      { label: '挂起并派单', sub: '持久化 + 指派策略' },
      { label: '通知到人', sub: '站内 / 邮件' },
      { label: '对话式处理', sub: 'AI 填表 + 确认' },
      { label: '提交即续跑', sub: '回注流程状态' },
    ]}
    loopBack={{ label: '可取消 / 退回:关键决策卡在人这里,不被 AI 蒙混过去' }}
  />
);

/** 我的待办拓扑:待办前端 / 流程与任务服务 / 应用与模型依赖。 */
export const TasksTopologyDiagram: React.FC = () => (
  <svg {...svgProps(TOPO_VB_W, TOPO_VB_H, '我的待办模块拓扑示意图')}>
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

    <Lane x={10} y={24} w={340} h={44} label="客户端" />
    <Lane x={10} y={82} w={340} h={50} label="协同服务" />
    <Lane x={10} y={146} w={340} h={44} label="依赖能力" />

    {/* 客户端 */}
    <Box x={40} y={34} w={124} h={24} label="我的待办收件箱" sub="四视图 · 截止置顶" tone="brand" />
    <Box
      x={196}
      y={34}
      w={124}
      h={24}
      label="对话式表单处理页"
      sub="对话 + 嵌入式表单"
      tone="brand"
    />
    <Link
      points={[
        [164, 46],
        [196, 46],
      ]}
    />

    {/* 协同服务 */}
    <Box x={26} y={92} w={86} h={30} label="流程引擎" sub="挂起 / 续跑" />
    <Box x={124} y={92} w={86} h={30} label="人工任务服务" sub="派单 / 认领 / 提交" />
    <Box x={222} y={92} w={118} h={30} label="通知服务" sub="站内 / 邮件" />

    {/* 依赖能力 */}
    <Box x={34} y={156} w={96} h={26} label="智能空间应用" sub="放置人工节点" tone="accent" />
    <Box x={146} y={156} w={96} h={26} label="对话模型" sub="抽取表单字段" tone="accent" />
    <Box x={258} y={156} w={86} h={26} label="任务存储" sub="表单 / 状态" tone="accent" />

    {/* 前端 → 服务 */}
    <Link
      points={[
        [100, 58],
        [80, 92],
      ]}
      brand
    />
    <Link
      points={[
        [258, 58],
        [167, 92],
      ]}
      brand
    />

    {/* 服务内部 */}
    <Link
      points={[
        [112, 107],
        [124, 107],
      ]}
    />
    <Link
      points={[
        [210, 107],
        [222, 107],
      ]}
    />
    {/* 应用 → 流程引擎(人工节点定义来源) */}
    <Link
      points={[
        [82, 156],
        [69, 122],
      ]}
      dashed
    />
    {/* 处理页 → 对话模型 */}
    <Link
      points={[
        [258, 58],
        [194, 156],
      ]}
      dashed
    />
    {/* 人工任务服务 → 任务存储 */}
    <Link
      points={[
        [180, 122],
        [290, 156],
      ]}
    />

    <Caption
      x={150}
      y={206}
      text="流程引擎把人工节点挂起,人工任务服务派单到收件箱"
      anchor="middle"
    />
    <Caption x={150} y={219} text="实线 = 主数据流   虚线 = 节点定义 / 模型抽取" anchor="middle" />
  </svg>
);
