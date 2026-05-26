/**
 * 96-4 + 96-5:多永道容器。
 *
 * 单/多道**走同一渲染路径**(都是 ``lanes.map(<Lane>)``),区别仅在 Lane 内部
 * isSingle 控制顶部工具条的显隐。这样从 1 道→2 道时,lane[0] 对应的
 * ``<Lane key="main">`` React tree 不变,内部 ``<ChatWindow>`` /
 * ``<ConversationView>`` 不卸载,messages state(包括正在流式输出的内容)完整保留。
 *
 * 历史包袱:本组件曾经在单道时直接渲染 ``singleLaneFallback`` (= 父级传进来
 * 的另一棵 React tree)、多道时切到 ``lanes.map`` 路径,导致加新道瞬间 React
 * 看到子树类型完全变 → 卸载旧 ConversationView → 已输出的内容被清空。修法
 * (2026-05-09):去掉 ``singleLaneFallback`` 概念,父级永远只传业务回调进来。
 *
 * "+ 加道" 按钮由父级 Header 控制(点 [+] → arenaStore.addLane(),本壳自动响应)。
 *
 * 统一发送 dispatcher:
 *   每个 Lane mount 时把自己的 sendMessage 注册到本壳的 fanout 表;
 *   用户在任一 Lane Composer 提交 + unifiedSend=ON 时,壳并发触发所有 lane
 *   的 sendMessage(各自 fetch + SSE,不互相阻塞)。
 *
 * lanes 数组空(用户删完了)→ store 自动重建一道,不会出现空白页。
 */
import * as React from 'react';
import {
  selectScopedLanes, useModelArenaStore,
} from '../../store/modelArena';
import { useArenaScope } from '../../store/arenaScope';
import { Lane, LaneResizer } from './Lane';
import { useDetachedLaneSync } from './DetachedLaneRoute';

/** Map<laneId, sendFn> — 注入给所有 Lane 子组件;统一发送时遍历调用 */
type SendRegistry = Map<string, (text: string) => void>;
const SendRegistryContext = React.createContext<{
  register: (laneId: string, fn: (text: string) => void) => void;
  unregister: (laneId: string) => void;
  /** unifiedSend ON 时调它:并发广播到所有非 detached lane(folded 也发) */
  broadcast: (text: string, originLaneId?: string) => void;
}>({
  register: () => {},
  unregister: () => {},
  broadcast: () => {},
});

export const useArenaSendRegistry = () => React.useContext(SendRegistryContext);

interface Props {
  /**
   * Lane 内部 ConversationView lazy 创建会话后回调。
   * 父级用它升级 tab.path / 把 lane.conversationId 写回 modelArena store。
   */
  onConversationCreated?: (laneId: string, convId: string, title: string) => void;
}

export function MultiLaneShell({ onConversationCreated }: Props): React.JSX.Element {
  const scope = useArenaScope();
  const lanes = useModelArenaStore(selectScopedLanes(scope));

  // 96-7:监听拖出窗口的 reattach 信号 + 双向 patch(scope 用作回路 key)
  useDetachedLaneSync(scope);

  // sendFn 注册表(Map 引用稳定,不靠 React state)
  const sendRegistryRef = React.useRef<SendRegistry>(new Map());

  const register = React.useCallback((laneId: string, fn: (t: string) => void) => {
    sendRegistryRef.current.set(laneId, fn);
  }, []);

  const unregister = React.useCallback((laneId: string) => {
    sendRegistryRef.current.delete(laneId);
  }, []);

  const broadcast = React.useCallback((text: string, originLaneId?: string) => {
    // 当前实现:统一发送 ON 时,所有 active lane(包括 origin)并发发送;
    // OFF 时由各 lane Composer 自己发送,不走此处
    const activeLanes = useModelArenaStore.getState().getActiveLanes(scope);
    activeLanes.forEach((l) => {
      const fn = sendRegistryRef.current.get(l.id);
      fn?.(text);
    });
  }, [scope]);

  const ctxValue = React.useMemo(
    () => ({ register, unregister, broadcast }),
    [register, unregister, broadcast],
  );

  const visibleLanes = lanes.filter((l) => !l.detached);
  const isSingle = visibleLanes.length <= 1;

  return (
    <SendRegistryContext.Provider value={ctxValue}>
      <div className="flex h-full flex-col">
        <div className="flex h-full min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
          {visibleLanes.map((lane, idx) => (
            <React.Fragment key={lane.id}>
              <Lane
                lane={lane}
                index={idx}
                isSingle={isSingle}
                registerSendFn={register}
                unregisterSendFn={unregister}
                onConversationCreated={
                  onConversationCreated
                    ? (cid, title) => onConversationCreated(lane.id, cid, title)
                    : undefined
                }
              />
              {idx < visibleLanes.length - 1 && (
                <LaneResizer laneId={lane.id} />
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </SendRegistryContext.Provider>
  );
}


// 注:统一发送 toggle 已移到 ChatPage header(用户调整)
// 多道时显示在右上角 [+ 添加] 左侧;单道时不显示
