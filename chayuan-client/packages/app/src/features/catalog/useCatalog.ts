/**
 * 服务端目录的统一查询。
 *
 * - tools / mcps / kbs / models 用 TanStack Query：
 *     · 60s staleTime（与旧 catalog store 行为一致）
 *     · 同 key 的并发自动去重（inflight）
 *     · 失败 retry=0（不打扰后端，让 UI 显示空状态）
 *
 * - 视图模型 mapping 放这里，UI 只接 *CardItem，不直接接后端 schema。
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { tools, mcp, kb, models, type ToolInfo, type McpConnection, type KnowledgeBase, type RawModelItem } from '@chayuan/api';

export const CATALOG_KEYS = {
  tools: ['catalog', 'tools'] as const,
  mcps: ['catalog', 'mcps'] as const,
  kbs: ['catalog', 'kbs'] as const,
  models: ['catalog', 'models'] as const,
};

export interface ToolCardItem {
  kind: 'tool';
  id: string;
  name: string;
  title: string;
  description: string;
  icon: string;
}
export interface McpCardItem {
  kind: 'mcp';
  id: string;
  name: string;
  title: string;
  description: string;
  icon: string;
}
export interface KbCardItem {
  kind: 'kb';
  id: string;
  name: string;
  title: string;
  description: string;
  visibility: string;
  icon: string;
}

const TOOL_ICON_MAP: Record<string, string> = {
  weather: '☀️',
  amap_weather: '☀️',
  calculate: '🧮',
  calculator: '🧮',
  search: '🔎',
  web_search: '🔎',
  search_internet: '🔎',
  duckduckgo_search: '🦆',
  bing_search: '🅱️',
  google_search: '🅶',
  arxiv: '📚',
  wikipedia: '📖',
  wolfram: '🧠',
  shell: '💻',
  python_repl: '🐍',
  amap_poi_search: '📍',
  url: '🔗',
  text2image: '🎨',
  translate: '🌐',
};

function pickToolIcon(name: string): string {
  const lower = name.toLowerCase();
  for (const k of Object.keys(TOOL_ICON_MAP)) {
    if (lower.includes(k)) return TOOL_ICON_MAP[k]!;
  }
  return '🧩';
}

function mapTool(t: ToolInfo): ToolCardItem {
  return {
    kind: 'tool',
    id: t.name,
    name: t.name,
    title: t.title || t.name,
    description: t.description || '',
    icon: pickToolIcon(t.name),
  };
}

function mapMcp(m: McpConnection): McpCardItem {
  return {
    kind: 'mcp',
    id: m.id,
    name: m.server_name,
    title: m.server_name,
    description: m.description || m.transport,
    icon: '🔌',
  };
}

function mapKb(k: KnowledgeBase): KbCardItem {
  return {
    kind: 'kb',
    id: k.kb_name,
    name: k.kb_name,
    title: k.kb_name,
    description: k.kb_info || `${k.file_count ?? 0} 文件`,
    visibility: k.visibility || 'private',
    icon: '📚',
  };
}

export function useTools(): UseQueryResult<ToolCardItem[]> {
  return useQuery({
    queryKey: CATALOG_KEYS.tools,
    queryFn: async () => (await tools.listEnabled()).map(mapTool),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useMcps(): UseQueryResult<McpCardItem[]> {
  return useQuery({
    queryKey: CATALOG_KEYS.mcps,
    queryFn: async () => (await mcp.list({ enabled: true, limit: 50 })).map(mapMcp),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useKbs(): UseQueryResult<KbCardItem[]> {
  return useQuery({
    queryKey: CATALOG_KEYS.kbs,
    queryFn: async () => (await kb.list()).map(mapKb),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useModels(): UseQueryResult<RawModelItem[]> {
  return useQuery({
    queryKey: CATALOG_KEYS.models,
    queryFn: () => models.list(),
    staleTime: 5 * 60_000,
    retry: 0,
  });
}

/** 启动后预拉，减少首次进对话区时的等待 */
export async function prefetchCatalog(qc: import('@tanstack/react-query').QueryClient): Promise<void> {
  await Promise.allSettled([
    qc.prefetchQuery({ queryKey: CATALOG_KEYS.tools, queryFn: async () => (await tools.listEnabled()).map(mapTool) }),
    qc.prefetchQuery({ queryKey: CATALOG_KEYS.mcps, queryFn: async () => (await mcp.list({ enabled: true, limit: 50 })).map(mapMcp) }),
    qc.prefetchQuery({ queryKey: CATALOG_KEYS.kbs, queryFn: async () => (await kb.list()).map(mapKb) }),
    qc.prefetchQuery({ queryKey: CATALOG_KEYS.models, queryFn: () => models.list() }),
  ]);
}
