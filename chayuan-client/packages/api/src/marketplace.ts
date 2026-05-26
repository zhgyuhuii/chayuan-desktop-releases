/**
 * 模型广场后台对接层。
 *
 * 后端端点(chayuan-server):
 *   GET /v1/models                                      → { data: RawModelItem[] }
 *   GET /img/model_logos/logos-manifest.json            → { lower_name: 'png'|'svg'|... }
 *   GET /img/model_logos/<name>.<ext>                   → 静态 logo
 *
 * 前端策略(详见 docs/contracts.md §2):
 *   - 推荐 / 厂商分组 / 管理 这 3 个是 UI 概念,后端无对应 endpoint;
 *   - 这里把 RawModelItem 富化为 MarketplaceModel(含 logoUrl / vendor / supportsXxx 推断 / 角标);
 *   - 推荐与厂商常量来自 ./marketplace.constants;不依赖后端目录字段。
 *
 * 实现搬自 chayuan-server-frontend/src/api/models.ts(2026-04-25, ADR-02);
 * 关键修改:fetch 走 PAL.net.fetch(已被 client.ts 用 request 包,这里也走 request)。
 */

import { models, type RawModelItem } from './endpoints';
import { request, getClientConfig } from './client';

export type ModelCapability =
  | 'streaming'
  | 'reasoning'
  | 'vision'
  | 'tools'
  | 'json'
  | 'embedding'
  | 'image-gen'
  | 'audio-tts'
  | 'audio-stt';

export interface MarketplaceModel {
  /** /v1/models 中的 id,作为 chat completion 时的 model 字段 */
  id: string;
  /** 展示名(默认 = id,可被 RECOMMENDED_MODELS 覆盖) */
  name: string;
  /** 后端 platform_name(如 "deepseek" "qwen" "kimi"),前端按它分组 */
  vendor: string;
  /** 简介,来自 RECOMMENDED_MODELS 配置;后端缺则空 */
  description?: string;
  /** logo URL,已合 manifest;失败时 undefined,UI 退回首字母 */
  logoUrl?: string;
  /** 推断的能力集合(基于 id 模糊匹配,后端没有标记) */
  capabilities: Set<ModelCapability>;
  /** 角标 */
  badge?: 'hot' | 'new';
  /** 是否在前端推荐位 */
  recommended?: boolean;
  /** 原始后端项,排错用 */
  raw: RawModelItem;
}

// ─────────────────────────────────────────────────────────────────────
// Logo manifest(单进程内缓存)
// ─────────────────────────────────────────────────────────────────────

let logoManifest: Record<string, string> | null = null;
let logoManifestLoading: Promise<Record<string, string>> | null = null;

/** 异步加载 logo manifest;失败回退空对象,不抛 */
export async function loadLogoManifest(): Promise<Record<string, string>> {
  if (logoManifest) return logoManifest;
  if (logoManifestLoading) return logoManifestLoading;
  logoManifestLoading = (async () => {
    try {
      logoManifest = await models.logosManifest();
    } catch {
      logoManifest = {};
    }
    return logoManifest;
  })();
  return logoManifestLoading;
}

const normalize = (s: string): string =>
  s.toLowerCase().replace(/[\s_/:-]/g, '');

/**
 * 用 platform_name / model.id 在 manifest 中匹配 logo。
 * 与 server-frontend resolveLogoUrl 等价(规则:精确同形 → 模糊包含 → undefined)。
 */
