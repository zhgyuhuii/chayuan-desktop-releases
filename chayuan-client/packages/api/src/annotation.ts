import { request } from './client';

const BASE = '/annotation';

export type AnnotationStatus =
  | 'pending'
  | 'in_progress'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'conflict'
  | string;

export interface AnnotationTask {
  id: string;
  source: string;
  task_type: string;
  status: AnnotationStatus;
  priority: number;
  target_type?: string | null;
  target_id?: string | null;
  route_context_id?: string | null;
  inputs: Record<string, unknown>;
  model_output: Record<string, unknown>;
  llm_prel_labels: Record<string, unknown>;
  labels: Record<string, unknown>;
  review: Record<string, unknown>;
  error_tags: string[];
  meta: Record<string, unknown>;
  note: string;
  assignee_id?: number | null;
  reviewer_id?: number | null;
  created_by?: number | null;
  updated_by?: number | null;
  create_time?: string | null;
  update_time?: string | null;
}

export interface AnnotationTaskCreateBody {
  source?: string;
  task_type?: string;
  target_type?: string | null;
  target_id?: string | null;
  route_context_id?: string | null;
  inputs?: Record<string, unknown>;
  model_output?: Record<string, unknown>;
  llm_prel_labels?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  priority?: number;
  note?: string;
}

export interface AnnotationTaskPatchBody {
  status?: AnnotationStatus;
  labels?: Record<string, unknown>;
  review?: Record<string, unknown>;
  error_tags?: string[];
  note?: string;
}

export interface AnnotationDatasetItem {
  id: string;
  task_type: string;
  input: Record<string, unknown>;
  model_output: Record<string, unknown>;
  labels: Record<string, unknown>;
  error_tags: string[];
  route_context_id?: string | null;
  meta: Record<string, unknown>;
}

export interface AnnotationImportResult {
  created: number;
  failed: number;
  skipped?: number;
  items: AnnotationTask[];
  errors: Array<{ index: number; error: string }>;
}

export interface AnnotationFeedback {
  id: string;
  task_id?: string | null;
  source: string;
  target_type?: string | null;
  target_id?: string | null;
  source_ref: Record<string, unknown>;
  rating?: number | null;
  quality: string;
  labels: Record<string, unknown>;
  error_tags: string[];
  comment: string;
  created_by?: number | null;
  create_time?: string | null;
}

export interface AnnotationFeedbackBody {
  task_id?: string | null;
  source?: string;
  target_type?: string | null;
  target_id?: string | null;
  source_ref?: Record<string, unknown>;
  rating?: number | null;
  quality?: string;
  labels?: Record<string, unknown>;
  error_tags?: string[];
  comment?: string;
  create_task?: boolean;
  task_type?: string;
  inputs?: Record<string, unknown>;
  model_output?: Record<string, unknown>;
}

export interface AnnotationUsageSummary {
  usable_total: number;
  by_task_type: Record<string, number>;
  by_target_type: Record<string, number>;
  online_consumers: string[];
}

export interface DataMount {
  id: string;
  name: string;
  description: string;
  scope_type: 'user' | 'kb' | 'group' | 'app' | 'global' | string;
  scope_id: string;
  source_filter: Record<string, unknown>;
  mount_modes: string[];
  priority: number;
  max_items: number;
  max_tokens: number;
  enabled: boolean;
  status: 'draft' | 'published' | 'archived' | string;
  version: number;
  published_at?: string | null;
  create_time?: string | null;
  update_time?: string | null;
  artifacts?: Array<{
    id: string;
    mount_id: string;
    version: number;
    artifact_type: string;
    payload: Record<string, unknown>;
    stats: Record<string, unknown>;
    checksum: string;
  }>;
}

export interface AnnotationDatasetMountBody {
  name: string;
  description?: string;
  task_type?: string;
  target_type?: string;
  target_id?: string;
  sample_ids?: string[];
  scope_type?: 'user' | 'kb' | 'group' | 'app' | 'global' | string;
  scope_id?: string;
  mount_modes?: string[];
  priority?: number;
  max_items?: number;
  max_tokens?: number;
  publish?: boolean;
}

