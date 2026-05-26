/**
 * 严格 W3C SSE 解析 + 协议归一。
 *
 * 设计：
 * - 纯函数 / 异步 generator；无平台依赖；可在 main thread / worker 共用。
 * - parseSSEFrames：处理 event:/id:/data:/retry:/CRLF/keep-alive 注释/多行 data: 累积。
 * - parseStructuredSSE：把后端三种风格（OpenAI 兼容、AG-UI/ChatGraph 显式 event、最末 [DONE]）
 *   归一到 StructuredSSEEvent。
 * - 内部不抛业务异常，所有错误都转 `{ type: 'error' }`，由调用方决定 UI 表现。
 */

import type { StructuredSSEEvent } from './types';

interface RawSSERecord {
  event?: string;
  id?: string;
  data: string;
  retry?: number;
}

export async function* parseSSEFrames(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<RawSSERecord> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let evt: string | undefined;
  let id: string | undefined;
  let retry: number | undefined;
  let dataLines: string[] = [];

  const flush = (): RawSSERecord | null => {
    if (!dataLines.length && evt === undefined && id === undefined) return null;
    const rec: RawSSERecord = { event: evt, id, retry, data: dataLines.join('\n') };
    evt = undefined;
    id = undefined;
    retry = undefined;
    dataLines = [];
    return rec;
  };

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let nlIdx: number;
      while ((nlIdx = buffer.indexOf('\n')) !== -1) {
        const rawLine = buffer.slice(0, nlIdx);
        buffer = buffer.slice(nlIdx + 1);
        const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;

        if (line === '') {
          const rec = flush();
          if (rec) yield rec;
          continue;
        }
        if (line.startsWith(':')) continue; // keep-alive comment

        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        let val = colon === -1 ? '' : line.slice(colon + 1);
        if (val.startsWith(' ')) val = val.slice(1);

        switch (field) {
          case 'event':
            evt = val;
            break;
          case 'id':
            id = val;
            break;
          case 'retry': {
            const n = Number.parseInt(val, 10);
            if (!Number.isNaN(n)) retry = n;
            break;
          }
          case 'data':
            dataLines.push(val);
            break;
        }
      }
    }
    const last = flush();
    if (last) yield last;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

interface OpenAIChunk {
  id?: string;
  choices?: Array<{
    delta?: {
      content?: string;
      reasoning_content?: string;
      tool_calls?: Array<{
        id?: string;
        function?: { name?: string; arguments?: string };
      }>;
    };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost_usd?: number;
  };
  error?: { message?: string; code?: number };
}

export async function* parseStructuredSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<StructuredSSEEvent> {
  for await (const frame of parseSSEFrames(body, signal)) {
    const raw = frame.data;
    if (!raw) continue;
    if (raw === '[DONE]') {
      yield { type: 'done' };
      return;
    }

    let parsed: unknown = null;
    try {
      parsed = JSON.parse(raw);
    } catch {
      continue;
    }

    // ── AG-UI / ChatGraph 显式 event:
    if (frame.event && frame.event !== 'message') {
      const yielded = handleNamedEvent(frame.event, parsed);
      if (yielded) {
        for (const ev of yielded) yield ev;
        if (yielded.some((e) => e.type === 'done' || e.type === 'error')) return;
        continue;
      }
    }

    // ── OpenAI 兼容 fallback
    const evt = parsed as OpenAIChunk;
    if (evt.error?.message) {
      yield { type: 'error', message: evt.error.message, code: evt.error.code };
      return;
    }

    if (evt.usage) {
      yield {
        type: 'usage',
        promptTokens: evt.usage.prompt_tokens,
        completionTokens: evt.usage.completion_tokens,
        totalTokens: evt.usage.total_tokens,
        costUsd: evt.usage.cost_usd,
      };
    }

    const choice = evt.choices?.[0];
    if (!choice) continue;

    const delta = choice.delta?.content;
    const reasoning = choice.delta?.reasoning_content;
    if (delta || reasoning) {
      yield { type: 'token', delta: delta ?? '', reasoning, messageId: evt.id };
    }
    if (choice.delta?.tool_calls?.length) {
      for (const tc of choice.delta.tool_calls) {
        yield {
          type: 'tool_call',
          id: tc.id,
          name: tc.function?.name,
          arguments: tc.function?.arguments,
          status: 'delta',
        };
      }
    }
    if (choice.finish_reason) {
      yield { type: 'done', finishReason: choice.finish_reason };
      return;
    }
  }
}

