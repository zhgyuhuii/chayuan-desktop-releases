/**
 * 工具中心数据层 hooks(TanStack Query 封装)。
 *
 * 设计:
 *  - QK_CATALOG = 全量目录(包含字段 schema + 当前值);60s 缓存
 *  - QK_CUSTOM  = 自定义工具列表(P3);独立缓存,改了刷
 *  - 单工具 update 不走单独 query,直接 mutate + invalidate catalog
 *
 * 复用:与 KB / Marketplace / MCP 等其它 board 的 React-Query 写法对齐。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  tools as toolsApi,
  type CustomToolSpec,
  type OpenApiDraft,
  type ToolCard,
  type ToolConfigUpdate,
} from '@chayuan/api';

export const QK_TOOLS_CATALOG = ['tools', 'catalog'] as const;
export const QK_TOOLS_CUSTOM = ['tools', 'custom'] as const;

/** 拉目录(卡片网格 + 字段 schema + 当前 yaml 值)。 */
export function useToolsCatalog() {
  return useQuery({
    queryKey: QK_TOOLS_CATALOG,
    queryFn: () => toolsApi.getCatalog(),
    staleTime: 60_000,
    retry: 0,
  });
}

/** 单工具配置 update。
 *
 *  - 入参 partial,只传改过的字段
 *  - 成功后 invalidate ``QK_TOOLS_CATALOG`` 让卡片网格立即反映最新 enabled / value
 *  - 同时把单卡片的 fresh 值塞进 query cache 的对应位置(O(1) 不重拉),避免闪烁
 */
export function useUpdateToolConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, update }: { key: string; update: ToolConfigUpdate }) =>
      toolsApi.updateConfig(key, update),
    onSuccess: (fresh: ToolCard) => {
      // 同步把新卡片回填到 catalog cache,避免 refetch 抖动
      qc.setQueryData<{ cards: ToolCard[]; categories: Record<string, string> } | undefined>(
        QK_TOOLS_CATALOG,
        (old) => {
          if (!old) return old;
          return {
            ...old,
            cards: old.cards.map((c) => (c.key === fresh.key ? fresh : c)),
          };
        },
      );
    },
  });
}

// ─── P3 自定义工具 ─────────────────────────────────────────────

export function useCustomTools() {
  return useQuery({
    queryKey: QK_TOOLS_CUSTOM,
    queryFn: () => toolsApi.listCustom(),
    staleTime: 30_000,
    retry: 0,
  });
}

export function useCreateCustomTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (spec: CustomToolSpec) => toolsApi.createCustom(spec),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK_TOOLS_CUSTOM });
    },
  });
}

export function useUpdateCustomTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, spec }: { name: string; spec: CustomToolSpec }) =>
      toolsApi.updateCustom(name, spec),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK_TOOLS_CUSTOM });
    },
  });
}

export function useDeleteCustomTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => toolsApi.deleteCustom(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK_TOOLS_CUSTOM });
    },
  });
}

// ─── P4 OpenAPI 导入 ───────────────────────────────────────────

export function useImportOpenApiPreview() {
  return useMutation({
    mutationFn: (req: { spec_url?: string; spec_text?: string }) =>
      toolsApi.importOpenApiPreview(req),
  });
}

export function useImportOpenApiCommit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: { drafts: OpenApiDraft[]; overwrite?: boolean }) =>
      toolsApi.importOpenApiCommit(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK_TOOLS_CUSTOM });
    },
  });
}
