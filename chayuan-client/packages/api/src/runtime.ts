/**
 * 运行时编排 API（与 chayuan-server `chayuan/server/api_server/runtime_routes.py` 对齐）。
 *
 * 暴露三组能力：
 *   1. 受管服务端点 + 凭据（密码默认 ****）
 *   2. vendor/ 离线包扫描结果
 *   3. 本地模型索引（增量扫描 + SSE 实时事件）
 *
 * 与"模型广场"（marketplace）的区别：
 *   - marketplace.* 走 `/model_registry/*`，处理"远端 Hub 索引 / 下载"
 *   - runtime.models.* 走 `/runtime/models/*`，处理"本机磁盘已有的文件"
 *
 * SSE 在 web/desktop 平台行为有差异：
 *   - web：原生 EventSource，自动重连
 *   - desktop（Tauri 2）：fetch + ReadableStream；本模块按"用 EventSource 包一层"
 *     抽象，外层 hook 不需要关心。
 */

import { request, getClientConfig } from './client';

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

export interface RuntimeServiceEndpoint {
  name?: string;
  host?: string;
  port?: number;
  scheme?: string;
  user?: string;
  /** 默认 ****；reveal=true 时为明文 */
  password?: string;
  database?: string;
  url?: string;
  console_url?: string;
  health_url?: string;
  kind?: string;
  version?: string;
  binary?: string;
  pid?: number | null;
  started_at?: number;
  extra?: Record<string, unknown>;
}

export interface RuntimeServicesPayload {
  updated_at?: number;
  services: RuntimeServiceEndpoint[];
  warning?: string;
}

export interface VendorEntry {
  group: 'services' | 'runtimes' | 'unknown' | string;
  name: string;
  label: string;
  kind: string;
  path: string;
  binary?: string;
  docker_compose?: string;
  readme?: string;
  default_port?: string;
  available: boolean;
  issues: string[];
}

export interface VendorPayload {
  root: string;
  services: VendorEntry[];
  runtimes: VendorEntry[];
  unknown: VendorEntry[];
  known_services: Record<string, Record<string, string>>;
  known_runtimes: Record<string, Record<string, string>>;
}

export interface LocalModelEntry {
  model_id: string;
  path: string;
  relpath: string;
  capability: string;
  family: string;
  format: string;
  size_bytes: number;
  mtime: number;
  confidence: number;
  evidence: string[];
  meta: Record<string, unknown>;
  source_tag: string;
}

export interface ScanDeltaPayload {
  added: LocalModelEntry[];
  updated: LocalModelEntry[];
  removed: string[];
}

export interface BootstrapResultPayload {
  api: RuntimeServiceEndpoint;
  config_panel: RuntimeServiceEndpoint;
  vendor: RuntimeServiceEndpoint[];
  warnings: string[];
}

// ---------------------------------------------------------------------------
// services
// ---------------------------------------------------------------------------

export const runtimeServices = {
  async list(opts: { reveal?: boolean } = {}): Promise<RuntimeServicesPayload> {
    return (
      await request<RuntimeServicesPayload>('/runtime/services', {
        query: opts.reveal ? { reveal: true } : undefined,
      })
    ).data;
  },

  async get(name: string, opts: { reveal?: boolean } = {}): Promise<RuntimeServiceEndpoint> {
    return (
      await request<RuntimeServiceEndpoint>(`/runtime/services/${encodeURIComponent(name)}`, {
        query: opts.reveal ? { reveal: true } : undefined,
      })
    ).data;
  },

  async recheck(): Promise<BootstrapResultPayload> {
    return (
      await request<BootstrapResultPayload>('/runtime/services/recheck', {
        method: 'POST',
        timeoutMs: 60_000,
      })
    ).data;
  },
};

// ---------------------------------------------------------------------------
// vendor
// ---------------------------------------------------------------------------

export const runtimeVendor = {
  async list(): Promise<VendorPayload> {
    return (await request<VendorPayload>('/runtime/vendor')).data;
  },
};

// ---------------------------------------------------------------------------
// local models
// ---------------------------------------------------------------------------

export const runtimeModels = {
  async list(opts: { capability?: string } = {}): Promise<{ total: number; items: LocalModelEntry[] }> {
    return (
      await request<{ total: number; items: LocalModelEntry[] }>('/runtime/models', {
        query: opts.capability ? { capability: opts.capability } : undefined,
      })
    ).data;
  },

  async scan(): Promise<ScanDeltaPayload> {
    return (await request<ScanDeltaPayload>('/runtime/models/scan', { method: 'POST', timeoutMs: 120_000 })).data;
  },

  async importLocalPath(path: string, name?: string): Promise<{ action: string; target: string; scan_delta_added: number }> {
    return (
      await request<{ action: string; target: string; scan_delta_added: number }>(
        '/runtime/models/import',
        { method: 'POST', body: { path, name }, timeoutMs: 120_000 },
      )
    ).data;
  },

  /**
   * 订阅 SSE 事件流。返回 unsubscribe 函数。
   *
   * 三种事件：
   *   - `model.snapshot`：连接成功后立即收到一次全量快照（{items: LocalModelEntry[]}）
   *   - `model.changed` ：增/改/删差异（ScanDeltaPayload）
   *   - `ping`          ：心跳（每 25s 一次）
   */
  subscribeEvents(handlers: {
    onSnapshot?: (items: LocalModelEntry[]) => void;
    onChange?: (delta: ScanDeltaPayload & { ts: number }) => void;
    onError?: (err: unknown) => void;
  }): () => void {
    const cfg = getClientConfig();
    const url = `${cfg.baseURL.replace(/\/+$/, '')}/runtime/models/events`;
    let es: EventSource | null = null;
    try {
      es = new EventSource(url, { withCredentials: cfg.authMode === 'cookie' });
    } catch (e) {
      handlers.onError?.(e);
      return () => undefined;
    }
    es.addEventListener('model.snapshot', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data);
        handlers.onSnapshot?.(data?.items ?? []);
      } catch (e) {
        handlers.onError?.(e);
      }
    });
    es.addEventListener('model.changed', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data);
        handlers.onChange?.(data);
      } catch (e) {
        handlers.onError?.(e);
      }
    });
    es.onerror = (ev) => handlers.onError?.(ev);
    return () => {
      try {
        es?.close();
      } catch {
        // noop
      }
    };
  },
};

export const runtime = {
  services: runtimeServices,
  vendor: runtimeVendor,
  models: runtimeModels,
};
