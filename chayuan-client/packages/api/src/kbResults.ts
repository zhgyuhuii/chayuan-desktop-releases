/**
 * 统一知识检索结果模型 — chayuan-client 所有 KB 搜索结果展示的真源。
 *
 * 三个真实来源各自的字段不同(详见各 adapter):
 *   - chat 流的 `ChatCitation[]` (来自 SSE `citation` 事件)
 *   - knowledge_universe ask 的 `AskBlock[]` (per-ku_id 一块)
 *   - /api/v1/kb-query/search 的 `KbQueryItem.blocks[]`
 *
 * UI 组件 `<KbResultsView>` 只消费这套统一类型,渲染分三级:
 *   Source(KB / 站外来源) → Group(文件 / 集合 / 表 / 站点) → Snippet(段 / 行 / SQL / 向量元数据)
 */

import type { AskBlock, KbQueryItem } from './kbUniverse';

export type RetrievalKind = 'document' | 'structured' | 'vector' | 'image' | 'web' | 'file';

/** 顶层一条分隔条对应一份 RetrievalSource[];每个 source 是一个 KB / 一个外链桶。 */
export interface RetrievalSource {
  /** ku_id / kb_id;无明确归属(如裸 web 链接)给 'web' / 'file' / 'unknown'。 */
  id: string;
  label: string;
  kind: RetrievalKind;
  subKind?: string;
  ok: boolean;
  error?: string;
  /** 顶部信息条:未命中诊断 / 改写策略 / 索引状态等;由 adapter 拼成短句。 */
  diagnostic?: string;
  groups: RetrievalGroup[];
}

/** Source 内的二级组:一份文件 / 一个集合 / 一张表 / 一个站点。 */
export interface RetrievalGroup {
  /** 稳定 key,适合作 React key + 去重。 */
  key: string;
  label: string;
  kind: RetrievalKind;
  subKind?: string;
  /** document 类型:命中片段总数(可能 > snippets.length 因为只展示前 N)。 */
  count?: number;
  /** 最佳分数;来自 hit.score / rerank_score。 */
  bestScore?: number;
  /** 1-based 引用编号;LLM 在答案里写「[出处 N]」用的就是这个,排序用最小值。 */
  citeIndex?: number;
  /** 文档类型:开下载 / 预览面板的参数。 */
  preview?: { kbName: string; fileName: string };
  download?: { kbName: string; fileName: string };
  /** 外链 / web 来源:直接用 url。 */
  url?: string;
  /** 结构化:展开时顶部展示的 SQL。 */
  sqlText?: string;
  /** 结构化:列名。 */
  columns?: string[];
  /** 结构化:展开时直接渲染的行(已剥离 __truncated__ 哨兵)。 */
  rows?: Array<Record<string, unknown>>;
  /** 结构化:被截断时的提示文本。 */
  truncationHint?: string;
  /** 结构化:summary / 自由文本(供"概述"卡)。 */
  summary?: string;
  snippets: RetrievalSnippet[];
}

/** Snippet 是最细粒度的命中:文档段、向量 hit、图像 caption、SQL 行也降格成单行 snippet。 */
export interface RetrievalSnippet {
  text: string;
  page?: number | null;
  chunkIndex?: number | null;
  retrievalPath?: string;
  score?: number;
  /** 向量来源:collection 名 / vector id。 */
  collection?: string;
  vectorId?: string;
  /** 图像缩略 / 文件预览。 */
  imageUrl?: string;
  metadata?: Record<string, unknown>;
}

/**
 * diagnostic 在服务端有两种形态:短句字符串(老路径) 或 对象(新路径包含 retrieval_model_policy /
 * index_status / warning / elapsed_ms 等)。直接塞进 JSX 渲染会触发
 * "Objects are not valid as a React child"。统一规整成 string 或 undefined。
 */
