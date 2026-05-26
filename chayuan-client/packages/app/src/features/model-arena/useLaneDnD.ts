/**
 * 96-6:拖动排序 + 调宽度 hooks。
 *
 * 不引入新依赖(@dnd-kit / react-resizable),纯 pointer events 实现。
 *
 * useLaneSortable(laneId, idx)
 *   - 返回 { dragHandleProps, dragOverlayClass }
 *   - dragHandleProps 挂到 LaneHeader 顶部一个 grab 区域
 *   - 拖动时显示蓝色虚线占位,松手调用 arenaStore.reorder
 *
 * useLaneResizer(laneId)
 *   - 返回 { resizerProps }
 *   - resizerProps 挂到 lane 右侧 8px 竖条
 *   - 拖动时累加 dx 写到 lane.widthPx,松手时持久化
 */
import * as React from 'react';
import { useModelArenaStore, selectScopedLanes } from '../../store/modelArena';
import { useArenaScope } from '../../store/arenaScope';

interface DragState {
  laneId: string;
  startX: number;
  draggingIndex: number;
}

const LANE_WIDTH_MIN = 280;
const LANE_WIDTH_MAX = 1200;


/**
 * Drag 排序协调单例 — 全局只有一个 lane 在拖,统一管 hover-target index。
 * 比每个 Lane 各自维护一个 ref 简单 + 性能好。
 */
const dragStateRef: { current: DragState | null } = { current: null };
const hoverIndexRef: { current: number | null } = { current: null };

/** 给 hover 占位用的全局事件 — Lane 监听更新本地高亮 */
type Listener = (s: { draggingId: string | null; hoverIndex: number | null }) => void;
const listeners: Set<Listener> = new Set();

function notifyListeners() {
  const payload = {
    draggingId: dragStateRef.current?.laneId ?? null,
    hoverIndex: hoverIndexRef.current,
  };
  listeners.forEach((l) => l(payload));
}


export function useLaneSortable(laneId: string, index: number) {
  const scope = useArenaScope();
  const [highlight, setHighlight] = React.useState<{
    isDragging: boolean;
    isDropTarget: boolean;
  }>({ isDragging: false, isDropTarget: false });

  React.useEffect(() => {
    const listener: Listener = ({ draggingId, hoverIndex }) => {
      setHighlight({
        isDragging: draggingId === laneId,
        isDropTarget: draggingId !== null && draggingId !== laneId
          && hoverIndex === index,
      });
    };
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, [laneId, index]);

  const onPointerDown = React.useCallback((e: React.PointerEvent) => {
    // 只允许左键 + 不响应 input 内的点击
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('input, textarea, button, select')) return;

    dragStateRef.current = {
      laneId, startX: e.clientX, draggingIndex: index,
    };
    notifyListeners();

    const onMove = (ev: PointerEvent) => {
      // 计算 hover 落点:遍历所有 lane container DOM 找鼠标 x 落在哪一个的中点段
      const containers = document.querySelectorAll<HTMLElement>('[data-lane-id]');
      let hover: number | null = null;
      for (let i = 0; i < containers.length; i++) {
        const el = containers[i]!;
        const rect = el.getBoundingClientRect();
        const mid = rect.left + rect.width / 2;
        if (ev.clientX < mid) { hover = i; break; }
      }
      if (hover === null) hover = containers.length - 1;
      if (hoverIndexRef.current !== hover) {
        hoverIndexRef.current = hover;
        notifyListeners();
      }
    };

    const onUp = () => {
      const drag = dragStateRef.current;
      const hover = hoverIndexRef.current;
      if (drag && hover !== null && hover !== drag.draggingIndex) {
        useModelArenaStore.getState().reorder(scope, drag.draggingIndex, hover);
      }
      dragStateRef.current = null;
      hoverIndexRef.current = null;
      notifyListeners();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
    e.preventDefault();
  }, [laneId, index, scope]);

  return {
    /** 挂到 LaneHeader 上的拖手柄 */
    dragHandleProps: {
      onPointerDown,
      style: { cursor: 'grab' as const, touchAction: 'none' as const },
    },
    /** lane 容器需要的属性 */
    laneContainerProps: {
      'data-lane-id': laneId,
      'data-dragging': highlight.isDragging || undefined,
      'data-drop-target': highlight.isDropTarget || undefined,
    } as Record<string, string | undefined>,
    isDragging: highlight.isDragging,
    isDropTarget: highlight.isDropTarget,
  };
}


// ---------------------------------------------------------------------------
// useLaneResizer — lane 之间的竖条
// ---------------------------------------------------------------------------

export function useLaneResizer(laneId: string) {
  const scope = useArenaScope();
  const onPointerDown = React.useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const startX = e.clientX;
    const lanes = selectScopedLanes(scope)(useModelArenaStore.getState());
    const lane = lanes.find((l) => l.id === laneId);
    const initialWidth = lane?.widthPx ?? (() => {
      // 没有 widthPx 时,从 DOM 测当前宽度
      const el = document.querySelector(`[data-lane-id="${laneId}"]`);
      return el ? (el as HTMLElement).getBoundingClientRect().width : 480;
    })();

    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      const newW = Math.max(LANE_WIDTH_MIN, Math.min(LANE_WIDTH_MAX, initialWidth + dx));
      useModelArenaStore.getState().setWidth(scope, laneId, newW);
    };

    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
    e.preventDefault();
  }, [laneId, scope]);

  return {
    resizerProps: {
      onPointerDown,
      style: { cursor: 'col-resize' as const, touchAction: 'none' as const },
    },
  };
}
