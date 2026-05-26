/**
 * useKbImageSearch — 跨所有 image 类 KB 的"以图搜库"。
 *
 * 思路:
 *   - 后端没有跨源的图像搜索端点;前端拿到 image kind KB 列表后并发调
 *     /knowledge_source/{id}/image/search_by_image,每个返回 hits[],
 *     取该 KB 的 max(score) 作为该 KB 的相关度。
 *   - 并发使用 Promise.allSettled,任意 KB 失败不影响整体;失败的算 0 分。
 *   - 进度按已完成的 KB 数做累加,UI 端显示 "3/5 个图像库已搜索"。
 *   - 用 AbortController 支持中途取消。
 *
 * 不包含 UI 渲染;返回 scores Map 给消费方排序/高亮。
 */

import * as React from 'react';
import { imageSource, type KuItem } from '@chayuan/api';

export interface KbImageSearchState {
  active: boolean;
  busy: boolean;
  done: number;
  total: number;
  /** ku_id → 该 KB 内最高 score */
  scores: Map<string, number>;
  /** 查询图的本地 objectURL,用于条幅缩略图 */
  queryThumb: string | null;
  error: string | null;
}

const INITIAL: KbImageSearchState = {
  active: false,
  busy: false,
  done: 0,
  total: 0,
  scores: new Map(),
  queryThumb: null,
  error: null,
};

export interface UseKbImageSearchReturn {
  state: KbImageSearchState;
  /** 选/拖一张图后调用;imageItems 是 KuItem 列表(只用 image 类) */
  start(file: File, imageKbs: KuItem[]): Promise<void>;
  cancel(): void;
  clear(): void;
}

function srcIdFromKuId(kuId: string): number | null {
  if (!kuId.startsWith('src:')) return null;
  const n = Number(kuId.slice(4));
  return Number.isFinite(n) ? n : null;
}

export function useKbImageSearch(): UseKbImageSearchReturn {
  const [state, setState] = React.useState<KbImageSearchState>(INITIAL);
  const ctrlRef = React.useRef<AbortController | null>(null);
  const thumbRef = React.useRef<string | null>(null);

  // unmount 时回收 objectURL + 取消请求
  React.useEffect(() => {
    return () => {
      ctrlRef.current?.abort();
      if (thumbRef.current) URL.revokeObjectURL(thumbRef.current);
    };
  }, []);

  const start = React.useCallback(async (file: File, imageKbs: KuItem[]) => {
    // 取消旧请求 + 释放旧 objectURL
    ctrlRef.current?.abort();
    if (thumbRef.current) {
      URL.revokeObjectURL(thumbRef.current);
      thumbRef.current = null;
    }
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;

    let thumb: string | null = null;
    try {
      thumb = URL.createObjectURL(file);
      thumbRef.current = thumb;
    } catch { /* ignore */ }

    const targets = imageKbs
      .map((k) => ({ ku_id: k.ku_id, sourceId: srcIdFromKuId(k.ku_id) }))
      .filter((t): t is { ku_id: string; sourceId: number } => t.sourceId != null);

    setState({
      active: true,
      busy: true,
      done: 0,
      total: targets.length,
      scores: new Map(),
      queryThumb: thumb,
      error: null,
    });

    if (targets.length === 0) {
      setState((s) => ({ ...s, busy: false, error: '没有可搜索的图像知识库' }));
      return;
    }

    const scores = new Map<string, number>();
    let done = 0;
    // 并发跑;每个完成后递增进度
    const tasks = targets.map(async (t) => {
      try {
        const hits = await imageSource.searchByImage(t.sourceId, file, 5, { signal: ctrl.signal });
        if (ctrl.signal.aborted) return;
        const max = hits.reduce((m, h) => Math.max(m, h.score ?? 0), 0);
        if (max > 0) scores.set(t.ku_id, max);
      } catch {
        // 单 KB 失败不影响整体;不记录 score
      } finally {
        if (!ctrl.signal.aborted) {
          done += 1;
          // 渐进式更新 progress + scores
          setState((s) =>
            s.active
              ? { ...s, done, scores: new Map(scores) }
              : s,
          );
        }
      }
    });

    await Promise.allSettled(tasks);
    if (ctrl.signal.aborted) return;
    setState((s) =>
      s.active
        ? { ...s, busy: false, scores: new Map(scores), done: targets.length }
        : s,
    );
  }, []);

  const cancel = React.useCallback(() => {
    ctrlRef.current?.abort();
    setState((s) => ({ ...s, busy: false }));
  }, []);

  const clear = React.useCallback(() => {
    ctrlRef.current?.abort();
    if (thumbRef.current) {
      URL.revokeObjectURL(thumbRef.current);
      thumbRef.current = null;
    }
    setState(INITIAL);
  }, []);

  return { state, start, cancel, clear };
}