function stringifyDiagnostic(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === 'string') return value.trim() || undefined;
  if (typeof value !== 'object') return String(value);
  try {
    const obj = value as Record<string, unknown>;
    const parts: string[] = [];
    if (obj.warning) parts.push(`warning: ${String(obj.warning)}`);
    if (obj.index_status) parts.push(`index: ${String(obj.index_status)}`);
    if (obj.retrieval_model_policy) parts.push(`policy: ${String(obj.retrieval_model_policy)}`);
    if (obj.elapsed_ms != null) parts.push(`${String(obj.elapsed_ms)}ms`);
    if (parts.length) return parts.join(' · ');
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ── ChatCitation[] (chat SSE 事件) ─────────────────────────────────────────

interface ChatCitationLike {
  title?: string;
  url?: string;
  snippet?: string;
  score?: number;
  kb_name?: string;
  file_name?: string;
  source_kind?: string;
  cite_index?: number;
  chunk_count?: number;
  chunks?: Array<{
    content?: string;
    snippet?: string;
    score?: number;
    page?: number | null;
    chunk_index?: number | null;
    retrieval_path?: string;
    title?: string;
  }>;
  /** image / structured / vector 类来源的扩展信息(后端 _block_to_*_sources 透传)。 */
  meta?: {
    source?: string;
    kind?: string;
    image_id?: string;
    preview_url?: string;
    download_url?: string;
    [k: string]: unknown;
  };
}

/**
 * 把 ChatCitation[] 摊平成统一来源:
 *   - 同一 kb_name 的 citation 聚合成一个 Source
 *   - 同一 (kb_name, file_name) 聚合成一个 Group
 *   - 没有 kb_name 但有 url 的归到 'web' source
 */
export function chatCitationsToSources(citations: ChatCitationLike[]): RetrievalSource[] {
  const byKb = new Map<string, RetrievalSource>();
  const webBucket: RetrievalGroup[] = [];

  for (const c of citations || []) {
    const kbName = (c.kb_name || '').trim();
    const fileName = (c.file_name || '').trim();
    const sourceKind = (c.source_kind || '').trim();
    // 图像类来源:与文档同形地聚合到一个"图库"Source,每张图作为 Group(自带
    // imageUrl + downloadUrl,供顶部附件折叠条渲染缩略图 + 下载按钮)。
    if (sourceKind === 'image' && kbName) {
      const sourceId = `img:${kbName}`;
      let src = byKb.get(sourceId);
      if (!src) {
        src = { id: sourceId, label: kbName, kind: 'image', ok: true, groups: [] };
        byKb.set(sourceId, src);
      }
      const meta = c.meta || {};
      const imageId = (meta.image_id as string) || fileName || c.title || `image-${src.groups.length}`;
      const previewUrl = (meta.preview_url as string) || c.url || '';
      const downloadUrl = (meta.download_url as string) || previewUrl;
      const groupKey = `${kbName}/${imageId}`;
      let group = src.groups.find((g) => g.key === groupKey);
      if (!group) {
        group = {
          key: groupKey,
          label: c.title || imageId,
          kind: 'image',
          count: 1,
          snippets: [{
            text: (c.snippet || c.title || '').trim() || imageId,
            score: c.score,
            imageUrl: previewUrl,
            metadata: { download_url: downloadUrl, image_id: imageId },
          }],
          url: downloadUrl,  // 顶部 chip 点击 → 下载原图
          citeIndex: c.cite_index,
          bestScore: c.score,
        };
        src.groups.push(group);
      } else {
        const score = c.score ?? 0;
        if (score > (group.bestScore || 0)) group.bestScore = score;
        if (c.cite_index && (!group.citeIndex || c.cite_index < group.citeIndex)) {
          group.citeIndex = c.cite_index;
        }
      }
      continue;
    }
    if (kbName) {
      const sourceId = `doc:${kbName}`;
      let src = byKb.get(sourceId);
      if (!src) {
        src = { id: sourceId, label: kbName, kind: 'document', ok: true, groups: [] };
        byKb.set(sourceId, src);
      }
      const groupKey = fileName ? `${kbName}/${fileName}` : `${kbName}/__virtual__`;
      let group = src.groups.find((g) => g.key === groupKey);
      if (!group) {
        group = {
          key: groupKey,
          label: fileName || kbName,
          kind: 'document',
          count: 0,
          snippets: [],
          preview: fileName ? { kbName, fileName } : undefined,
          download: fileName ? { kbName, fileName } : undefined,
        };
        src.groups.push(group);
      }
      const incomingChunks = c.chunks?.length
        ? c.chunks
        : c.snippet
          ? [{ snippet: c.snippet, score: c.score }]
          : [];
      for (const chunk of incomingChunks) {
        const text = (chunk.snippet || chunk.content || '').trim();
        if (!text) continue;
        group.snippets.push({
          text,
          page: chunk.page ?? null,
          chunkIndex: chunk.chunk_index ?? null,
          retrievalPath: chunk.retrieval_path,
          score: chunk.score ?? c.score,
        });
      }
      const inc = c.chunk_count ?? c.chunks?.length ?? 1;
      group.count = (group.count || 0) + inc;
      const score = c.score ?? 0;
      if (score > (group.bestScore || 0)) group.bestScore = score;
      if (c.cite_index && (!group.citeIndex || c.cite_index < group.citeIndex)) {
        group.citeIndex = c.cite_index;
      }
    } else if (c.url || c.title) {
      webBucket.push({
        key: c.url || c.title || `web-${webBucket.length}`,
        label: c.title || c.url || '外部来源',
        kind: 'web',
        url: c.url,
        snippets: c.snippet ? [{ text: c.snippet, score: c.score }] : [],
        bestScore: c.score,
        citeIndex: c.cite_index,
      });
    }
  }

  const sources: RetrievalSource[] = [];
  for (const src of byKb.values()) {
    src.groups.sort((a, b) => (a.citeIndex || 999) - (b.citeIndex || 999));
    sources.push(src);
  }
  if (webBucket.length) {
    sources.push({ id: 'web', label: '外部来源', kind: 'web', ok: true, groups: webBucket });
  }
  return sources;
}

// ── AskBlock[] (knowledge_universe ask) ───────────────────────────────────

/**
 * 把 universe ask 的 AskBlock[](每个 block = 一个 ku_id)摊到统一模型。
 * documentLabelByKuId 用于把 ku_id 显示成知识库名(KbAskResultPanel 已有 itemsByKuId)。
 */
export function askBlocksToSources(
  blocks: AskBlock[],
  labelByKuId: Map<string, string> = new Map(),
): RetrievalSource[] {
  const out: RetrievalSource[] = [];
  for (const block of blocks || []) {
    const kuId = block.ku_id;
    const label = labelByKuId.get(kuId) || kuId;
    const kind = (block.kind || 'document') as RetrievalKind;
    const source: RetrievalSource = {
      id: kuId,
      label,
      kind,
      ok: !!block.ok,
      error: block.error,
      groups: [],
    };
    if (!block.ok) {
      out.push(source);
      continue;
    }
    if (block.kind === 'document') {
      // 服务端有时把 diagnostic 写成 dict ({retrieval_model_policy,index_status,...}),
      // 有时写成短句字符串。统一规整成 string,避免直接塞进 JSX 触发 React 报错。
      source.diagnostic = stringifyDiagnostic(block.diagnostic);
      const byFile = new Map<string, RetrievalGroup>();
      for (const hit of block.results || []) {
        const fileName = hit.file_name || hit.source || '未知文件';
        const kbName = hit.kb_name || labelByKuId.get(kuId) || kuId.replace(/^doc:/, '');
        const groupKey = `${kbName}/${fileName}`;
        let group = byFile.get(groupKey);
        if (!group) {
          group = {
            key: groupKey,
            label: fileName,
            kind: 'document',
            count: 0,
            snippets: [],
            preview: { kbName, fileName },
            download: { kbName, fileName },
          };
          byFile.set(groupKey, group);
        }
        group.snippets.push({
          text: (hit.snippet || hit.content || '').trim(),
          page: hit.page,
          chunkIndex: hit.chunk_index,
          retrievalPath: hit.retrieval_path,
          score: hit.rerank_score ?? hit.score,
        });
        group.count = (group.count || 0) + 1;
        const score = hit.rerank_score ?? hit.score ?? 0;
        if (score > (group.bestScore || 0)) group.bestScore = score;
      }
      source.groups = [...byFile.values()].sort((a, b) => (b.bestScore || 0) - (a.bestScore || 0));
    } else if (block.kind === 'structured') {
      const rawRows = block.rows ?? [];
      let rows = rawRows;
      let truncationHint: string | undefined;
      const last = rows[rows.length - 1];
      if (last && '__truncated__' in last) {
        truncationHint = String(last.__truncated__ ?? '');
        rows = rows.slice(0, -1);
      }
      const tables = (block.scope?.tables || []).filter(Boolean);
      const groupKey = tables.length ? tables.join(',') : kuId;
      const group: RetrievalGroup = {
        key: groupKey,
        label: tables.length ? tables.join(', ') : (label || '结构化查询'),
        kind: 'structured',
        sqlText: block.sql,
        rows: rows as Array<Record<string, unknown>>,
        columns: block.columns,
        truncationHint,
        summary: block.summary,
        snippets: [],
      };
      if (!group.summary && block.text) {
        group.summary = block.text.replace(/执行的sql:'[\s\S]+?'/, '').trim() || undefined;
      }
      source.groups = [group];
    } else if (block.kind === 'vector') {
      const hits = block.hits || [];
      const group: RetrievalGroup = {
        key: kuId,
        label: label || '向量库',
        kind: 'vector',
        count: hits.length,
        snippets: hits.map((h, i) => ({
          text: (h.content || '').trim() || `vector#${i + 1}`,
          score: h.score,
          metadata: h.metadata,
          collection: (h.metadata?.collection as string) || undefined,
          vectorId: (h.metadata?.id as string) || (h.metadata?.vector_id as string) || undefined,
        })),
      };
      source.groups = [group];
    } else if (block.kind === 'image') {
      const hits = block.results || [];
      source.groups = hits.map((h, i) => ({
        key: h.id || h.path || `img-${i}`,
        label: h.title || h.caption || h.path || `图像 #${i + 1}`,
        kind: 'image',
        bestScore: h.score,
        snippets: [{
          text: h.caption || h.title || '',
          score: h.score,
          imageUrl: h.preview_url || h.path,
        }],
      }));
    }
    out.push(source);
  }
  return out;
}

// ── KbQueryItem (/api/v1/kb-query/search) ─────────────────────────────────

/**
 * 把统一查询接口的 KbQueryItem.blocks 摊成 RetrievalSource[]。
 * 与 askBlocksToSources 不同的是结构化 / 向量的 hit 都在 results[] 里 (由 hit.source_type 区分)。
 */
export function kbQueryItemToSources(item: KbQueryItem | null | undefined): RetrievalSource[] {
  if (!item) return [];
  const out: RetrievalSource[] = [];
  for (const block of item.blocks || []) {
    const kuId = block.kb_id;
    const kind = (block.kind || 'document') as RetrievalKind;
    const source: RetrievalSource = {
      id: kuId,
      label: block.display_name || block.name || kuId,
      kind,
      subKind: block.sub_kind,
      ok: !!block.ok,
      error: block.error?.message,
      groups: [],
    };
    const hits = block.results || [];
    if (kind === 'document') {
      const byFile = new Map<string, RetrievalGroup>();
      for (const h of hits) {
        const meta = (h.metadata || {}) as Record<string, unknown>;
        const fileName = String((meta.file_name as string) || (h.citation?.file_name as string) || (meta.source as string) || '未知文件');
        const kbName = String((meta.kb_name as string) || block.name || kuId.replace(/^doc:/, ''));
        const groupKey = `${kbName}/${fileName}`;
        let group = byFile.get(groupKey);
        if (!group) {
          group = {
            key: groupKey,
            label: fileName,
            kind: 'document',
            count: 0,
            snippets: [],
            preview: { kbName, fileName },
            download: { kbName, fileName },
          };
          byFile.set(groupKey, group);
        }
        group.snippets.push({
          text: (h.snippet || h.text || '').trim(),
          page: (meta.page as number) ?? null,
          chunkIndex: (meta.chunk_index as number) ?? null,
          retrievalPath: h.retrieval_path,
          score: h.score,
        });
        group.count = (group.count || 0) + 1;
        if ((h.score || 0) > (group.bestScore || 0)) group.bestScore = h.score;
      }
      source.groups = [...byFile.values()].sort((a, b) => (b.bestScore || 0) - (a.bestScore || 0));
    } else if (kind === 'structured') {
      // 一个 block 仅一个 SQL 结果;sql/columns/rows 都从首个 hit 读
      const first = hits[0];
      const meta = (first?.metadata || {}) as Record<string, unknown>;
      const sql = first?.sql || (meta.sql as string) || (block.diagnostic?.sql as string);
      const columns = first?.columns || (meta.columns as string[]);
      const rawRows = first?.rows || (meta.rows as Array<Record<string, unknown>>) || [];
      let rows = rawRows;
      let truncationHint: string | undefined;
      const last = rows[rows.length - 1];
      if (last && '__truncated__' in last) {
        truncationHint = String(last.__truncated__ ?? '');
        rows = rows.slice(0, -1);
      }
      source.groups = [{
        key: kuId,
        label: source.label,
        kind: 'structured',
        sqlText: sql,
        columns,
        rows,
        truncationHint,
        summary: first?.text,
        snippets: [],
      }];
    } else if (kind === 'vector' || kind === 'image') {
      const group: RetrievalGroup = {
        key: kuId,
        label: source.label,
        kind: kind as RetrievalKind,
        count: hits.length,
        snippets: hits.map((h, i) => ({
          text: (h.text || h.snippet || '').trim() || `${kind}#${i + 1}`,
          score: h.score,
          retrievalPath: h.retrieval_path,
          metadata: h.metadata,
          collection: (h.metadata?.collection as string) || undefined,
          vectorId: (h.metadata?.id as string) || (h.metadata?.vector_id as string) || undefined,
        })),
      };
      source.groups = [group];
    }
    out.push(source);
  }
  return out;
}

// ── 汇总信息(给 UI 顶部条用) ────────────────────────────────────────────

export interface RetrievalSummary {
  sourceCount: number;
  groupCount: number;
  snippetCount: number;
  topCiteIndex: number;
}

export function summarizeSources(sources: RetrievalSource[]): RetrievalSummary {
  let groupCount = 0;
  let snippetCount = 0;
  let topCiteIndex = 0;
  for (const s of sources) {
    for (const g of s.groups) {
      groupCount += 1;
      snippetCount += Math.max(g.count || 0, g.snippets.length);
      if (g.citeIndex && (!topCiteIndex || g.citeIndex < topCiteIndex)) topCiteIndex = g.citeIndex;
    }
  }
  return { sourceCount: sources.length, groupCount, snippetCount, topCiteIndex };
}
