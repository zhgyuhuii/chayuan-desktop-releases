/**
 * Langfuse 代理 hooks：调用后端 chayuan-server 的 /admin/lf/* 端点。
 *
 * 设计：
 * - keepPreviousData 让分页跳转无空白；
 * - 30s staleTime；admin 看板不需要近实时；
 * - 失败 retry=0；UI 自行决定 fallback。
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { http } from '@chayuan/api';

export interface LfTrace {
  id: string;
  name?: string;
  userId?: string;
  sessionId?: string;
  release?: string;
  timestamp?: string;
  latency?: number;
  totalCost?: number;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
}

export interface LfTraceListResp {
  data: LfTrace[];
  meta: { totalItems: number; totalPages: number; page: number; limit: number };
}

export interface LfPrompt {
  name: string;
  versions?: number[];
  labels?: string[];
  tags?: string[];
  lastUpdated?: string;
}

export interface LfPromptListResp {
  data: LfPrompt[];
  meta: { totalItems: number; totalPages: number; page: number; limit: number };
}

export const ADMIN_KEYS = {
  traces: (params: Record<string, unknown>) => ['admin', 'lf', 'traces', params] as const,
  trace: (id: string) => ['admin', 'lf', 'trace', id] as const,
  prompts: (params: Record<string, unknown>) => ['admin', 'lf', 'prompts', params] as const,
  prompt: (name: string, version?: number) => ['admin', 'lf', 'prompt', name, version] as const,
  health: ['admin', 'lf', 'health'] as const,
};

export function useLfTraces(params: { sessionId?: string; userId?: string; name?: string; page?: number; limit?: number } = {}) {
  const query = { page: 1, limit: 50, ...params };
  return useQuery({
    queryKey: ADMIN_KEYS.traces(query),
    queryFn: () => http.get<LfTraceListResp>('/admin/lf/traces', { query }),
    staleTime: 30_000,
    retry: 0,
    placeholderData: keepPreviousData,
  });
}

export function useLfTrace(id: string | null) {
  return useQuery({
    enabled: !!id,
    queryKey: ADMIN_KEYS.trace(id ?? ''),
    queryFn: () => http.get<LfTrace>(`/admin/lf/traces/${encodeURIComponent(id ?? '')}`),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useLfPrompts(params: { label?: string; limit?: number } = {}) {
  const query = { limit: 100, ...params };
  return useQuery({
    queryKey: ADMIN_KEYS.prompts(query),
    queryFn: () => http.get<LfPromptListResp>('/admin/prompts', { query }),
    staleTime: 60_000,
    retry: 0,
    placeholderData: keepPreviousData,
  });
}

export function useLfPrompt(name: string | null, version?: number) {
  return useQuery({
    enabled: !!name,
    queryKey: ADMIN_KEYS.prompt(name ?? '', version),
    queryFn: () => http.get<unknown>(`/admin/prompts/${encodeURIComponent(name ?? '')}`, {
      query: version ? { version } : undefined,
    }),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useLfHealth() {
  return useQuery({
    queryKey: ADMIN_KEYS.health,
    queryFn: () => http.get<{ reachable: boolean; status?: number }>('/admin/health'),
    staleTime: 10_000,
    refetchInterval: 30_000,
    retry: 0,
  });
}