export const annotationApi = {
  async createTask(body: AnnotationTaskCreateBody): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(`${BASE}/tasks`, {
      method: 'POST',
      body,
      raw: true,
    });
    if (!r.data?.data) throw new Error('createTask 返回为空');
    return r.data.data;
  },

  async sampleFromRouteContext(body: {
    route_context_id: string;
    task_type?: string;
    priority?: number;
    note?: string;
  }): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(`${BASE}/sample/route-context`, {
      method: 'POST',
      body,
      raw: true,
    });
    if (!r.data?.data) throw new Error('sampleFromRouteContext 返回为空');
    return r.data.data;
  },

  async sampleFromKbAsk(body: {
    query: string;
    ku_ids: string[];
    top_k?: number;
    result: Record<string, unknown>;
    options?: Record<string, unknown>;
    task_type?: string;
    priority?: number;
    note?: string;
  }): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(`${BASE}/sample/kb-ask`, {
      method: 'POST',
      body,
      raw: true,
    });
    if (!r.data?.data) throw new Error('sampleFromKbAsk 返回为空');
    return r.data.data;
  },

  async submitFeedback(body: AnnotationFeedbackBody): Promise<{
    feedback: AnnotationFeedback;
    task?: AnnotationTask | null;
  }> {
    const r = await request<{ data: { feedback: AnnotationFeedback; task?: AnnotationTask | null } }>(
      `${BASE}/feedback`,
      {
        method: 'POST',
        body,
        raw: true,
      },
    );
    if (!r.data?.data?.feedback) throw new Error('submitFeedback 返回为空');
    return r.data.data;
  },

  async listFeedback(opts?: {
    taskId?: string;
    targetType?: string;
    targetId?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ items: AnnotationFeedback[]; total: number }> {
    const qs = new URLSearchParams();
    if (opts?.taskId) qs.set('task_id', opts.taskId);
    if (opts?.targetType) qs.set('target_type', opts.targetType);
    if (opts?.targetId) qs.set('target_id', opts.targetId);
    if (opts?.limit) qs.set('limit', String(opts.limit));
    if (opts?.offset) qs.set('offset', String(opts.offset));
    const r = await request<{ data: { items: AnnotationFeedback[]; total: number } }>(
      `${BASE}/feedback${qs.size ? `?${qs.toString()}` : ''}`,
      { raw: true },
    );
    return r.data?.data ?? { items: [], total: 0 };
  },

  async listTasks(opts?: {
    status?: string;
    taskType?: string;
    source?: string;
    mine?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<{ items: AnnotationTask[]; total: number }> {
    const qs = new URLSearchParams();
    if (opts?.status) qs.set('status', opts.status);
    if (opts?.taskType) qs.set('task_type', opts.taskType);
    if (opts?.source) qs.set('source', opts.source);
    if (opts?.mine) qs.set('mine', 'true');
    if (opts?.limit) qs.set('limit', String(opts.limit));
    if (opts?.offset) qs.set('offset', String(opts.offset));
    const r = await request<{ data: { items: AnnotationTask[]; total: number } }>(
      `${BASE}/tasks${qs.size ? `?${qs.toString()}` : ''}`,
      { raw: true },
    );
    return r.data?.data ?? { items: [], total: 0 };
  },

  async getTask(taskId: string): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(
      `${BASE}/tasks/${encodeURIComponent(taskId)}`,
      { raw: true },
    );
    if (!r.data?.data) throw new Error('getTask 返回为空');
    return r.data.data;
  },

  async claimTask(taskId: string): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(
      `${BASE}/tasks/${encodeURIComponent(taskId)}/claim`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('claimTask 返回为空');
    return r.data.data;
  },

  async patchTask(taskId: string, body: AnnotationTaskPatchBody): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(
      `${BASE}/tasks/${encodeURIComponent(taskId)}`,
      { method: 'PATCH', body, raw: true },
    );
    if (!r.data?.data) throw new Error('patchTask 返回为空');
    return r.data.data;
  },

  async reviewTask(taskId: string, body: AnnotationTaskPatchBody): Promise<AnnotationTask> {
    const r = await request<{ data: AnnotationTask }>(
      `${BASE}/tasks/${encodeURIComponent(taskId)}/review`,
      { method: 'POST', body, raw: true },
    );
    if (!r.data?.data) throw new Error('reviewTask 返回为空');
    return r.data.data;
  },

  async exportDataset(opts?: {
    taskType?: string;
    status?: string;
    limit?: number;
  }): Promise<AnnotationDatasetItem[]> {
    const qs = new URLSearchParams({ fmt: 'json' });
    if (opts?.taskType) qs.set('task_type', opts.taskType);
    if (opts?.status) qs.set('status', opts.status);
    if (opts?.limit) qs.set('limit', String(opts.limit));
    const r = await request<{ data: { items: AnnotationDatasetItem[]; total: number } }>(
      `${BASE}/datasets/export?${qs.toString()}`,
      { raw: true },
    );
    return r.data?.data?.items ?? [];
  },

  async usageSummary(limit = 1000): Promise<AnnotationUsageSummary> {
    const qs = new URLSearchParams({ limit: String(limit) });
    const r = await request<{ data: AnnotationUsageSummary }>(
      `${BASE}/usage/summary?${qs.toString()}`,
      { raw: true },
    );
    return r.data?.data ?? {
      usable_total: 0,
      by_task_type: {},
      by_target_type: {},
      online_consumers: [],
    };
  },

  async usageSamples(opts?: {
    targetType?: string;
    targetId?: string;
    taskType?: string;
    limit?: number;
  }): Promise<{ items: AnnotationTask[]; total: number }> {
    const qs = new URLSearchParams();
    if (opts?.targetType) qs.set('target_type', opts.targetType);
    if (opts?.targetId) qs.set('target_id', opts.targetId);
    if (opts?.taskType) qs.set('task_type', opts.taskType);
    if (opts?.limit) qs.set('limit', String(opts.limit));
    const r = await request<{ data: { items: AnnotationTask[]; total: number } }>(
      `${BASE}/usage/samples${qs.size ? `?${qs.toString()}` : ''}`,
      { raw: true },
    );
    return r.data?.data ?? { items: [], total: 0 };
  },

  async importDataset(body: {
    items: Array<Record<string, unknown>>;
    source?: string;
    default_status?: string;
    note?: string;
  }): Promise<AnnotationImportResult> {
    const r = await request<{ data: AnnotationImportResult }>(
      `${BASE}/datasets/import`,
      { method: 'POST', body, raw: true },
    );
    if (!r.data?.data) throw new Error('importDataset 返回为空');
    return r.data.data;
  },

  async mountDataset(body: AnnotationDatasetMountBody): Promise<DataMount> {
    const r = await request<{ data: DataMount }>(
      `${BASE}/datasets/mount`,
      { method: 'POST', body, raw: true },
    );
    if (!r.data?.data) throw new Error('mountDataset 返回为空');
    return r.data.data;
  },

  datasetJsonlUrl(opts?: { taskType?: string; status?: string; limit?: number }): string {
    const qs = new URLSearchParams({ fmt: 'jsonl' });
    if (opts?.taskType) qs.set('task_type', opts.taskType);
    if (opts?.status) qs.set('status', opts.status);
    if (opts?.limit) qs.set('limit', String(opts.limit));
    return `${BASE}/datasets/export?${qs.toString()}`;
  },
};