function handleNamedEvent(name: string, parsed: unknown): StructuredSSEEvent[] | null {
  const p = (parsed ?? {}) as Record<string, unknown>;
  switch (name) {
    case 'token':
      return [
        {
          type: 'token',
          delta: String(p.delta ?? p.content ?? ''),
          reasoning: typeof p.reasoning === 'string' ? p.reasoning : undefined,
          messageId: typeof p.messageId === 'string' ? p.messageId : undefined,
        },
      ];
    case 'tool_call':
    case 'tool_call_start':
    case 'tool_call_delta':
    case 'tool_call_end':
      return [
        {
          type: 'tool_call',
          id: typeof p.id === 'string' ? p.id : undefined,
          name: typeof p.name === 'string' ? p.name : undefined,
          arguments: typeof p.arguments === 'string' ? p.arguments : undefined,
          result: p.result,
          status:
            name === 'tool_call_start'
              ? 'start'
              : name === 'tool_call_end'
                ? 'end'
                : name === 'tool_call_delta'
                  ? 'delta'
                  : undefined,
        },
      ];
    case 'citation':
    case 'citations':
    case 'sources':
      // server 在 KB / file / web 模式下都会发 `event: sources data: {sources, chunks}`,
      // 我们统一映射成一个 citation 事件。sources 数组直接透传 — 客户端按
      // `kb_name + file_name` 是否非空决定渲染成"可预览文件 chip"还是普通链接。
      {
        const chunks = Array.isArray(p.chunks) ? (p.chunks as Array<Record<string, unknown>>) : [];
        const normalizedChunks = chunks.map((chunk) => {
          const citation = (chunk.citation && typeof chunk.citation === 'object')
            ? (chunk.citation as Record<string, unknown>)
            : {};
          const meta = (citation.meta && typeof citation.meta === 'object')
            ? (citation.meta as Record<string, unknown>)
            : {};
          const source = String(meta.file_name ?? meta.source ?? citation.title ?? '');
          return {
            content: String(chunk.content ?? ''),
            snippet: String(chunk.snippet ?? chunk.content ?? ''),
            score: typeof chunk.score === 'number' ? chunk.score : Number(chunk.score ?? 0) || undefined,
            page: meta.page == null || meta.page === '' ? null : Number(meta.page),
            chunk_index: meta.chunk_index == null || meta.chunk_index === '' ? null : Number(meta.chunk_index),
            retrieval_path: typeof meta.retrieval_path === 'string' ? meta.retrieval_path : undefined,
            title: source,
            file_name: source.split(/[\\/]/).pop() || source,
          };
        });
        const sources = Array.isArray(p.sources)
          ? (p.sources as Array<{
              title?: string;
              url?: string;
              snippet?: string;
              score?: number;
              kb_name?: string;
              file_name?: string;
              source_kind?: string;
              cite_index?: number;
              chunk_count?: number;
            }>)
          : [];
        const enriched = sources.map((source) => {
          const fileName = source.file_name || source.title || '';
          const matched = fileName
            ? normalizedChunks.filter((chunk) => chunk.file_name === fileName || chunk.title === fileName)
            : [];
          return {
            ...source,
            chunks: matched.map(({ file_name: _fileName, ...chunk }) => chunk),
          };
        });
        return [
          ...(((p.mounted && typeof p.mounted === 'object') || Array.isArray(p.mounted_sources))
            ? [{
                type: 'mounted' as const,
                summary: (p.mounted && typeof p.mounted === 'object'
                  ? p.mounted
                  : {}) as Record<string, unknown>,
                sources: Array.isArray(p.mounted_sources)
                  ? (p.mounted_sources as Array<Record<string, unknown>>)
                  : undefined,
              }]
            : []),
          {
            type: 'citation',
            sources: enriched,
          },
        ];
      }
    case 'interrupt':
      return [{ type: 'interrupt', reason: typeof p.reason === 'string' ? p.reason : undefined, payload: parsed }];
    case 'mounted':
      return [{
        type: 'mounted',
        summary: (p.mounted && typeof p.mounted === 'object'
          ? p.mounted
          : p) as Record<string, unknown>,
        sources: Array.isArray(p.mounted_sources)
          ? (p.mounted_sources as Array<Record<string, unknown>>)
          : Array.isArray(p.sources)
            ? (p.sources as Array<Record<string, unknown>>)
            : undefined,
      }];
    case 'final':
      if (p.mounted && typeof p.mounted === 'object') {
        return [{
          type: 'mounted',
          summary: p.mounted as Record<string, unknown>,
          sources: Array.isArray(p.mounted_sources)
            ? (p.mounted_sources as Array<Record<string, unknown>>)
            : undefined,
        }];
      }
      return null;
    case 'usage':
      return [
        {
          type: 'usage',
          promptTokens: Number(p.prompt_tokens ?? p.promptTokens) || undefined,
          completionTokens: Number(p.completion_tokens ?? p.completionTokens) || undefined,
          totalTokens: Number(p.total_tokens ?? p.totalTokens) || undefined,
          costUsd: Number(p.cost_usd ?? p.costUsd) || undefined,
        },
      ];
    case 'error':
      return [{ type: 'error', message: String(p.message ?? 'Unknown error'), code: typeof p.code === 'number' ? p.code : undefined }];
    case 'done':
      return [{ type: 'done', finishReason: typeof p.finish_reason === 'string' ? p.finish_reason : undefined }];
    default:
      return null;
  }
}
