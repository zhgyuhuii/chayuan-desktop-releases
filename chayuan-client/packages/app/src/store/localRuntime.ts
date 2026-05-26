/**
 * 本地 LLM runtime 桌面端状态缓存。
 *
 * 职责:
 * - 缓存最新 status / config / installInfo
 * - 派发 actions:start / stop / restart / saveConfig
 * - 暴露轮询 hook (Task 4):mount 时拉一次 + 每 5s 一次
 *
 * 设计:不持久化 (后端是唯一真源,前端只是 cache layer);
 * 网络错误时保留旧值 + 写 lastError 让 UI 知会。
 */

import { create } from 'zustand';
import {
  localRuntime,
  type LocalRuntimeCapability,
  type LocalRuntimeInstallInfo,
  type LocalRuntimeSettings,
  type LocalRuntimeSettingsPatch,
  type LocalRuntimeStatus,
} from '@chayuan/api';

export interface LocalRuntimeStoreState {
  /** 最近一次 GET /status 返回 */
  status: LocalRuntimeStatus | null;
  /** 最近一次 GET /config 返回 (form 初始值来源) */
  config: LocalRuntimeSettings | null;
  /** 装机路径 (首次打开 panel 时拉) */
  installInfo: LocalRuntimeInstallInfo | null;

  /** 进行中的操作 (避免双击 start) */
  pending: 'start' | 'stop' | 'restart' | 'save-config' | null;
  /** 最近一次网络错误描述 */
  lastError: string | null;
  /** 后端是否可达 (404 / network error 时置 false) */
  reachable: boolean;

  // Plan 3B 多 capability
  statuses: Record<LocalRuntimeCapability, LocalRuntimeStatus | null>;
  pendingFor: Record<LocalRuntimeCapability, 'start' | 'stop' | 'restart' | null>;

  refreshStatus(): Promise<void>;
  refreshConfig(): Promise<void>;
  refreshInstallInfo(): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  restart(): Promise<void>;
  saveConfig(patch: LocalRuntimeSettingsPatch): Promise<void>;
  clearError(): void;

  // Plan 3B
  refreshRegistry(): Promise<void>;
  startCapability(cap: LocalRuntimeCapability, opts?: { model_id?: string }): Promise<void>;
  stopCapability(cap: LocalRuntimeCapability): Promise<void>;
  restartCapability(cap: LocalRuntimeCapability, opts?: { model_id?: string }): Promise<void>;
}

export const useLocalRuntimeStore = create<LocalRuntimeStoreState>((set, get) => ({
  status: null,
  config: null,
  installInfo: null,
  pending: null,
  lastError: null,
  reachable: true,
  statuses: { chat: null, embedding: null, rerank: null, asr: null, 'image-embedding': null },
  pendingFor: { chat: null, embedding: null, rerank: null, asr: null, 'image-embedding': null },

  async refreshStatus() {
    try {
      const status = await localRuntime.getStatus();
      set({ status, reachable: true });
    } catch (e) {
      set({ reachable: false, lastError: describeError(e) });
    }
  },

  async refreshConfig() {
    try {
      const config = await localRuntime.getConfig();
      set({ config, reachable: true });
    } catch (e) {
      set({ reachable: false, lastError: describeError(e) });
    }
  },

  async refreshInstallInfo() {
    try {
      const installInfo = await localRuntime.getInstallInfo();
      set({ installInfo, reachable: true });
    } catch (e) {
      set({ reachable: false, lastError: describeError(e) });
    }
  },

  async start() {
    if (get().pending) return;
    set({ pending: 'start', lastError: null });
    try {
      const status = await localRuntime.start();
      set({ status, pending: null });
    } catch (e) {
      set({ pending: null, lastError: describeError(e) });
    }
  },

  async stop() {
    if (get().pending) return;
    set({ pending: 'stop', lastError: null });
    try {
      const status = await localRuntime.stop();
      set({ status, pending: null });
    } catch (e) {
      set({ pending: null, lastError: describeError(e) });
    }
  },

  async restart() {
    if (get().pending) return;
    set({ pending: 'restart', lastError: null });
    try {
      const status = await localRuntime.restart();
      set({ status, pending: null });
    } catch (e) {
      set({ pending: null, lastError: describeError(e) });
    }
  },

  async saveConfig(patch) {
    if (get().pending) return;
    set({ pending: 'save-config', lastError: null });
    try {
      const config = await localRuntime.setConfig(patch);
      set({ config, pending: null });
    } catch (e) {
      set({ pending: null, lastError: describeError(e) });
    }
  },

  clearError() {
    set({ lastError: null });
  },

  async refreshRegistry() {
    try {
      const reg = await localRuntime.getRegistry();
      set({ statuses: reg, reachable: true });
    } catch (e) {
      set({ reachable: false, lastError: describeError(e) });
    }
  },

  async startCapability(cap, opts) {
    if (get().pendingFor[cap]) return;
    set((s) => ({
      pendingFor: { ...s.pendingFor, [cap]: 'start' },
      lastError: null,
    }));
    try {
      const status = await localRuntime.startFor(cap, opts);
      set((s) => ({
        statuses: { ...s.statuses, [cap]: status },
        pendingFor: { ...s.pendingFor, [cap]: null },
      }));
    } catch (e) {
      set((s) => ({
        pendingFor: { ...s.pendingFor, [cap]: null },
        lastError: describeError(e),
      }));
    }
  },

  async stopCapability(cap) {
    if (get().pendingFor[cap]) return;
    set((s) => ({
      pendingFor: { ...s.pendingFor, [cap]: 'stop' },
      lastError: null,
    }));
    try {
      const status = await localRuntime.stopFor(cap);
      set((s) => ({
        statuses: { ...s.statuses, [cap]: status },
        pendingFor: { ...s.pendingFor, [cap]: null },
      }));
    } catch (e) {
      set((s) => ({
        pendingFor: { ...s.pendingFor, [cap]: null },
        lastError: describeError(e),
      }));
    }
  },

  async restartCapability(cap, opts) {
    if (get().pendingFor[cap]) return;
    set((s) => ({
      pendingFor: { ...s.pendingFor, [cap]: 'restart' },
      lastError: null,
    }));
    try {
      const status = await localRuntime.restartFor(cap, opts);
      set((s) => ({
        statuses: { ...s.statuses, [cap]: status },
        pendingFor: { ...s.pendingFor, [cap]: null },
      }));
    } catch (e) {
      set((s) => ({
        pendingFor: { ...s.pendingFor, [cap]: null },
        lastError: describeError(e),
      }));
    }
  },
}));

function describeError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
