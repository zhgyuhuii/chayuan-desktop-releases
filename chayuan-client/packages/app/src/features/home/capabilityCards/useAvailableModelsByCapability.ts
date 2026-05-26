/**
 * 把 ``GET /v1/models`` 按 capability 分桶 — 主页能力卡用。
 *
 * 性能要点
 * ========
 * 1. **共享缓存**:queryKey 与 ``ComposerModelPill`` 一致(``['v1.models', { type:'all' }]``),
 *    React Query 自动去重 → 主页和 composer 同屏时**不会**多发请求
 * 2. **同 hook 内 useMemo**:模型数据 30s staleTime 内不变 → 分桶结果引用稳定,
 *    下游卡片不会重渲
 * 3. **不做 check_access** 探活:跟 ComposerModelPill 一致,避免每次进首页都
 *    并行触发 N 厂商 OpenAI client 探针(在掉线场景能拖几十秒)
 *
 * 并发安全
 * ========
 * 该 hook 是只读;React Query 把多次订阅串行化;``classifyVendor`` 是纯函数,
 * 无副作用,并行渲染多张卡同时调用安全。
 *
 * 返回结构
 * ========
 *   ``{ byCap: Map<cap, models>, isLoading, isFetched, hasAnyChat }``
 *
 * ``hasAnyChat`` 给主页"未配模型 banner"判断用 — 复用 ``models.list``,
 * 不再单独发 ``platformCatalog``。
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { models, type RawModelItem } from '@chayuan/api';
import type { ModalityCapability } from '@chayuan/transport';
import {
  inferCapability,
  isInteractiveCapability,
} from '../../composer/modelCapabilityChip';

export interface CapabilityModelBucket {
  cap: ModalityCapability;
  /** 这一类可用的全部模型(按 available !== false 过滤后) */
  models: RawModelItem[];
}

export interface AvailableModelsByCapability {
  byCap: Map<ModalityCapability, RawModelItem[]>;
  /** 任意 capability 下是否至少有一个可用模型 */
  hasAny: boolean;
  isLoading: boolean;
  isFetched: boolean;
}


export function useAvailableModelsByCapability(): AvailableModelsByCapability {
  const { data = [], isLoading, isFetched } = useQuery<RawModelItem[]>({
    // 与 ComposerModelPill / ModelPicker 同 key 共享缓存
    queryKey: ['v1.models', { type: 'all' }],
    queryFn: () => models.list(),
    staleTime: 60_000,
    retry: 0,
  });

  const byCap = React.useMemo(() => {
    const map = new Map<ModalityCapability, RawModelItem[]>();
    for (const m of data) {
      if (m.available === false) continue;
      const cap = inferCapability(m);
      // 不能在对话框里直接调的能力(embedding/rerank)在主页也不放,统一靠
      // isInteractiveCapability 单一真源
      if (!isInteractiveCapability(cap)) continue;
      const bucket = map.get(cap);
      if (bucket) bucket.push(m);
      else map.set(cap, [m]);
    }
    // 桶内按 id 字典序稳定 — 让"选第一个"的体感跟 ModelMenuList 第一条一致
    for (const list of map.values()) list.sort((a, b) => a.id.localeCompare(b.id));
    return map;
  }, [data]);

  const hasAny = React.useMemo(() => {
    for (const list of byCap.values()) {
      if (list.length > 0) return true;
    }
    return false;
  }, [byCap]);

  return { byCap, hasAny, isLoading, isFetched };
}


/** 取某 capability 的最佳模型(目前 = 第一个);后续可基于成本 / latency 排序。 */
export function pickBestModel(
  bucket: RawModelItem[] | undefined,
): RawModelItem | null {
  if (!bucket || !bucket.length) return null;
  return bucket[0] ?? null;
}
