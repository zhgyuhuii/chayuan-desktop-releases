/**
 * 96 题:单永道(Lane)— 用 ChatWindow 提供完整聊天能力。
 *
 * 视觉:
 *   ┌────────────────────────────────┐
 *   │ ⮟ ⬈ X      (永道头:折叠/拖出/删) │
 *   ├────────────────────────────────┤
 *   │ <ChatWindow scope=lane.id>      │  ← 完整聊天窗口(模型/KB/深度思考/...)
 *   │   ConversationView(消息+Composer)│
 *   └────────────────────────────────┘
 *
 * 折叠时只显示头部一根竖条;拖出时主窗口不渲染(由父级处理)。
 */
import * as React from 'react';
import { ChevronDown, ChevronRight, GripVertical, X } from 'lucide-react';
import { cn } from '@chayuan/ui';
import {
  selectScopedUnifiedSend, useModelArenaStore, type Lane as LaneState,
} from '../../store/modelArena';
import { useArenaScope } from '../../store/arenaScope';
import { ChatWindow } from '../chat/ChatWindow';
import { listMessages } from '../conversations/persistence';
import { useArenaSendRegistry } from './MultiLaneShell';
import { useLaneSortable, useLaneResizer } from './useLaneDnD';

interface Props {
  lane: LaneState;
  index: number;
  isSingle: boolean;
  registerSendFn?: (laneId: string, fn: (text: string) => void) => void;
  unregisterSendFn?: (laneId: string) => void;
  /**
   * ChatWindow 内部 ConversationView lazy 创建会话后回调。
   * Lane 直接转发给 ChatWindow;父级(ChatPage / MultiLaneShell)用它升级 tab.path 等。
   */
  onConversationCreated?: (id: string, title: string) => void;
}

