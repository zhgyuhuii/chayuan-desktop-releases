/**
 * 模型厂商公开 API（``/v1/providers/*``）。
 *
 * 单机版前端「模型广场」专用入口；底层与 ``modelPlatform``(``/admin/model_platforms``)
 * 复用同一组 repository / utils，但路由对游客开放（CLAUDE.md §1）。
 *
 * 路由契约（与后端 ``provider_routes.py`` 对齐）:
 *   GET    /v1/providers?category=…           列出厂商，按 7 类分类过滤
 *   POST   /v1/providers                      新增厂商（自定义 / 手动追加）
 *   PATCH  /v1/providers/:pid                 修改厂商配置
 *   DELETE /v1/providers/:pid                 删除厂商
 *   POST   /v1/providers/:pid/test            探测连通性 + 返回 detected
 *   POST   /v1/providers/:pid/refresh-models  仅返 detected
 *   GET    /v1/providers/:pid/models          当前已选模型分组 + disabled_models
 *   PATCH  /v1/providers/:pid/models/:model_id 启停模型（写 disabled_models）
 */

import { request } from './client';
import type {
  PlatformConfig,
  PlatformPatch,
  PlatformTestResult,
  ProviderCatalogEntry,
} from './modelPlatform';

/** 与后端 _CATEGORY_TAG_MAP / 前端 modelMarket.region 对齐的分类。 */
export type ProviderCategory =
  | 'recommended'
  | 'all'
  | 'local'
  | 'cn'
  | 'global'
  | 'gateway'
  | 'custom';

const BASE = '/v1/providers';

export interface ProviderModelsGroup {
  platform_name: string;
  models: Partial<Record<keyof PlatformConfig, string[]>> & Record<string, string[]>;
  disabled_models: string[];
}

export const providers = {
  /** 按分类列出全部厂商。空 / 'all' 返回全部。 */
  async list(category?: ProviderCategory): Promise<ProviderCatalogEntry[]> {
    const r = await request<{ data: ProviderCatalogEntry[] }>(BASE, {
      query: category && category !== 'all' ? { category } : undefined,
      raw: true,
    });
    return r.data?.data ?? [];
  },

  /** 新增厂商；platform_name 必填。 */
  async create(payload: { platform_name: string } & PlatformPatch): Promise<PlatformConfig> {
    const r = await request<{ data: PlatformConfig }>(BASE, {
      method: 'POST',
      body: payload,
      raw: true,
    });
    if (!r.data?.data) throw new Error('create 返回为空');
    return r.data.data;
  },

  /** PATCH 任意字段，保存即生效。 */
  async update(pid: string, patch: PlatformPatch): Promise<PlatformConfig> {
    const r = await request<{ data: PlatformConfig }>(`${BASE}/${encodeURIComponent(pid)}`, {
      method: 'PATCH',
      body: patch,
      raw: true,
    });
    if (!r.data?.data) throw new Error('update 返回为空');
    return r.data.data;
  },

  async delete(pid: string): Promise<boolean> {
    const r = await request<{ code: number }>(`${BASE}/${encodeURIComponent(pid)}`, {
      method: 'DELETE',
      raw: true,
    });
    return r.data?.code === 0;
  },

  /** 连通性测试 + 自动探测可用模型；不写 DB。 */
  async test(pid: string): Promise<PlatformTestResult> {
    const r = await request<{ data: PlatformTestResult }>(
      `${BASE}/${encodeURIComponent(pid)}/test`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('test 返回为空');
    return r.data.data;
  },

  /** 仅刷新该厂商可用模型清单。 */
  async refreshModels(pid: string): Promise<{ platform_name: string; detected: Record<string, string[]> }> {
    const r = await request<{ data: { platform_name: string; detected: Record<string, string[]> } }>(
      `${BASE}/${encodeURIComponent(pid)}/refresh-models`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('refresh-models 返回为空');
    return r.data.data;
  },

  async listModels(pid: string): Promise<ProviderModelsGroup> {
    const r = await request<{ data: ProviderModelsGroup }>(
      `${BASE}/${encodeURIComponent(pid)}/models`,
      { raw: true },
    );
    if (!r.data?.data) throw new Error('list models 返回为空');
    return r.data.data;
  },

  /** 启停单个模型；写到厂商的 disabled_models 黑名单。 */
  async setModelEnabled(pid: string, modelId: string, enabled: boolean): Promise<PlatformConfig> {
    const r = await request<{ data: PlatformConfig }>(
      `${BASE}/${encodeURIComponent(pid)}/models/${encodeURIComponent(modelId)}`,
      {
        method: 'PATCH',
        body: { enabled },
        raw: true,
      },
    );
    if (!r.data?.data) throw new Error('patch model 返回为空');
    return r.data.data;
  },
};
