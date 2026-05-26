import { request } from './client';

export interface ImageModelItem {
  name: string;
  family?: string;
  dim?: number;
  approx_size_mb?: number;
  cached?: boolean;
  cached_size_mb?: number;
  deps_available?: boolean;
  deps_reason?: string;
  ready?: boolean;
  smoke_tested?: boolean;
  smoke_tested_at?: number;
  smoke_error?: string;
  chinese_level?: string;
  description?: string;
  capabilities?: {
    image?: boolean;
    text?: boolean;
    crossmodal?: boolean;
  };
}

export interface ImageModelDiskUsage {
  root: string;
  exists: boolean;
  total_mb: number;
  items: Array<{ name: string; path: string; size_mb: number }>;
}

export const imageModels = {
  async list(opts: { ready?: boolean; crossmodalOnly?: boolean } = {}): Promise<ImageModelItem[]> {
    return (
      await request<ImageModelItem[]>('/image_models/', {
        query:
          opts.ready || opts.crossmodalOnly
            ? {
                ...(opts.ready ? { ready: true } : {}),
                ...(opts.crossmodalOnly ? { crossmodal_only: true } : {}),
              }
            : undefined,
      })
    ).data;
  },

  async diskUsage(): Promise<ImageModelDiskUsage> {
    return (await request<ImageModelDiskUsage>('/image_models/disk_usage')).data;
  },

  async download(modelName: string, sync = false): Promise<Record<string, unknown>> {
    return (
      await request<Record<string, unknown>>('/image_models/download', {
        method: 'POST',
        body: { model_name: modelName, sync },
        timeoutMs: 60_000,
      })
    ).data;
  },

  async smokeTest(modelName: string, sync = true): Promise<Record<string, unknown>> {
    const pathName = modelName.split('/').map(encodeURIComponent).join('/');
    return (
      await request<Record<string, unknown>>(`/image_models/${pathName}/smoke_test`, {
        method: 'POST',
        query: { sync },
        timeoutMs: 120_000,
      })
    ).data;
  },

  async uploadBundle(modelName: string, file: File): Promise<Record<string, unknown>> {
    const body = new FormData();
    body.set('model_name', modelName);
    body.set('bundle', file);
    return (
      await request<Record<string, unknown>>('/image_models/upload_bundle', {
        method: 'POST',
        body,
        timeoutMs: 120_000,
      })
    ).data;
  },

  async delete(modelName: string): Promise<void> {
    const pathName = modelName.split('/').map(encodeURIComponent).join('/');
    await request(`/image_models/${pathName}`, { method: 'DELETE' });
  },
};
