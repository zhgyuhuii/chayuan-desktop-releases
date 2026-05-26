/**
 * 文件预览中央状态机(Zustand)。
 *
 * 状态:
 *   - current:当前正在预览的文件(null = 不渲染面板)
 *   - mode:'dock' | 'float' | 'window'
 *   - collapsed:折叠成边缘窄条
 *   - dock 宽度 / float 几何
 *   - history:简易前进/后退栈(便于"上一个文件"/"下一个文件")
 *
 * 跨窗同步:
 *   - 通过 BroadcastChannel('cy.preview') 让主窗 + detached 子窗共享 close/redock 事件
 *   - 仅广播"动作",不同步全部状态(避免循环 + 子窗自治)
 */

import { create } from 'zustand';
import type { PanelMode, PreviewFile } from './types';
import { previewFileKey } from './types';

const MIN_DOCK_W = 360;
const MAX_DOCK_W = 960;
const DEFAULT_DOCK_W = 520;

const MIN_FLOAT = { w: 360, h: 320 } as const;

const STORAGE_DOCK_W = 'cy.preview.dock-width';
const STORAGE_FLOAT_GEOM = 'cy.preview.float-geom';
const STORAGE_MODE = 'cy.preview.mode';

function readNumberLS(key: string, fallback: number): number {
  try {
    const v = globalThis.localStorage?.getItem(key);
    if (!v) return fallback;
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}

function readModeLS(): PanelMode {
  try {
    const v = globalThis.localStorage?.getItem(STORAGE_MODE);
    if (v === 'dock' || v === 'float') return v;
  } catch {
    /* ignore */
  }
  return 'dock';
}

function writeLS(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    /* ignore */
  }
}

interface FloatGeom {
  x: number;
  y: number;
  w: number;
  h: number;
}

function readFloatGeomLS(): FloatGeom {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_FLOAT_GEOM);
    if (raw) return JSON.parse(raw) as FloatGeom;
  } catch {
    /* ignore */
  }
  // 默认在视口右上,留 24px 边距
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;
  return { x: vw - 600 - 24, y: 80, w: 600, h: Math.min(720, vh - 120) };
}

export interface PreviewState {
  current: PreviewFile | null;
  /**
   * 'dock' = 紧贴主窗右侧;'float' = 应用内浮窗;'window' = 已弹独立窗,主窗不渲染
   * 当 mode='window' 时,current 也为 null(子窗有自己的 store)。
   */
  mode: PanelMode;
  collapsed: boolean;
  /** dock 模式宽度 px */
  dockWidth: number;
  /** float 模式 x/y/w/h(逻辑像素,相对视口) */
  float: FloatGeom;
  /** 是否在独立子窗中运行(由 PreviewMount 在 standalone 路由下置 true) */
  standalone: boolean;
  /** 简易历史:open 多次后可前进/后退 */
  history: PreviewFile[];
  cursor: number;
  /**
   * 当前已挂载的"内嵌宿主"数量(KbDetailPage 等容器把预览面板嵌在自己布局里时,
   * 通过 claimInline() 把这个值 +1;PreviewMount 看到 >0 就不再渲染全局抽屉,
   * 避免双份 iframe / 视频 / DOM)。
   * 设计为计数器(不是 bool)以兼容多个 inline host 同时存在的极端情况。
   */
  inlineOwners: number;

  // ── actions ──
  open(file: PreviewFile, opts?: { mode?: PanelMode; resetHistory?: boolean }): void;
  close(): void;
  setMode(mode: PanelMode): void;
  toggleCollapse(): void;
  setCollapsed(v: boolean): void;
  setDockWidth(w: number): void;
  setFloat(geom: Partial<FloatGeom>): void;
  go(delta: 1 | -1): void;
  setStandalone(v: boolean): void;
  /** 内嵌宿主声明所有权;返回 release 函数,unmount 时调用 */
  claimInline(): () => void;
}

export const usePreviewStore = create<PreviewState>((set, get) => ({
  current: null,
  mode: readModeLS(),
  collapsed: false,
  dockWidth: clampDock(readNumberLS(STORAGE_DOCK_W, DEFAULT_DOCK_W)),
  float: readFloatGeomLS(),
  standalone: false,
  history: [],
  cursor: -1,
  inlineOwners: 0,

  open(file, opts) {
    const cur = get();
    const mode = opts?.mode ?? (cur.mode === 'window' ? 'dock' : cur.mode);
    let history = cur.history;
    let cursor = cur.cursor;
    if (opts?.resetHistory) {
      history = [file];
      cursor = 0;
    } else {
      const top = history[cursor];
      if (!top || previewFileKey(top) !== previewFileKey(file)) {
        history = [...history.slice(0, cursor + 1), file].slice(-20);
        cursor = history.length - 1;
      }
    }
    set({ current: file, mode, collapsed: false, history, cursor });
    writeLS(STORAGE_MODE, mode);
  },
  close() {
    set({ current: null });
  },
  setMode(mode) {
    set({ mode });
    writeLS(STORAGE_MODE, mode);
  },
  toggleCollapse() {
    set((s) => ({ collapsed: !s.collapsed }));
  },
  setCollapsed(v) {
    set({ collapsed: v });
  },
  setDockWidth(w) {
    const c = clampDock(w);
    set({ dockWidth: c });
    writeLS(STORAGE_DOCK_W, String(c));
  },
  setFloat(geom) {
    set((s) => {
      const merged: FloatGeom = {
        x: geom.x ?? s.float.x,
        y: geom.y ?? s.float.y,
        w: Math.max(MIN_FLOAT.w, geom.w ?? s.float.w),
        h: Math.max(MIN_FLOAT.h, geom.h ?? s.float.h),
      };
      writeLS(STORAGE_FLOAT_GEOM, JSON.stringify(merged));
      return { float: merged };
    });
  },
  go(delta) {
    const s = get();
    const next = s.cursor + delta;
    if (next < 0 || next >= s.history.length) return;
    set({ cursor: next, current: s.history[next]! });
  },
  setStandalone(v) {
    set({ standalone: v });
  },
  claimInline() {
    set((s) => ({ inlineOwners: s.inlineOwners + 1 }));
    let released = false;
    return () => {
      if (released) return;
      released = true;
      set((s) => ({ inlineOwners: Math.max(0, s.inlineOwners - 1) }));
    };
  },
}));

function clampDock(n: number): number {
  return Math.max(MIN_DOCK_W, Math.min(MAX_DOCK_W, Math.round(n)));
}

export const PREVIEW_DOCK_BOUNDS = { min: MIN_DOCK_W, max: MAX_DOCK_W } as const;
export const PREVIEW_FLOAT_MIN = MIN_FLOAT;
