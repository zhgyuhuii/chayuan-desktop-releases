/**
 * KB 上传任务状态层(zustand)。
 *
 * 设计:
 *   - 一条 UploadJob 对应一个文件的一次上传
 *   - 用 ku_id 作为 grouping key,UI 通过 selector 只订阅自己 KB 的 jobs
 *     避免「卡片 A 进度变化导致卡片 B 重渲」
 *   - 不持久化(刷新即清):上传是瞬时操作,不需要恢复
 */

import { create } from 'zustand';

export type UploadStatus = 'queued' | 'uploading' | 'ok' | 'error' | 'cancelled';

export interface UploadJob {
  jobId: string;
  kuId: string;
  fileName: string;
  fileSize: number;
  loaded: number;
  status: UploadStatus;
  error?: string;
  startedAt?: number;
  finishedAt?: number;
  /** 用于取消 */
  abort?: AbortController;
}

interface KbUploadState {
  /** jobId → UploadJob */
  jobs: Record<string, UploadJob>;
  enqueue(jobs: Array<Omit<UploadJob, 'status' | 'loaded'> & { abort: AbortController }>): void;
  start(jobId: string): void;
  progress(jobId: string, loaded: number, total?: number): void;
  ok(jobId: string): void;
  error(jobId: string, msg: string): void;
  cancel(jobId: string): void;
  clearKu(kuId: string): void;
  clearFinishedFor(kuId: string): void;
}

export const useKbUploadStore = create<KbUploadState>((set, get) => ({
  jobs: {},
  enqueue: (jobs) =>
    set((s) => {
      const next = { ...s.jobs };
      for (const j of jobs) {
        next[j.jobId] = {
          ...j,
          status: 'queued',
          loaded: 0,
        };
      }
      return { jobs: next };
    }),
  start: (jobId) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      return {
        jobs: { ...s.jobs, [jobId]: { ...j, status: 'uploading', startedAt: Date.now() } },
      };
    }),
  progress: (jobId, loaded, total) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      // 不要把 fileSize 改成更大的猜测值;只用 loaded
      const next = { ...j, loaded };
      if (total && total > j.fileSize) next.fileSize = total;
      return { jobs: { ...s.jobs, [jobId]: next } };
    }),
  ok: (jobId) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      return {
        jobs: {
          ...s.jobs,
          [jobId]: {
            ...j,
            status: 'ok',
            loaded: j.fileSize,
            finishedAt: Date.now(),
          },
        },
      };
    }),
  error: (jobId, msg) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      return {
        jobs: {
          ...s.jobs,
          [jobId]: { ...j, status: 'error', error: msg, finishedAt: Date.now() },
        },
      };
    }),
  cancel: (jobId) => {
    const j = get().jobs[jobId];
    if (!j) return;
    j.abort?.abort();
    set((s) => ({
      jobs: {
        ...s.jobs,
        [jobId]: { ...s.jobs[jobId]!, status: 'cancelled', finishedAt: Date.now() },
      },
    }));
  },
  clearKu: (kuId) =>
    set((s) => {
      const next: Record<string, UploadJob> = {};
      for (const [k, v] of Object.entries(s.jobs)) {
        if (v.kuId !== kuId) next[k] = v;
      }
      return { jobs: next };
    }),
  clearFinishedFor: (kuId) =>
    set((s) => {
      const next: Record<string, UploadJob> = {};
      for (const [k, v] of Object.entries(s.jobs)) {
        if (v.kuId === kuId && (v.status === 'ok' || v.status === 'cancelled' || v.status === 'error')) {
          continue;
        }
        next[k] = v;
      }
      return { jobs: next };
    }),
}));

/** 聚合视图:某 KB 的总进度 / 状态 / 计数 */
export interface KbUploadSummary {
  jobs: UploadJob[];
  total: number;
  done: number;
  uploading: number;
  failed: number;
  /** 0..1 综合百分比(按字节加权) */
  ratio: number;
  /** 是否还有进行中或排队 */
  active: boolean;
}

/**
 * Selector:按 ku_id 聚合;让消费方用 useKbUploadStore(selectKuSummary(kuId))。
 *
 * 缓存策略:
 *   useSyncExternalStore(zustand 内部用)在一次 render 里可能多次调 getSnapshot
 *   做稳定性校验。如果每次调都重新 filter/聚合,返回的对象引用永远不同,React
 *   会判定 store changed → 又触发 render → ...infinite loop("The result of
 *   getSnapshot should be cached" warning + Maximum update depth exceeded)。
 *
 *   用 WeakMap<state, Map<kuId, summary>> 做"按 store state 引用 + kuId"的双重
 *   缓存:state 引用不变(没有 set 过)时 = 命中,直接返回上次结果;set 之后
 *   state 是新对象,WeakMap 自动失效,旧 entry 会被 GC。
 */
const _summaryCache = new WeakMap<KbUploadState, Map<string, KbUploadSummary>>();

const _emptySummary: KbUploadSummary = {
  jobs: [],
  total: 0,
  done: 0,
  uploading: 0,
  failed: 0,
  ratio: 0,
  active: false,
};

export function selectKuSummary(kuId: string) {
  return (s: KbUploadState): KbUploadSummary => {
    let perKu = _summaryCache.get(s);
    if (perKu) {
      const cached = perKu.get(kuId);
      if (cached) return cached;
    } else {
      perKu = new Map();
      _summaryCache.set(s, perKu);
    }

    const jobs = Object.values(s.jobs).filter((j) => j.kuId === kuId);
    if (jobs.length === 0) {
      // 没有作业 → 复用同一份空结果,避免每次构造新对象
      perKu.set(kuId, _emptySummary);
      return _emptySummary;
    }

    let totalBytes = 0;
    let loadedBytes = 0;
    let done = 0;
    let failed = 0;
    let uploading = 0;
    for (const j of jobs) {
      totalBytes += j.fileSize;
      loadedBytes += j.status === 'ok' ? j.fileSize : Math.min(j.loaded, j.fileSize);
      if (j.status === 'ok') done += 1;
      else if (j.status === 'error') failed += 1;
      else if (j.status === 'uploading') uploading += 1;
    }
    const ratio = totalBytes > 0 ? loadedBytes / totalBytes : 0;
    const active = jobs.some((j) => j.status === 'queued' || j.status === 'uploading');
    const result: KbUploadSummary = {
      jobs,
      total: jobs.length,
      done,
      uploading,
      failed,
      ratio,
      active,
    };
    perKu.set(kuId, result);
    return result;
  };
}
