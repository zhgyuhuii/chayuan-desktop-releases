/**
 * 96-6:DnD hooks 行为测试 — 直接测 store 联动,DOM 部分依赖 jsdom mock。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useModelArenaStore } from '../../../store/modelArena';

const TEST_SCOPE = 'tab-test';

beforeEach(() => {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('chayuan.modelArena');
  }
  useModelArenaStore.getState().resetToSingleLane(TEST_SCOPE);
});

function lanes() {
  return useModelArenaStore.getState().byTabId[TEST_SCOPE]?.lanes ?? [];
}

describe('useLaneResizer 通过 store API 调宽', () => {
  it('setWidth 修正 lane.widthPx', () => {
    useModelArenaStore.getState().setWidth(TEST_SCOPE, 'main', 500);
    expect(lanes()[0]!.widthPx).toBe(500);
  });

  it('setWidth(undefined) 恢复 1fr', () => {
    useModelArenaStore.getState().setWidth(TEST_SCOPE, 'main', 500);
    useModelArenaStore.getState().setWidth(TEST_SCOPE, 'main', undefined);
    expect(lanes()[0]!.widthPx).toBeUndefined();
  });
});

describe('useLaneSortable 通过 store API 排序', () => {
  it('reorder 0 → 2 把首道挪到末位', () => {
    const s = useModelArenaStore.getState();
    s.addLane(TEST_SCOPE, { modelId: 'B' });
    s.addLane(TEST_SCOPE, { modelId: 'C' });
    // [main, B-id, C-id]
    s.reorder(TEST_SCOPE, 0, 2);
    const ids = lanes().map((l) => l.id);
    expect(ids[2]).toBe('main');
  });

  it('reorder 同 index 不动', () => {
    const before = lanes();
    useModelArenaStore.getState().reorder(TEST_SCOPE, 0, 0);
    expect(lanes()).toBe(before);
  });
});
