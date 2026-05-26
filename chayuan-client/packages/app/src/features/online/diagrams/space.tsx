/**
 * 智能空间 —— 流程图 + 拓扑图。
 *
 * 流程图(搭建流程):选模板起步 → 配提示词/知识库/工具/模型 → 调试预览 →
 *   发布出版本 → 分享/上架,并带「迭代回流」反馈虚线(改 → 存草稿 → 再发版)。
 * 拓扑图(模块关系):应用工作台前端 ↔ 应用 CRUD / 草稿 / 版本 / 运行时服务 ↔
 *   知识库、工具、模型,产物进应用库供应用中心与我的待办消费。
 *
 * 数据依据:chayuan-client AppGalleryPage.tsx(我的/共享/市场三 Tab,模板,
 *   listApps/getDraft/saveDraft)、AppStudioPage.tsx(五区:提示词/知识库/工具/
 *   测试集/发布,lifecycle+草稿+版本)。
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

/** 智能空间业务流程:从模板到发布的搭建链,带迭代回流。 */
export const SpaceFlowDiagram: React.FC = () => (
  <StepFlow
    caption="AI 应用搭建流程 · 低代码全流程在线"
    steps={[
      { label: '选模板起步', sub: '问答/智能体/工作流' },
      { label: '配置应用', sub: '提示词·知识库·工具·模型' },
      { label: '调试预览', sub: '草稿即时验证' },
      { label: '发布版本', sub: '草稿→正式版本' },
      { label: '分享上架', sub: '同事 / 应用市场' },
    ]}
    loopBack={{ label: '迭代回流:改配置 → 存草稿 → 再发新版本' }}
  />
);

/** 智能空间拓扑:工作台前端 / 应用服务 / 能力依赖 + 产物去向。 */
export const SpaceTopologyDiagram: React.FC = () => (
  <svg {...svgProps(TOPO_VB_W, TOPO_VB_H, '智能空间模块拓扑示意图')}>
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
    <Lane x={10} y={82} w={340} h={48} label="应用服务" />
    <Lane x={10} y={144} w={340} h={44} label="能力依赖" />

    {/* 客户端 */}
    <Box x={42} y={34} w={120} h={24} label="应用画廊" sub="我的/共享/市场" />
    <Box x={196} y={34} w={120} h={24} label="应用工作台" sub="五区配置" tone="brand" />
    <Link
      points={[
        [162, 46],
        [196, 46],
      ]}
    />

    {/* 应用服务 */}
    <Box x={26} y={92} w={78} h={30} label="应用 CRUD" sub="lifecycle" />
    <Box x={114} y={92} w={78} h={30} label="草稿 / 版本" sub="保存 / 回滚" />
    <Box x={202} y={92} w={78} h={30} label="运行时" sub="对话 / 流程执行" />
    <Box x={290} y={92} w={52} h={30} label="发布" sub="鉴权 / Key" tone="accent" />

    {/* 能力依赖 */}
    <Box x={30} y={154} w={88} h={26} label="知识库" sub="doc / src / vec" tone="accent" />
    <Box x={136} y={154} w={88} h={26} label="工具 / MCP" sub="检索 / SQL / API" tone="accent" />
    <Box x={242} y={154} w={88} h={26} label="模型网关" sub="多厂商路由" tone="accent" />

    {/* 客户端 → 服务 */}
    <Link
      points={[
        [100, 58],
        [70, 92],
      ]}
      brand
    />
    <Link
      points={[
        [256, 58],
        [240, 92],
      ]}
      brand
    />
    <Link
      points={[
        [256, 58],
        [150, 92],
      ]}
      brand
    />

    {/* 运行时 → 能力依赖 */}
    <Link
      points={[
        [225, 122],
        [110, 154],
      ]}
    />
    <Link
      points={[
        [235, 122],
        [180, 154],
      ]}
    />
    <Link
      points={[
        [255, 122],
        [280, 154],
      ]}
    />

    {/* 产物去向:发布 → 应用库(供应用中心 / 我的待办) */}
    <Box x={120} y={200} w={120} h={22} label="应用库 → 应用中心 · 我的待办" tone="success" />
    <Link
      points={[
        [316, 122],
        [316, 195],
        [220, 211],
      ]}
      dashed
    />

    <Caption x={36} y={216} text="发布产物" tone="brand" />
  </svg>
);