export function Lane({
  lane, index, isSingle, registerSendFn, unregisterSendFn, onConversationCreated,
}: Props): React.JSX.Element {
  const scope = useArenaScope();
  const removeLane = useModelArenaStore((s) => s.removeLane);
  const toggleCollapsed = useModelArenaStore((s) => s.toggleCollapsed);
  const unifiedSend = useModelArenaStore(selectScopedUnifiedSend(scope));
  const { broadcast } = useArenaSendRegistry();
  const { dragHandleProps, laneContainerProps, isDragging, isDropTarget } =
    useLaneSortable(lane.id, index);

  // ChatWindow 内部 sendMessage 引用 — Lane 用它注册到 SendRegistry,
  // unifiedSend ON 时父级 broadcast 把 text 调用到所有 lane 的 sendMessage
  const sendMessageRef = React.useRef<((text: string) => Promise<void>) | null>(null);

  // 折叠条上的标题 = 本道第一条用户提问。两路来源,任一命中即可:
  //   1) lane.firstQuestion(主路径):新会话 lazy create 时 ChatPage 在
  //      onConversationCreated 里写入,即时,不等 DB 落盘。
  //   2) listMessages 兜底:从侧边栏点开旧会话时,onConversationCreated
  //      不会再触发,只能折叠时从本地 DB 拉。
  const [firstQuestionFromDb, setFirstQuestionFromDb] = React.useState<string | null>(null);
  React.useEffect(() => {
    // 切到新会话时清掉旧 DB 缓存
    setFirstQuestionFromDb(null);
    if (!lane.conversationId || !lane.collapsed) return;
    if (lane.firstQuestion) return; // 主路径已命中,不必查 DB
    let cancelled = false;
    void (async () => {
      try {
        const msgs = await listMessages(lane.conversationId!);
        if (cancelled) return;
        const first = msgs.find((m) => m.role === 'user');
        const text = first?.content?.trim();
        if (text) setFirstQuestionFromDb(text);
      } catch {
        /* DB 查询失败就回退到 "无标题" */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lane.conversationId, lane.collapsed, lane.firstQuestion]);

  const collapsedLabel = (lane.firstQuestion || firstQuestionFromDb || '无标题').trim();

  React.useEffect(() => {
    if (!registerSendFn) return;
    // 通过函数包装 ref → 注册 fn 始终调用最新 ref
    const fn = (text: string) => {
      const real = sendMessageRef.current;
      if (real) void real(text);
    };
    registerSendFn(lane.id, fn);
    return () => unregisterSendFn?.(lane.id);
  }, [lane.id, registerSendFn, unregisterSendFn]);

  // unifiedSend ON 时拦截:ChatWindow 内部调 sendMessage 之前问 onBeforeSend,
  // 我们说 "true 中止本道",改由 broadcast 把 text 发到所有 lane 各自的 sendMessage
  const onBeforeSend = React.useCallback(
    async (text: string): Promise<boolean> => {
      if (!unifiedSend) return false;
      // 先发本道(它的 sendMessageRef 被自己注册,broadcast 内含),所以直接 broadcast
      broadcast(text);
      return true;  // 中止 ChatWindow 内部本次单道 send,因为已经由 broadcast 触发了
    },
    [unifiedSend, broadcast],
  );

  // 折叠/展开切的是 CSS 显隐,ChatWindow 始终挂载,避免 unmount 把组件内部
  // 的消息/流式状态/composer 草稿一并销毁。zustand store 虽然按 scope 持久,
  // 但 useChayuanChat / ConversationView 内部本地 state 是绑组件生命周期的,
  // 之前直接 `if (collapsed) return <Strip>` 会让 ChatWindow 整棵子树重建,
  // 展开时消息列表回不来。
  return (
    <div
      {...laneContainerProps}
      className={cn(
        'flex h-full flex-col transition-opacity',
        lane.collapsed
          ? 'w-12 items-center border-r bg-muted/30'
          : cn('min-w-0 bg-card', !isSingle && 'border-r'),
        isDragging && 'opacity-50',
        lane.collapsed
          ? isDropTarget && 'border-l-2 border-l-primary'
          : !isSingle && isDropTarget && 'ring-2 ring-primary',
      )}
      style={
        lane.collapsed
          ? undefined
          : lane.widthPx
            ? { width: `${lane.widthPx}px`, flex: '0 0 auto' }
            : { flex: '1 1 0' }
      }
    >
      {/* 折叠态 — 一根 48px 竖条;与展开态共存,通过 hidden 控制可见性 */}
      <div className={cn('flex flex-col items-center', !lane.collapsed && 'hidden')}>
        <button
          type="button"
          onClick={() => toggleCollapsed(scope, lane.id)}
          className="mt-2 rounded p-1 hover:bg-accent"
          title="展开"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        {/* 竖向显示该泳道首条提问;尚未问话回退到「无标题」(不再回退到模型名)。
            writing-mode: vertical-rl 让字符从上往下排;text-orientation: upright 让
            每个汉字保持横向不旋转;最多展示 24 字符,鼠标悬停可看完整原文。 */}
        <div
          className="mt-3 max-h-[calc(100vh-6rem)] overflow-hidden whitespace-nowrap text-xs text-foreground"
          style={{ writingMode: 'vertical-rl' as const, textOrientation: 'upright' as const }}
          title={collapsedLabel}
        >
          {collapsedLabel.slice(0, 24)}
        </div>
      </div>

      {/* 展开态 — 顶部工具条 + ChatWindow;折叠时整块 hidden 但不卸载,保留组件状态 */}
      <div className={cn('flex h-full min-w-0 flex-col', lane.collapsed && 'hidden')}>
        {/* 单道:整个顶部工具条隐藏 — 折叠/拖出/关闭对单道无意义,而且会跟 ChatPage 顶栏重复;
            多道:工具条显示,折叠 / 关闭 / 拖动手柄齐全。 */}
        {!isSingle && (
          <div
            {...dragHandleProps}
            className="flex items-center gap-0.5 border-b bg-muted/30 px-1.5 py-1 select-none"
            title="按住此区域拖动 → 调整顺序"
          >
            <span
              className="flex h-5 items-center gap-1 rounded px-1 text-[10px] text-muted-foreground/70 hover:text-foreground"
              aria-hidden
            >
              <GripVertical className="h-3 w-3" />
              <span className="truncate font-mono">{lane.modelId || '未选'}</span>
            </span>
            <span className="flex-1" />
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => toggleCollapsed(scope, lane.id)}
              className="rounded p-1 hover:bg-accent"
              title="折叠"
            >
              <ChevronDown className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => removeLane(scope, lane.id)}
              className="rounded p-1 text-destructive hover:bg-accent"
              title="关闭永道"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* 完整聊天窗口 — 用 lane.id 作 scope,model/draft/KB 各 lane 独立。 */}
        <div className="min-h-0 flex-1">
          <ChatWindow
            scope={`lane.${lane.id}`}
            conversationId={lane.conversationId}
            onConversationCreated={onConversationCreated}
            sendMessageRef={sendMessageRef}
            onBeforeSend={onBeforeSend}
            showHeader={false}
          />
        </div>
      </div>
    </div>
  );
}


/** Lane 之间的可拖拽 resizer */
export function LaneResizer({ laneId }: { laneId: string }): React.JSX.Element {
  const { resizerProps } = useLaneResizer(laneId);
  return (
    <div
      {...resizerProps}
      className="group relative w-px shrink-0 bg-border hover:bg-primary"
      title="拖动调整宽度"
    >
      <div className="absolute inset-y-0 -left-1 -right-1" />
    </div>
  );
}
