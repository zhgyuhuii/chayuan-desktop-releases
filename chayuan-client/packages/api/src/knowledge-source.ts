/**
 * 结构化数据源(/knowledge_source/*)对接层。
 *
 * 后端契约:
 *   GET    /knowledge_source/dialects                   → { mysql: 'MySQL', kingbase: '人大金仓 …', dm: '达梦 …', ... }
 *   POST   /knowledge_source/test_connection            → { ok, msg, error_code? }
 *   POST   /knowledge_source/                           → { id, connection_id }
 *   GET    /knowledge_source/                           → KnowledgeSource[]
 *   GET    /knowledge_source/{id}                       → KnowledgeSource
 *   PATCH  /knowledge_source/{id}
 *   DELETE /knowledge_source/{id}
 *   POST   /knowledge_source/{id}/introspect            → 刷新 schema 缓存
 *
 * dialect 列表全部从后端拉(包含 kingbase / dm / hive 等),前端不再硬编码;
 * 但保留一份 fallback,作为后端不可达时的兜底(参考 ADR-0004 同形思想)。
 */

import { http, request } from './client';

// ─────────────────────────────────────────────────────────────────────
// 类型
// ─────────────────────────────────────────────────────────────────────

export type KsKind = 'sql' | 'mongo' | 'es' | 'vector' | 'image' | 'vs';
export type KsVisibility = 'private' | 'public';

