/**
 * 96-1:ArenaStore 单测。v2:状态按 scope(tab.id)分桶。
 *
 * 用 zustand vanilla store 测;每个 case 起始重置成单道。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { selectScopedIsMultiLane, useModelArenaStore } from '../modelArena';

const TEST_SCOPE = 'tab-test';

beforeEach(() => {
  // localStorage 清掉避免上次跑残留
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('chayuan.modelArena');
  }
  useModelArenaStore.getState().resetToSingleLane(TEST_SCOPE);
});

function lanes() {
  return useModelArenaStore.getState().byTabId[TEST_SCOPE]?.lanes ?? [];
}

function unifiedSend() {
  return useModelArenaStore.getState().byTabId[TEST_SCOPE]?.unifiedSend ?? false;
}

describe('ArenaStore — 默认状态', () => {
  it('默认有一道 main', () => {
    expect(lanes()).toHaveLength(1);
    expect(lanes()[0]!.id).toBe('main');
    expect(lanes()[0]!.collapsed).toBe(false);
    expect(lanes()[0]!.detached).toBe(false);
  });

  it('unifiedSend 默认 OFF(用户决策 4)', () => {
    expect(unifiedSend()).toBe(false);
  });
});

describe('addLane', () => {
  it('返回新 lane id', () => {
    const id = useModelArenaStore.getState().addLane(TEST_SCOPE);
    expect(id).toMatch(/^lane-/);
    expect(lanes()).toHaveLength(2);
  });

  it('新道 model 跟随最后一次操作(用户决策 2)', () => {
    const s = useModelArenaStore.getState();
    s.setModel(TEST_SCOPE, 'main', 'qwen2.5-7b');
    const newId = s.addLane(TEST_SCOPE);
    const newLane = lanes().find((l) => l.id === newId)!;
    expect(newLane.modelId).toBe('qwen2.5-7b');
  });

  it('显式 modelId 覆盖 fallback', () => {
    const s = useModelArenaStore.getState();
    s.setModel(TEST_SCOPE, 'main', 'old-model');
    s.addLane(TEST_SCOPE, { modelId: 'explicit-model' });
    const last = lanes().slice(-1)[0]!;
    expect(last.modelId).toBe('explicit-model');
  });

  it('lastTouchedModelId 在加道后更新', () => {
    const s = useModelArenaStore.getState();
    s.setModel(TEST_SCOPE, 'main', 'a');
    s.addLane(TEST_SCOPE, { modelId: 'b' });
    expect(useModelArenaStore.getState().lastTouchedModelId).toBe('b');
  });
});

describe('removeLane(用户决策 3:任意删)', () => {
  it('可删非 main 道', () => {
    const id = useModelArenaStore.getState().addLane(TEST_SCOPE);
    useModelArenaStore.getState().removeLane(TEST_SCOPE, id);
    expect(lanes()).toHaveLength(1);
  });

  it('可删 main 道', () => {
    const s = useModelArenaStore.getState();
    s.addLane(TEST_SCOPE);
    s.removeLane(TEST_SCOPE, 'main');
    const ids = lanes().map((l) => l.id);
    expect(ids).not.toContain('main');
    expect(ids).toHaveLength(1);
  });

  it('删完全部 → lanes 数组为空(由 ChatPage 自动重建)', () => {
    const s = useModelArenaStore.getState();
    s.removeLane(TEST_SCOPE, 'main');
    expect(lanes()).toHaveLength(0);
  });
});

describe('setModel / setConversationId / setWidth', () => {
  it('setModel 更新对应 lane', () => {
    useModelArenaStore.getState().setModel(TEST_SCOPE, 'main', 'foo');
    expect(lanes()[0]!.modelId).toBe('foo');
  });

  it('setConversationId 不更新 lastTouchedModel', () => {
    const before = useModelArenaStore.getState().lastTouchedModelId;
    useModelArenaStore.getState().setConversationId(TEST_SCOPE, 'main', 'conv-x');
    expect(useModelArenaStore.getState().lastTouchedModelId).toBe(before);
    expect(lanes()[0]!.conversationId).toBe('conv-x');
  });

  it('setWidth 持 px 数;undefined 还原 1fr', () => {
    useModelArenaStore.getState().setWidth(TEST_SCOPE, 'main', 600);
    expect(lanes()[0]!.widthPx).toBe(600);
    useModelArenaStore.getState().setWidth(TEST_SCOPE, 'main', undefined);
    expect(lanes()[0]!.widthPx).toBeUndefined();
  });
});

describe('toggleCollapsed / setDetached', () => {
  it('折叠后再 toggle 回展开', () => {
    const s = useModelArenaStore.getState();
    s.toggleCollapsed(TEST_SCOPE, 'main');
    expect(lanes()[0]!.collapsed).toBe(true);
    s.toggleCollapsed(TEST_SCOPE, 'main');
    expect(lanes()[0]!.collapsed).toBe(false);
  });

  it('setDetached(true) 后 getActiveLanes 不含它', () => {
    const s = useModelArenaStore.getState();
    s.setDetached(TEST_SCOPE, 'main', true);
    const active = s.getActiveLanes(TEST_SCOPE);
    expect(active.find((l) => l.id === 'main')).toBeUndefined();
  });

  it('折叠的道仍参与统一发送(getActiveLanes 包含)', () => {
    useModelArenaStore.getState().toggleCollapsed(TEST_SCOPE, 'main');
    const active = useModelArenaStore.getState().getActiveLanes(TEST_SCOPE);
    expect(active.find((l) => l.id === 'main')).toBeDefined();
  });
});

describe('reorder', () => {
  it('交换两道顺序', () => {
    const s = useModelArenaStore.getState();
    const a = s.addLane(TEST_SCOPE, { modelId: 'A' });
    const b = s.addLane(TEST_SCOPE, { modelId: 'B' });
    // 当前: [main, A, B]
    s.reorder(TEST_SCOPE, 0, 2);
    const ids = lanes().map((l) => l.id);
    expect(ids).toEqual([a, b, 'main']);
  });

  it('索引越界不抛、不动', () => {
    const before = lanes().map((l) => l.id);
    useModelArenaStore.getState().reorder(TEST_SCOPE, 0, 999);
    expect(lanes().map((l) => l.id)).toEqual(before);
  });

  it('from === to 不动', () => {
    const before = lanes();
    useModelArenaStore.getState().reorder(TEST_SCOPE, 0, 0);
    expect(lanes()).toBe(before);
  });
});

describe('unifiedSend toggle', () => {
  it('setUnifiedSend(true) 切到 ON', () => {
    useModelArenaStore.getState().setUnifiedSend(TEST_SCOPE, true);
    expect(unifiedSend()).toBe(true);
  });
});

describe('selectScopedIsMultiLane selector', () => {
  it('1 道 → false,2+ 道 → true', () => {
    expect(selectScopedIsMultiLane(TEST_SCOPE)(useModelArenaStore.getState())).toBe(false);
    useModelArenaStore.getState().addLane(TEST_SCOPE);
    expect(selectScopedIsMultiLane(TEST_SCOPE)(useModelArenaStore.getState())).toBe(true);
  });
});

describe('resetToSingleLane', () => {
  it('多道 → 单道,unifiedSend 归 OFF', () => {
    const s = useModelArenaStore.getState();
    s.addLane(TEST_SCOPE); s.addLane(TEST_SCOPE);
    s.setUnifiedSend(TEST_SCOPE, true);
    s.resetToSingleLane(TEST_SCOPE);
    expect(lanes()).toHaveLength(1);
    expect(lanes()[0]!.id).toBe('main');
    expect(unifiedSend()).toBe(false);
  });
});

describe('getActiveLanes', () => {
  it('排除 detached 但保留 collapsed', () => {
    const s = useModelArenaStore.getState();
    const a = s.addLane(TEST_SCOPE);
    const b = s.addLane(TEST_SCOPE);
    s.setDetached(TEST_SCOPE, a, true);
    s.toggleCollapsed(TEST_SCOPE, b);
    const active = s.getActiveLanes(TEST_SCOPE);
    const ids = active.map((l) => l.id);
    expect(ids).toContain('main');
    expect(ids).toContain(b);  // collapsed 但仍 active
    expect(ids).not.toContain(a);  // detached 排除
  });
});

describe('per-tab 隔离(v2 新增)', () => {
  it('两个 scope 互不影响', () => {
    const s = useModelArenaStore.getState();
    s.resetToSingleLane('tab-A');
    s.resetToSingleLane('tab-B');
    s.addLane('tab-A');
    s.addLane('tab-A');
    expect(useModelArenaStore.getState().byTabId['tab-A']!.lanes).toHaveLength(3);
    expect(useModelArenaStore.getState().byTabId['tab-B']!.lanes).toHaveLength(1);
  });

  it('removeScope 把对应桶整个清掉', () => {
    const s = useModelArenaStore.getState();
    s.resetToSingleLane('tab-X');
    expect(useModelArenaStore.getState().byTabId['tab-X']).toBeDefined();
    s.removeScope('tab-X');
    expect(useModelArenaStore.getState().byTabId['tab-X']).toBeUndefined();
  });
});