export const dataMountApi = {
  async list(opts?: {
    status?: string;
    scopeType?: string;
    enabled?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<{ items: DataMount[]; total: number }> {
    const qs = new URLSearchParams();
    if (opts?.status) qs.set('status', opts.status);
    if (opts?.scopeType) qs.set('scope_type', opts.scopeType);
    if (opts?.enabled != null) qs.set('enabled', String(opts.enabled));
    if (opts?.limit) qs.set('limit', String(opts.limit));
    if (opts?.offset) qs.set('offset', String(opts.offset));
    const r = await request<{ data: { items: DataMount[]; total: number } }>(
      `/data-mounts${qs.size ? `?${qs.toString()}` : ''}`,
      { raw: true },
    );
    return r.data?.data ?? { items: [], total: 0 };
  },

  async publish(mountId: string): Promise<DataMount> {
    const r = await request<{ data: DataMount }>(
      `/data-mounts/${encodeURIComponent(mountId)}/publish`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('publish data mount 返回为空');
    return r.data.data;
  },

  async setEnabled(mountId: string, enabled: boolean): Promise<DataMount> {
    const r = await request<{ data: DataMount }>(
      `/data-mounts/${encodeURIComponent(mountId)}/${enabled ? 'enable' : 'disable'}`,
      { method: 'POST', raw: true },
    );
    if (!r.data?.data) throw new Error('setEnabled data mount 返回为空');
    return r.data.data;
  },
};