export interface KnowledgeSource {
  id: number;
  name: string;
  kind: KsKind;
  display_name?: string;
  description?: string;
  connection_id?: number;
  dialect?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  visibility?: KsVisibility;
  owner_id?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface TestConnectionInput {
  dialect: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  options?: Record<string, unknown>;
  /** 消歧:'vs' 时走外部向量库通道 */
  kind?: '' | 'vs';
  /** 复用已有连接(后端 patch 式补字段) */
  connection_id?: number;
}

export interface TestConnectionResult {
  ok: boolean;
  msg: string;
  error_code?: string;
}

export interface CreateSourceInput {
  name: string;
  kind: KsKind;
  display_name?: string;
  description?: string;
  dialect?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  options?: Record<string, unknown>;
  /** 白名单:tables/collections/indices */
  allowed?: {
    tables?: string[];
    collections?: string[];
    indices?: string[];
  };
  visibility?: KsVisibility;
}

// ─────────────────────────────────────────────────────────────────────
// dialect 元数据 —— 默认端口 + 友好名 fallback
// ─────────────────────────────────────────────────────────────────────

/**
 * SQL 方言的默认端口与图标 hint。
 * 与后端 sql/dialects.py 的 default_port 对齐(包括金仓 54321、达梦 5236)。
 */
export interface DialectMeta {
  /** 默认端口 */
  defaultPort: number;
  /** 友好显示名(备用,后端不可达时使用) */
  fallbackLabel: string;
  /** 是否需要 database 字段 */
  requiresDatabase: boolean;
  /** 'sql' / 'mongo' / 'es' / 'image' / 'vs' */
  kind: KsKind;
  /** 备注:协议兼容性等 */
  hint?: string;
}

export const DIALECT_META: Record<string, DialectMeta> = {
  // —— 通用 SQL ——
  mysql: { kind: 'sql', defaultPort: 3306, fallbackLabel: 'MySQL', requiresDatabase: true },
  postgres: { kind: 'sql', defaultPort: 5432, fallbackLabel: 'PostgreSQL', requiresDatabase: true },
  sqlite: {
    kind: 'sql', defaultPort: 0, fallbackLabel: 'SQLite', requiresDatabase: true,
    hint: 'database 填本地路径或 :memory:',
  },
  mssql: { kind: 'sql', defaultPort: 1433, fallbackLabel: 'SQL Server', requiresDatabase: true },
  oracle: {
    kind: 'sql', defaultPort: 1521, fallbackLabel: 'Oracle', requiresDatabase: true,
    hint: 'options.service_name 可指定服务名',
  },
  clickhouse: { kind: 'sql', defaultPort: 8123, fallbackLabel: 'ClickHouse', requiresDatabase: true },
  doris: {
    kind: 'sql', defaultPort: 9030, fallbackLabel: 'Apache Doris / StarRocks', requiresDatabase: true,
    hint: '兼容 MySQL 协议',
  },
  hive: {
    kind: 'sql', defaultPort: 10000, fallbackLabel: 'Apache Hive', requiresDatabase: true,
    hint: 'options.auth: NONE / NOSASL / LDAP / KERBEROS',
  },
  // —— 国产信创(后端 sql/dialects.py 已支持) ——
  kingbase: {
    kind: 'sql', defaultPort: 54321, fallbackLabel: '人大金仓 KingbaseES', requiresDatabase: true,
    hint: 'PG 协议兼容;驱动 ksycopg2 / kingbase8 / psycopg2',
  },
  dm: {
    kind: 'sql', defaultPort: 5236, fallbackLabel: '达梦 DM', requiresDatabase: false,
    hint: 'Oracle 协议兼容;驱动 dmPython',
  },
  // —— NoSQL & 文本搜索 ——
  mongo: { kind: 'mongo', defaultPort: 27017, fallbackLabel: 'MongoDB', requiresDatabase: true },
  es: { kind: 'es', defaultPort: 9200, fallbackLabel: 'Elasticsearch', requiresDatabase: false },
  // —— 多模态 ——
  image: {
    kind: 'image', defaultPort: 0, fallbackLabel: '图像源(CLIP / Chinese-CLIP)', requiresDatabase: false,
    hint: '本地存储,无远端连接',
  },
};

/** dialect → 默认端口;未知方言返回 0 */
export function getDefaultPort(dialect: string): number {
  return DIALECT_META[dialect]?.defaultPort ?? 0;
}

/** 兜底列表(后端 /dialects 调失败时使用)。包含金仓 / 达梦。 */
export const FALLBACK_DIALECTS: Record<string, string> = Object.fromEntries(
  Object.entries(DIALECT_META).map(([k, v]) => [k, v.fallbackLabel]),
);

// ─────────────────────────────────────────────────────────────────────
// 端点封装
// ─────────────────────────────────────────────────────────────────────

export const knowledgeSource = {
  /** 拉取后端支持的方言;失败回退到 FALLBACK_DIALECTS(仍含金仓/达梦) */
  async listDialects(): Promise<Record<string, string>> {
    try {
      const res = await request<{ data: Record<string, string> }>(
        '/knowledge_source/dialects',
        { raw: true },
      );
      return res.data?.data ?? FALLBACK_DIALECTS;
    } catch {
      return FALLBACK_DIALECTS;
    }
  },

  testConnection: (body: TestConnectionInput) =>
    http.post<TestConnectionResult>('/knowledge_source/test_connection', body),

  list: () => http.get<KnowledgeSource[]>('/knowledge_source/'),

  get: (id: number) =>
    http.get<KnowledgeSource>(`/knowledge_source/${id}`),

  create: (body: CreateSourceInput) =>
    http.post<{ id: number; connection_id: number }>('/knowledge_source/', body),

  update: (id: number, patch: Partial<CreateSourceInput>) =>
    http.patch<KnowledgeSource>(`/knowledge_source/${id}`, patch),

  delete: (id: number) =>
    http.del<void>(`/knowledge_source/${id}`),

  introspect: (id: number) =>
    http.post<{ ok: boolean; tables?: number }>(
      `/knowledge_source/${encodeURIComponent(String(id))}/introspect`,
      {},
    ),

  /**
   * 拉某个数据源的"轻量目录"——表/集合/索引名 + 当前白名单。
   * 配合 SchemaScopePicker 使用:用 names 渲染选项,用 allowed_* 回填已选。
   * `refresh=true` 强制后端重做 introspect(表结构变更后可手动刷)。
   */
  catalog: (
    id: number,
    refresh = false,
  ): Promise<{
    dialect: string;
    kind: string;
    names: string[];
    count: number;
    allowed_tables: string[];
    allowed_collections: string[];
    allowed_indices: string[];
  }> =>
    http.get(
      `/knowledge_source/${encodeURIComponent(String(id))}/catalog${refresh ? '?refresh=true' : ''}`,
    ),

  /**
   * 单独更新 allowed(表/集合/索引白名单),不触动其它字段。
   * 后端会级联失效 schema/template/result 三层缓存。
   * 空数组 = 取消范围限制,默认查询全部。
   */
  patchAllowed: (
    id: number,
    allowed: { tables?: string[]; collections?: string[]; indices?: string[] },
  ): Promise<{ allowed: typeof allowed }> =>
    http.patch(`/knowledge_source/${encodeURIComponent(String(id))}/allowed`, allowed),
};
