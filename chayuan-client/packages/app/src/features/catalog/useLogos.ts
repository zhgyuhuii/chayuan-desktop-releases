/**
 * 模型 logo 解析。
 *
 * - 启动时拉一次 /img/model_logos/logos-manifest.json，TanStack Query 缓存到 24h；
 * - 解析逻辑（normalize + 包含匹配）保持与旧 Vue 版一致，方便回归对比；
 * - 跨端：逻辑无平台差异；URL 拼接走 getClientConfig().baseURL。
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { models, getClientConfig } from '@chayuan/api';

const QK = ['catalog', 'logos-manifest'] as const;

export function useLogosManifest() {
  return useQuery({
    queryKey: QK,
    queryFn: () => models.logosManifest(),
    staleTime: 24 * 60 * 60_000, // 24h
    retry: 0,
  });
}

/**
 * 把 platformName / modelId 解析成 logo 绝对 URL；找不到返回 undefined。
 * Hook 接口；返回纯函数稳定引用以便 memo 化下游。
 */
export function useResolveLogo(): (platformName?: string, modelId?: string) => string | undefined {
  const qc = useQueryClient();
  return (platformName?: string, modelId?: string) => {
    const manifest = qc.getQueryData<Record<string, string>>(QK) ?? {};
    if (!manifest || !Object.keys(manifest).length) return undefined;
    return resolveLogoUrl(manifest, getClientConfig().baseURL, platformName, modelId);
  };
}

/** 纯函数：测试友好；与 useResolveLogo 共享实现 */
export function resolveLogoUrl(
  manifest: Record<string, string>,
  apiBase: string,
  platformName?: string,
  modelId?: string,
): string | undefined {
  if (!Object.keys(manifest).length) return undefined;
  const isAbsolute = /^https?:\/\//i.test(apiBase);
  const prefix = isAbsolute ? apiBase.replace(/\/+$/, '') : '';
  const normalize = (s: string) => s.toLowerCase().replace(/[\s_-]/g, '');
  const keys = Object.keys(manifest);

  const candidates: string[] = [];
  if (platformName) candidates.push(platformName);
  if (modelId) {
    const mid = modelId.toLowerCase();
    const p = mid.split(/[-/_:]/)[0];
    if (p) candidates.push(p);
  }
  for (const c of candidates) {
    const n = normalize(c);
    const exact = keys.find((k) => normalize(k) === n);
    if (exact) return `${prefix}/img/model_logos/${exact}.${manifest[exact]}`;
    const loose = keys.find((k) => !k.includes('_dark') && normalize(k).includes(n));
    if (loose) return `${prefix}/img/model_logos/${loose}.${manifest[loose]}`;
  }
  return undefined;
}
