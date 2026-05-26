/**
 * 共享 KB 详情 query — 同时被 KbDetailPage(独立 Tab)和 KbDetailDialog(legacy) 消费。
 *
 * 关键点:
 *   - cache key = ['ku.detail', kuId];多个 Tab/dialog 引用同一个 kuId 自动去重
 *   - staleTime 30s;切 tab 来回不会无脑重拉
 *   - 提供 invalidate helper 让上层 mutation 写后失效;list 也跟着失效一次
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { knowledgeUniverse, type KuDetail } from '@chayuan/api';

export const KU_DETAIL_KEY = (kuId: string | null) => ['ku.detail', kuId] as const;
export const KU_LIST_KEY = ['ku.list'] as const;

export function useKuDetail(kuId: string | null) {
  return useQuery<KuDetail>({
    queryKey: KU_DETAIL_KEY(kuId),
    queryFn: () => knowledgeUniverse.detail(kuId as string),
    enabled: !!kuId,
    staleTime: 30 * 1000,
  });
}

/** 写后热生效:detail + list 同步失效 */
export function useInvalidateKu() {
  const qc = useQueryClient();
  return (kuId: string | null) => {
    void qc.invalidateQueries({ queryKey: KU_DETAIL_KEY(kuId) });
    void qc.invalidateQueries({ queryKey: KU_LIST_KEY });
  };
}
