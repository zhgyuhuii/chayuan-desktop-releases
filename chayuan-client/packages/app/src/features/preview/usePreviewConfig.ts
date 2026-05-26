/**
 * 全局预览配置 hook — 当前唯一字段是 kkFileView 旁车地址。
 *
 * 行为:
 *   - 启动时拉一次 /knowledge_base/preview-config(public,无 auth)
 *   - 缓存 5 分钟,避免每次开预览面板都打一次接口
 *   - 拉失败安静返回 ""(后端没起 / 老版本没这个端点),完全 fallback 到内置 renderer
 */
import { useQuery } from '@tanstack/react-query';
import { kb } from '@chayuan/api';

export interface PreviewConfig {
  /** kkFileView 基地址(空 = 未配置,前端走内置 renderer) */
  kkFileViewUrl: string;
}

const QK_PREVIEW_CONFIG = ['preview', 'config'] as const;

export function usePreviewConfig(): PreviewConfig {
  const q = useQuery({
    queryKey: QK_PREVIEW_CONFIG,
    queryFn: async () => {
      try {
        const r = await kb.getPreviewConfig();
        return { kkFileViewUrl: (r?.kkfileview_url || '').trim() };
      } catch {
        // 后端没接这个端点 / 网络抖动 — 全部回退到内置渲染
        return { kkFileViewUrl: '' };
      }
    },
    // 30s staleTime:用户在配置面板改完 KKFILEVIEW_URL 后,前端最长 30s 内
    // 切到新行为(kkFileView 全格式接管 / 内置 renderer);不做 5 分钟缓存
    // 是因为这是个低频接口、payload 极小,频繁查问题不大。
    staleTime: 30 * 1000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
    retry: 0,
  });
  return q.data ?? { kkFileViewUrl: '' };
}
