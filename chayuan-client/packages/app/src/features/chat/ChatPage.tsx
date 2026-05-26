/**
 * 主对话页 — 永远走 MultiLaneShell + Lane 渲染(单道也走这条路径)。
 *
 * 这样从 1 道→2 道时,lane[0] 的 React tree 不变,内部 ConversationView 不卸载,
 * 已经流式输出的消息保留下来。换句话说,"添加永道" 是真正的"在旁边追加新窗口",
 * 不会清掉已有内容。
 *
 * 顶栏 [拖出 / + 添加 / unifiedSend toggle / Artifact 切换] 永远挂在外层(本组件
 * 直接渲染 header,不再走 ConversationView headerSlot),这样 Lane 改变也不会
 * 撕掉外层 header,布局稳定。
 *
 * tab.path 同步:
 *   - props.conversationId(tab.path 解析)是真源 → useEffect 同步到 lane[0]
 *   - lane[0] 内部 ChatWindow lazy 创建会话 → 通过 onConversationCreated 回调拿到
 *     新 cid → 升级 tab.path 为 /chat/<cid>,并写回 lane.conversationId
 */

import * as React from 'react';
import { Plus } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { AdvancedParams } from './AdvancedParams';
import { StreamMetrics } from './StreamMetrics';
import { useArtifactStore } from '../../store/artifact';
import { useTabsStore } from '../../store/tabs';
import {
  selectScopedIsMultiLane, selectScopedLanes, selectScopedUnifiedSend, useModelArenaStore,
} from '../../store/modelArena';
import { useArenaScope } from '../../store/arenaScope';
import { MultiLaneShell } from '../model-arena/MultiLaneShell';

export interface ChatPageProps {
  /**
   * 当前 tab 渲染的会话 id。由 page-registry 从 tab.path 解析后传入:
   *   - `/chat` → null(草稿模式,首发消息时 lazy 建会话)
   *   - `/chat/<id>` → 该 id
   *
   * 为什么不读全局 useComposerStore.activeConversationId:
   * 多个 chat tab 共享 __global__ scope 的 composer store,激活某 tab 时
   * setActive 会污染其它后台 tab,导致"两个 tab 显示同一会话"。tab.path 才是
   * 各 tab 独立的真源,通过 prop 注入能保证渲染严格按 tab 隔离。
   */
  conversationId: string | null;
}

