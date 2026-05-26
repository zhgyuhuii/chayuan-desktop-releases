/**
 * /data-mounts/* 客户端 API。
 *
 * 与训练数据中心"数据挂载" tab + 历史 annotationApi.mountDataset 都接进同一
 * 后端;后者保留向后兼容。
 */
import { request } from './client';

const BASE = '/data-mounts';

// ---------- 数据类型 ----------

export type MountModeKey =
  | 'corpus' | 'context' | 'fewshot' | 'safety' | 'preference'
  // 老 mode 名(向后兼容,与 annotation 路径共用)
  | 'retrieval_boost' | 'safety_rule' | 'answer_style';

export type MountStatus = 'draft' | 'published' | 'disabled' | string;

export interface DataMountRecord {
  id: string;
  name: string;
  description: string;
  scope_type: string;
  scope_id: string;
  source_filter: Record<string, unknown>;
  mount_modes: MountModeKey[];
  priority: number;
  max_items: number;
  max_tokens: number;
  enabled: boolean;
  status: MountStatus;
  version: number;
  created_by?: number | null;
  updated_by?: number | null;
  published_at?: string | null;
  create_time?: string | null;
  update_time?: string | null;
  artifacts?: Array<{
    id: string;
    artifact_type: string;
    payload: Record<string, unknown>;
    stats: Record<string, unknown>;
  }>;
}

export interface SourceFormField {
  name: string;
  label: string;
  type: 'string' | 'int' | 'select' | 'password' | 'bool';
  required?: boolean;
  default?: unknown;
  help?: string;
  options?: Array<{ value: string; label: string }>;
}

export interface SourceCatalogItem {
  type_id: string;
  label: string;
  description: string;
  icon: string;
  capabilities: string[];
  spec_form: { fields: SourceFormField[] };
}

export interface ProbeResult {
  status: 'ok' | 'warning' | 'error';
  message: string;
  counted?: number | null;
  extra?: Record<string, unknown>;
}

export interface FieldSchema {
  name: string;
  type: string;
  sample_values: unknown[];
  fill_rate: number;
  unique_count: number;
  notes: string;
}

export interface DocumentRecord {
  id?: string | null;
  text: string;
  metadata: Record<string, unknown>;
}

export interface SampleResult {
  items: DocumentRecord[];
  total_estimate?: number | null;
  fields: FieldSchema[];
}

export interface MountCreateBody {
  name: string;
  description?: string;
  scope_type?: string;
  scope_id?: string;
  source_filter?: Record<string, unknown>;
  mount_modes?: MountModeKey[];
  priority?: number;
  max_items?: number;
  max_tokens?: number;
}

export interface MountPatchBody {
  name?: string;
  description?: string;
  scope_type?: string;
  scope_id?: string;
  source_filter?: Record<string, unknown>;
  mount_modes?: MountModeKey[];
  priority?: number;
  max_items?: number;
  max_tokens?: number;
  enabled?: boolean;
}

// ---------- API ----------

