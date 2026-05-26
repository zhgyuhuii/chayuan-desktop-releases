import { QueryClient } from '@tanstack/react-query';

/**
 * 全局 QueryClient。
 * - staleTime 默认 30s：减少多组件订阅相同数据时的抖动；
 * - retry 1：避免对后端重试风暴；
 * - refetchOnWindowFocus = false：桌面应用窗口频繁聚焦不应触发额外网络。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: 0 },
  },
});
