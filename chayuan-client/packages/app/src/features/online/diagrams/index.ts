/**
 * 在线办公功能介绍页用的「流程图 / 拓扑图」注册表。
 *
 * 每个在线功能各有 1 张流程图 + 1 张拓扑图,手绘 SVG(见同目录各功能文件)。
 * onlineFeatures.ts 的 RichSection 用字符串 id 引用这里的组件,OnlineFeaturePage
 * 渲染时按 id 取对应 SVG —— 数据(onlineFeatures.ts)与画法(diagrams/)解耦。
 *
 * 命名:<feature>-flow 流程图 / <feature>-topology 拓扑图。
 */

import * as React from 'react';
import { OfficeFlowDiagram, OfficeTopologyDiagram } from './office';
import { SpaceFlowDiagram, SpaceTopologyDiagram } from './space';
import { MarketFlowDiagram, MarketTopologyDiagram } from './market';
import { TasksFlowDiagram, TasksTopologyDiagram } from './tasks';
import { AnnotationFlowDiagram, AnnotationTopologyDiagram } from './annotation';

/** 所有可用的图表 id —— 与 RichSection.diagram 取值一致。 */
export type DiagramId =
  | 'office-flow'
  | 'office-topology'
  | 'space-flow'
  | 'space-topology'
  | 'market-flow'
  | 'market-topology'
  | 'tasks-flow'
  | 'tasks-topology'
  | 'annotation-flow'
  | 'annotation-topology';

/** 图表种类 —— 决定 figcaption 文案(流程示意图 / 拓扑示意图)。 */
export type DiagramKind = 'flow' | 'topology';

export interface DiagramEntry {
  component: React.FC;
  kind: DiagramKind;
}

/** id → SVG 组件 + 种类。 */
export const DIAGRAMS: Record<DiagramId, DiagramEntry> = {
  'office-flow': { component: OfficeFlowDiagram, kind: 'flow' },
  'office-topology': { component: OfficeTopologyDiagram, kind: 'topology' },
  'space-flow': { component: SpaceFlowDiagram, kind: 'flow' },
  'space-topology': { component: SpaceTopologyDiagram, kind: 'topology' },
  'market-flow': { component: MarketFlowDiagram, kind: 'flow' },
  'market-topology': { component: MarketTopologyDiagram, kind: 'topology' },
  'tasks-flow': { component: TasksFlowDiagram, kind: 'flow' },
  'tasks-topology': { component: TasksTopologyDiagram, kind: 'topology' },
  'annotation-flow': { component: AnnotationFlowDiagram, kind: 'flow' },
  'annotation-topology': { component: AnnotationTopologyDiagram, kind: 'topology' },
};