export const dataMountsApi = {
  // 数据源目录
  async listSources(): Promise<SourceCatalogItem[]> {
    const r = await request<{ data: SourceCatalogItem[] }>(`${BASE}/sources`, { raw: true });
    return r.data?.data ?? [];
  },
  async probe(body: { source_type: string; options: Record<string, unknown>; max_items?: number }): Promise<ProbeResult> {
    const r = await request<{ data: ProbeResult }>(`${BASE}/sources/probe`, {
      method: 'POST', body, raw: true,
    });
    if (!r.data?.data) throw new Error('probe 返回为空');
    return r.data.data;
  },
  async analyze(body: { source_type: string; options: Record<string, unknown>; sample_size?: number }): Promise<SampleResult> {
    const r = await request<{ data: SampleResult }>(`${BASE}/sources/analyze`, {
      method: 'POST', body, raw: true,
    });
    if (!r.data?.data) throw new Error('analyze 返回为空');
    return r.data.data;
  },

  // CRUD
  async list(params?: { scope_type?: string; status?: string }): Promise<DataMountRecord[]> {
    const qs = new URLSearchParams();
    if (params?.scope_type) qs.set('scope_type', params.scope_type);
    if (params?.status) qs.set('status', params.status);
    // 服务端 /data-mounts 返回 { code, msg, data: { items: [], total } };
    // 但历史调用方期望 .data 直接是数组,这里把两种都兼容掉,避免任何变动炸 .map。
    const r = await request<{
      data: DataMountRecord[] | { items?: DataMountRecord[]; total?: number };
    }>(`${BASE}${qs.size ? `?${qs.toString()}` : ''}`, { raw: true });
    const payload = r.data?.data;
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray((payload as { items?: DataMountRecord[] }).items)) {
      return (payload as { items: DataMountRecord[] }).items;
    }
    return [];
  },
  async get(mountId: string): Promise<DataMountRecord> {
    const r = await request<{ data: DataMountRecord }>(`${BASE}/${mountId}`, { raw: true });
    if (!r.data?.data) throw new Error('mount 不存在');
    return r.data.data;
  },
  async create(body: MountCreateBody): Promise<DataMountRecord> {
    const r = await request<{ data: DataMountRecord }>(BASE, {
      method: 'POST', body, raw: true,
    });
    if (!r.data?.data) throw new Error('create 返回为空');
    return r.data.data;
  },
  async patch(mountId: string, body: MountPatchBody): Promise<DataMountRecord> {
    const r = await request<{ data: DataMountRecord }>(`${BASE}/${mountId}`, {
      method: 'PATCH', body, raw: true,
    });
    if (!r.data?.data) throw new Error('patch 返回为空');
    return r.data.data;
  },
  async preview(mountId: string): Promise<{
    mount: DataMountRecord;
    sample_count: number;
    sample_ids: string[];
    fields?: FieldSchema[];
    preview_records?: DocumentRecord[];
    artifacts: Array<{ artifact_type: string; payload: Record<string, unknown>; stats?: Record<string, unknown> }>;
    error?: string;
  }> {
    const r = await request<{ data: unknown }>(`${BASE}/${mountId}/preview`, {
      method: 'POST', raw: true,
    });
    return (r.data?.data ?? {}) as ReturnType<typeof dataMountsApi.preview> extends Promise<infer T> ? T : never;
  },
  async publish(mountId: string): Promise<DataMountRecord> {
    const r = await request<{ data: DataMountRecord }>(`${BASE}/${mountId}/publish`, {
      method: 'POST', raw: true,
    });
    if (!r.data?.data) throw new Error('publish 返回为空');
    return r.data.data;
  },
  async setEnabled(mountId: string, enabled: boolean): Promise<DataMountRecord> {
    const path = enabled ? 'enable' : 'disable';
    const r = await request<{ data: DataMountRecord }>(`${BASE}/${mountId}/${path}`, {
      method: 'POST', raw: true,
    });
    if (!r.data?.data) throw new Error(`${path} 返回为空`);
    return r.data.data;
  },

  // 导入 / 导出
  async import(body: {
    format: 'json' | 'csv';
    content: string;
    scope_type?: string;
    scope_id?: string;
    publish?: boolean;
  }): Promise<{ created: unknown[]; errors: Array<{ name?: string; error: string }> }> {
    const r = await request<{ data: { created: unknown[]; errors: Array<{ name?: string; error: string }> } }>(
      `${BASE}/import`, { method: 'POST', body, raw: true },
    );
    return r.data?.data ?? { created: [], errors: [] };
  },
  async export(mountId: string): Promise<DataMountRecord> {
    const r = await request<{ data: DataMountRecord }>(`${BASE}/${mountId}/export`, { raw: true });
    if (!r.data?.data) throw new Error('export 返回为空');
    return r.data.data;
  },
};

// ---------- KB 端: corpus_pending 待 ingest 任务管理 ----------

export interface PendingMount {
  id: string;
  mount_id: string;
  version: number;
  artifact_type: string;
  payload: Record<string, unknown>;
  stats: Record<string, unknown>;
  item_count: number;
  mount: DataMountRecord;
  create_time?: string | null;
}

export const kbPendingMountsApi = {
  async list(kbName: string): Promise<{ items: PendingMount[]; total: number }> {
    const r = await request<{ data: { items: PendingMount[]; total: number } }>(
      `/knowledge_base/${encodeURIComponent(kbName)}/pending_mounts`, { raw: true },
    );
    return r.data?.data ?? { items: [], total: 0 };
  },
  async confirm(kbName: string, artifactId: string): Promise<{ ingested: number; total_items: number }> {
    const r = await request<{ data: { ingested: number; total_items: number } }>(
      `/knowledge_base/${encodeURIComponent(kbName)}/pending_mounts/${artifactId}/confirm`,
      { method: 'POST', raw: true },
    );
    return r.data?.data ?? { ingested: 0, total_items: 0 };
  },
  async reject(kbName: string, artifactId: string, reason?: string): Promise<void> {
    await request(
      `/knowledge_base/${encodeURIComponent(kbName)}/pending_mounts/${artifactId}/reject`,
      { method: 'POST', body: { reason: reason ?? '' }, raw: true },
    );
  },
};