export function resolveLogoUrl(
  platformName?: string,
  modelId?: string,
): string | undefined {
  if (!logoManifest) return undefined;
  const baseURL = getClientConfig().baseURL || '';
  // 后端把静态目录挂在根路径 /img/,与 /api/v1 等并列;
  // 这里直接相对路径走代理,Vite/nginx 都已配 /img 反代。
  const prefix = baseURL.replace(/\/api\/?$/, '').replace(/\/$/, '');
  const keys = Object.keys(logoManifest);

  const candidates: string[] = [];
  if (platformName) candidates.push(platformName);
  if (modelId) {
    const mid = modelId.toLowerCase();
    const head = mid.split(/[-/_:]/)[0];
    if (head) candidates.push(head);
  }
  for (const c of candidates) {
    const n = normalize(c);
    const exact = keys.find((k) => normalize(k) === n);
    if (exact) {
      return `${prefix}/img/model_logos/${exact}.${logoManifest[exact]}`;
    }
    const loose = keys.find(
      (k) => !k.includes('_dark') && normalize(k).includes(n),
    );
    if (loose) {
      return `${prefix}/img/model_logos/${loose}.${logoManifest[loose]}`;
    }
  }
  return undefined;
}

// ─────────────────────────────────────────────────────────────────────
// 能力推断 —— 后端 /v1/models 不带能力字段,只能从 id 模糊判。
// 真要精确,需要后端补 capabilities[]。
// ─────────────────────────────────────────────────────────────────────

function inferCapabilities(id: string): Set<ModelCapability> {
  const lower = id.toLowerCase();
  const caps = new Set<ModelCapability>(['streaming']);
  if (/o1|reason|think|r1|opus|sonnet/.test(lower)) caps.add('reasoning');
  if (/vision|vl|gpt-4o|claude-3|gemini-pro/.test(lower)) caps.add('vision');
  if (/embedding|m3e|bge|text-embed/.test(lower)) caps.add('embedding');
  if (/tts|speech/.test(lower)) caps.add('audio-tts');
  if (/whisper|stt|transcrib/.test(lower)) caps.add('audio-stt');
  if (/dall|image-gen|sdxl|stable/.test(lower)) caps.add('image-gen');
  return caps;
}

// ─────────────────────────────────────────────────────────────────────
// 推荐 / 厂商常量(前端配置;后端无对应)
// ─────────────────────────────────────────────────────────────────────

/** 厂商显示名映射(i18n key);未命中时直接使用原始 platform_name */
export const VENDOR_LABELS: Record<string, string> = {
  deepseek: 'modelMarket.categories.deepseek',
  qwen: 'modelMarket.categories.qianwen',
  qianwen: 'modelMarket.categories.qianwen',
  ernie: 'modelMarket.categories.wenxin',
  wenxin: 'modelMarket.categories.wenxin',
  doubao: 'modelMarket.categories.douban',
  kimi: 'modelMarket.categories.kimi',
  moonshot: 'modelMarket.categories.kimi',
  minimax: 'modelMarket.categories.minimax',
};

/** 推荐位排序;命中的模型按本数组顺序置顶 */
export const RECOMMENDED_MODELS: ReadonlyArray<{
  /** 用 model id 的子串匹配(忽略大小写) */
  match: string;
  description: string;
  badge?: 'hot' | 'new';
}> = [
  { match: 'deepseek-v3', description: '混合推理架构,更高的思考效率', badge: 'hot' },
  { match: 'qwen3-coder', description: '卓越的代码和 Agent 能力,支持 358 种编程语言' },
  { match: 'ernie', description: '适用于各领域复杂任务场景,擅长文本和逻辑推理' },
  { match: 'qwen-max', description: '更新至 Qwen3,适用于高级编程辅助、多轮对话模拟' },
  { match: 'doubao', description: '字节豆包多模态对话' },
  { match: 'kimi', description: '长文本理解 / 长上下文检索' },
  { match: 'minimax', description: '中文综合性能优秀' },
];

// ─────────────────────────────────────────────────────────────────────
// 主入口
// ─────────────────────────────────────────────────────────────────────

/**
 * 列出**仅 embed 类型**的模型,供 RAG 嵌入选择器使用。
 *
 * 设计要点:
 * - 复用 RawModelItem,不引入新类型;UI 取 id / platform_name 做展示。
 * - 维度信息后端目前不返(/v1/models 没字段)。如需"按 KB 维度过滤"严格执行,
 *   依赖后端在 RawModelItem 上补 dim 字段后再启用过滤;当前先全量列出,
 *   并在 UI 用 tooltip 提醒用户"维度不一致会搜不到东西"。
 */
