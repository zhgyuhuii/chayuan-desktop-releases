/**
 * 模型平台状态层 — RQ keys + 写后热生效编排。
 *
 * 核心:写操作成功后**先 setQueryData 局部更新**(零闪烁),**再** invalidate models query
 * 让 ChatComposer / 模型广场 cards 自动用上最新平台配置。
 *
 * 后端契约:`bump_platform_version()` + `get_OpenAIClient` 不缓存
 *  → 任何后续 chat/embedding 调用都会读到最新 platform,无需重启服务。
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import {
  modelPlatform,
  type EnrichResult,
  type PlatformConfig,
  type PlatformCreate,
  type PlatformPatch,
  type PlatformTestResult,
  type ProviderCatalogEntry,
} from '@chayuan/api';

export const PLATFORM_KEYS = {
  list: ['marketplace.platforms'] as const,
  catalog: ['marketplace.catalog'] as const,
  /** marketplace 卡片用的 /v1/models 聚合(MarketplaceModel[],带 vendor 等) */
  models: ['marketplace', 'models'] as const,
  /**
   * Composer/Header ModelPicker 用的 raw /v1/models(RawModelItem[])。
   * 和 marketplace.models 共用同一个后端端点,但前端缓存 key 不同 —
   * 写后必须**两个 key 都 invalidate**,否则模型选择器要等用户手动刷新页面。
   */
  rawModels: ['v1.models'] as const,
} as const;

/** 列表查询;60s staleTime + keepPrev 避免抖动 */
export function usePlatformsQuery() {
  return useQuery<PlatformConfig[]>({
    queryKey: PLATFORM_KEYS.list,
    queryFn: () => modelPlatform.list(),
    staleTime: 60 * 1000,
    placeholderData: (prev) => prev,
  });
}

/** 全量厂商目录(public,~60 家含国内/国外/本地/聚合/推荐 标签 + 内置默认模型) */
export function usePlatformCatalogQuery() {
  return useQuery<ProviderCatalogEntry[]>({
    queryKey: PLATFORM_KEYS.catalog,
    queryFn: () => modelPlatform.catalog(),
    staleTime: 5 * 60 * 1000, // 目录稳定,5 分钟够用
    placeholderData: (prev) => prev,
    retry: 1, // 端点已对游客开放,偶发 5xx 可重试一次
  });
}

/** 写后热生效:setQueryData 局部更新 + 触发 models / catalog query 重拉 */
function applyPlatformWriteThrough(qc: QueryClient, next: PlatformConfig) {
  qc.setQueryData<PlatformConfig[]>(PLATFORM_KEYS.list, (prev) => {
    if (!prev) return [next];
    const idx = prev.findIndex((p) => p.platform_name === next.platform_name);
    if (idx === -1) return [...prev, next];
    const copy = [...prev];
    copy[idx] = next;
    return copy;
  });
  // catalog 也要更新 configured/enabled/llm_models 字段,直接 invalidate 让它重拉
  void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.catalog });
  // marketplace.models 走 /v1/models 聚合,平台改动后需要重拉
  void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.models });
  // ChatComposer / 顶部 ModelPicker 走的同源端点,key 不同 —— 一并失效,
  // 保证添加/启用平台后模型选择器立刻能看到新模型,无需刷新页面
  void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.rawModels });
  // 平台改动会让 /admin/capability_defaults 的 candidates 变化,且后端会自动
  // 把"还没默认的 cap"提升第一个候选为默认(见 CapabilityDefaultsResponse
  // 的 auto_assigned)。让设置页 DefaultModelsSection 和 AppFooter 都看到新默认。
  void qc.invalidateQueries({ queryKey: ['serverCapabilityDefaults'] });
}

export function useCreatePlatform() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PlatformCreate) => modelPlatform.create(payload),
    onSuccess: (data) => applyPlatformWriteThrough(qc, data),
  });
}

export function useUpdatePlatform() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: PlatformPatch }) =>
      modelPlatform.update(name, patch),
    onSuccess: (data) => applyPlatformWriteThrough(qc, data),
  });
}

export function useDeletePlatform() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => modelPlatform.delete(name),
    onSuccess: (_ok, name) => {
      qc.setQueryData<PlatformConfig[]>(PLATFORM_KEYS.list, (prev) =>
        prev ? prev.filter((p) => p.platform_name !== name) : prev,
      );
      void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.catalog });
      void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.models });
      void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.rawModels });
      // 删厂商后该厂商的模型会从 candidates 列表消失,默认可能落到别的厂商上
      void qc.invalidateQueries({ queryKey: ['serverCapabilityDefaults'] });
    },
  });
}

/**
 * 用 LLM 自动补全指定厂商模型的元信息(简介/发布日期/上下文/性能短评)。
 * 成功后 invalidate catalog,卡片 hover 立刻能看到新写入的元信息。
 */
export function useEnrichPlatform() {
  const qc = useQueryClient();
  return useMutation<EnrichResult, Error, { name: string; models?: string[] }>({
    mutationFn: ({ name, models }) => modelPlatform.enrich(name, models),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.catalog });
    },
  });
}

export function useTestPlatform() {
  const qc = useQueryClient();
  // 测试本身不写 DB(后端不修改配置),但成功后我们至少要让 models query 重拉,
  // 让用户在 dialog 里点了"一键填入"后,主界面的模型选择器也跟着出现新条目
  return useMutation<PlatformTestResult, Error, string>({
    mutationFn: (name: string) => modelPlatform.test(name),
    onSuccess: (data) => {
      if (data.reachable) {
        void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.rawModels });
        void qc.invalidateQueries({ queryKey: PLATFORM_KEYS.models });
      }
    },
  });
}