export const ChatPage: React.FC<ChatPageProps> = ({ conversationId }) => {
  const artifactOpen = useArtifactStore((s) => s.open);
  const setArtifactOpen = useArtifactStore((s) => s.setOpen);
  const artifactCount = useArtifactStore((s) => Object.keys(s.items).length);

  // 96 题:多永道(model arena)接入。单/多道**走同一渲染路径**(都进 MultiLaneShell),
  // 单道差别只在 Lane 内部 isSingle=true 时隐藏顶部工具条,视觉等价老 ChatPage。
  // v2:状态按 tab.id 分桶,各 tab 独立 lanes / unifiedSend。
  const scope = useArenaScope();
  const isMulti = useModelArenaStore(selectScopedIsMultiLane(scope));
  const addLane = useModelArenaStore((s) => s.addLane);
  const lanes = useModelArenaStore(selectScopedLanes(scope));
  const unifiedSend = useModelArenaStore(selectScopedUnifiedSend(scope));
  const setUnifiedSend = useModelArenaStore((s) => s.setUnifiedSend);
  const setLaneConversationId = useModelArenaStore((s) => s.setConversationId);
  const setLaneFirstQuestion = useModelArenaStore((s) => s.setFirstQuestion);
  const navigate = useNavigate();

  // ── props.conversationId(tab.path 真源)→ lane[0].conversationId 单方向同步 ──
  // 用户从 sidebar 点已有会话 / refresh 后 tab.path 都会变化,需要把它喂给 lane[0]
  // 让 ChatWindow 能加载对应消息。lane[0] 必须存在(下面有 effect 保底重建)。
  React.useEffect(() => {
    const mainLane = lanes[0];
    if (!mainLane) return;
    if (mainLane.conversationId === conversationId) return;
    setLaneConversationId(scope, mainLane.id, conversationId);
    // 这里只关心 conversationId 变化,lanes 变化由其它 effect 处理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, lanes[0]?.id]);

  // lane[0] 内部 ChatWindow lazy 创建会话:升级 tab.path + 把 cid 写回 lane state。
  // 其他 lane 创建的会话目前不升级 tab.path(对抗模式下每道 cid 独立,只在自己 lane 持有)。
  const onConversationCreated = React.useCallback((laneId: string, cid: string, title: string) => {
    setLaneConversationId(scope, laneId, cid);
    // ConversationView lazy create 时 title = 用户首条输入(text.slice(0, 40)),
    // 直接落到 lane.firstQuestion 给折叠条标签用 — 即时、不等 DB 落盘。
    setLaneFirstQuestion(scope, laneId, title);
    if (lanes[0]?.id !== laneId) return;
    const tabs = useTabsStore.getState();
    const cur = tabs.activeId
      ? tabs.tabs.find((tt) => tt.id === tabs.activeId)
      : undefined;
    if (cur && cur.path === '/chat') {
      tabs.updatePath(cur.id, `/chat/${encodeURIComponent(cid)}`, title);
    }
  }, [scope, setLaneConversationId, setLaneFirstQuestion, lanes]);

  // lanes 数组空(本 scope 未初始化 / 用户全删完)→ 自动重建 main 道,避免空白页
  React.useEffect(() => {
    if (lanes.length === 0) {
      useModelArenaStore.getState().resetToSingleLane(scope);
    }
  }, [lanes.length, scope]);

  // 非 detached lane 数 = 0 → 主窗口聊天页空,跳 /home
  // (单泳道 detach / 多泳道全 detach 都走这条;条件 lanes.length > 0 防止
  //  初始化空数组误触,那条由上面的 resetToSingleLane effect 接管)
  React.useEffect(() => {
    const activeCount = lanes.filter((l) => !l.detached).length;
    if (activeCount === 0 && lanes.length > 0) {
      void navigate({ to: '/home' });
    }
  }, [lanes, navigate]);

  // 顶栏右侧:[Artifact 切换?] [☐ 统一发送(仅多道显示)] [+ 添加]
  // 顶栏左侧不放任何东西 — ModelPicker 已在 ChatWindow 内部顶栏,这里只留 page-level 控制
  // AdvancedParams 已经挂在每个 Lane 内 ChatWindow 顶栏(scope 独立),这里不再重复挂
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-end border-b px-6 py-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <ChatPageMetrics />
          {artifactCount > 0 && (
            <button
              type="button"
              onClick={() => setArtifactOpen(!artifactOpen)}
              className="rounded-md border px-2 py-1 text-xs hover:bg-accent"
            >
              {artifactOpen ? '收起' : '展开'} Artifact ({artifactCount})
            </button>
          )}
          {isMulti && (
            <label className="flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs select-none hover:bg-accent">
              <input
                type="checkbox"
                checked={unifiedSend}
                onChange={(e) => setUnifiedSend(scope, e.target.checked)}
                className="h-3.5 w-3.5"
              />
              <span>统一发送</span>
            </label>
          )}
          <button
            type="button"
            onClick={() => addLane(scope)}
            className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"
            title="添加永道(模型对抗)"
          >
            <Plus className="h-3.5 w-3.5" />
            添加
          </button>
          {/* 高级参数 — 放「+ 添加」右侧。Lane 内 ChatWindow 顶栏已 showHeader=false,
              这里成为唯一入口,作用域是当前 chat 路由的全局 composer state */}
          <AdvancedParams />
        </div>
      </header>
      <div className="min-h-0 flex-1">
        <MultiLaneShell onConversationCreated={onConversationCreated} />
      </div>
    </div>
  );
};

/**
 * StreamMetrics 需要从 chat state 拿数据;但我们把状态机放在 ConversationView
 * 里了。简单方案:让指标条只显示静态信息(消息数我们这层拿不到),或后续把
 * chat.isStreaming 通过 context 暴露出来。当前 KISS,先放一个空的占位
 * — 90% 的体验仍在 ConversationView 内部完整保留(StreamMetrics 是 nice-to-have)。
 *
 * TODO(若用户反馈想恢复指标条):在 ConversationView 加 onStateChange 回调,
 * 把 isStreaming / startedAt / firstTokenAt 上抛父级。
 */
const ChatPageMetrics: React.FC = () => {
  return (
    <StreamMetrics
      active={false}
      text=""
      startedAt={null}
      firstTokenAt={null}
    />
  );
};