export async function listEmbeddingModels(): Promise<RawModelItem[]> {
  const list = await models.list();
  // 同 id 去重(同模型挂多平台时保留首个)
  const seen = new Set<string>();
  const out: RawModelItem[] = [];
  for (const it of list) {
    if (seen.has(it.id)) continue;
    // 优先看 model_type;老版本无此字段时退化用 id 启发匹配
    const mt = (it.model_type || '').toLowerCase();
    const isEmbed =
      mt === 'embed' ||
      mt === 'embedding' ||
      (mt === '' && /embedding|m3e|bge[-_]?m3|text-embed|gte/i.test(it.id));
    if (!isEmbed) continue;
    seen.add(it.id);
    out.push(it);
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

/**
 * 列模型,合 logo + 推荐 + 能力推断。
 * 同 id 不同 platform 去重(保留首个;platform 加入 vendor 字段)。
 *
 * 仅返回**对话用**(model_type='llm')模型;embed/rerank/tts/stt 等不该出现在
 * 模型广场和 chat 选择器里。后端 /v1/models 也支持 ?type=llm 过滤,但部分端点
 * 走过滤、部分要全集(例如 catalog 渲染),所以前端再过一次以保险。
 * 老版本后端不返回 model_type 字段时按"未知"放行(向后兼容)。
 */
export async function listMarketplaceModels(): Promise<MarketplaceModel[]> {
  const [list] = await Promise.all([models.list(), loadLogoManifest()]);
  const seen = new Map<string, MarketplaceModel>();

  for (const it of list) {
    if (seen.has(it.id)) continue;
    // 过滤非对话类模型(embed / rerank / image2text / tts / stt 等)
    if (it.model_type && it.model_type !== 'llm') continue;
    const vendor = (it.platform_name || it.owned_by || 'unknown').toLowerCase();
    const reco = RECOMMENDED_MODELS.find((r) =>
      it.id.toLowerCase().includes(r.match.toLowerCase()),
    );
    const m: MarketplaceModel = {
      id: it.id,
      name: it.id,
      vendor,
      description: reco?.description,
      logoUrl: resolveLogoUrl(it.platform_name, it.id),
      capabilities: inferCapabilities(it.id),
      raw: it,
      ...(reco?.badge ? { badge: reco.badge } : {}),
      ...(reco ? { recommended: true } : {}),
    };
    seen.set(it.id, m);
  }

  // 推荐前置;否则按 id 字母序
  return [...seen.values()].sort((a, b) => {
    if (a.recommended && !b.recommended) return -1;
    if (!a.recommended && b.recommended) return 1;
    if (a.recommended && b.recommended) {
      const ia = RECOMMENDED_MODELS.findIndex((r) =>
        a.id.toLowerCase().includes(r.match.toLowerCase()),
      );
      const ib = RECOMMENDED_MODELS.findIndex((r) =>
        b.id.toLowerCase().includes(r.match.toLowerCase()),
      );
      if (ia !== ib) return ia - ib;
    }
    return a.id.localeCompare(b.id);
  });
}

/** 按 vendor 分组;Map 保持插入顺序 */
export function groupByVendor(
  list: MarketplaceModel[],
): Map<string, MarketplaceModel[]> {
  const map = new Map<string, MarketplaceModel[]>();
  for (const m of list) {
    const arr = map.get(m.vendor) ?? [];
    arr.push(m);
    map.set(m.vendor, arr);
  }
  return map;
}

/** 测试钩子:重置 logo manifest 缓存 */
export function __resetLogoManifestForTest(): void {
  logoManifest = null;
  logoManifestLoading = null;
}

// 走 typed re-export,避免 client 直接 import 老 RawModelItem
void request;
