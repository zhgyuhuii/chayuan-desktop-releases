import type { KuDetail } from '@chayuan/api';

/** 4 个 detail 子面板共享的契约。dialog 容器统一传 detail+权限+刷新句柄进来 */
export interface DetailPanelProps<D extends KuDetail = KuDetail> {
  detail: D;
  /** 当前用户对该 KB 是否有 owner-only 权限(删/改/上传) */
  mine: boolean;
  /** 子面板执行变更后通知容器重新拉 detail */
  onMutate(): void;
}
