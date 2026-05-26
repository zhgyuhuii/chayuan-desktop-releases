/**
 * AI 生成笔记摘要 + 要点。
 *
 * 输入:Tiptap JSON 内容(walk 出纯文本)
 * 输出:markdown 字符串(含 "## 摘要" + "## 要点" 两段)
 *
 * 默认 chat 模型:aiPlatform.recommended.list().default_for_capability.chat。
 * 没默认 / API 不通 → 返 error,UI 显示"未配置 AI 模型"。
 *
 * 非流式 — 用 aiPlatform.chat.complete(...)。一般 ≤ 10s 出结果,延迟可接受;
 * 后续若需流式可切到 transport.chat() 走 SSE。
 */
import * as React from 'react';
import { aiPlatform } from '@chayuan/api';

const SYSTEM_PROMPT = `你是一个专业的文档摘要助手。根据用户提供的笔记内容,严格按以下 Markdown 格式输出:

## 摘要
<1-2 句话简明概述,不超过 80 字>

## 要点
- <要点 1,≤ 30 字>
- <要点 2,≤ 30 字>
- <要点 3,≤ 30 字>
- (可选 要点 4-5)

约束:
- 直接给出格式化结果,**不要**包含"好的"/"以下是"等开场白
- 用简体中文(除非用户笔记主要语种是其它语言,跟随之)
- 要点数量 3-5 个,过短文档可少
- 不要把摘要和要点混在一起`;

/** 走 Tiptap JSON,只取 type==='text' 节点的 text 字段拼成纯文本。 */
function extractPlainText(node: unknown): string {
  if (!node || typeof node !== 'object') return '';
  const obj = node as Record<string, unknown>;
  if (obj.type === 'text' && typeof obj.text === 'string') return obj.text;
  if (Array.isArray(obj.content)) {
    const children = obj.content as unknown[];
    const parts = children.map((c) => extractPlainText(c));
    const isBlock = (n: unknown): boolean => {
      if (!n || typeof n !== 'object') return false;
      const t = (n as Record<string, unknown>).type;
      return typeof t === 'string'
        && t !== 'text' && t !== 'hardBreak';
    };
    return parts
      .map((p, i) => p + (isBlock(children[i]) ? '\n' : ''))
      .join('')
      .replace(/\n{3,}/g, '\n\n');
  }
  return '';
}

export interface UseNoteSummaryResult {
  summary: string;
  loading: boolean;
  error: string | null;
  /** 触发生成,会覆盖现有 summary。caller 拿到结果可继续 persist。 */
  generate: (content: unknown, opts?: { title?: string }) => Promise<string | null>;
  setSummary: (s: string) => void;
  clear: () => void;
}

export function useNoteSummary(initialSummary?: string): UseNoteSummaryResult {
  const [summary, setSummary] = React.useState<string>(initialSummary ?? '');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const generate = React.useCallback(async (
    content: unknown, opts: { title?: string } = {},
  ): Promise<string | null> => {
    if (loading) return null;
    setError(null);
    const text = extractPlainText(content).trim();
    if (!text || text.length < 20) {
      setError('笔记内容太短(< 20 字),先写点东西再让 AI 摘要');
      return null;
    }
    setLoading(true);
    try {
      // 取**实际注册**的 chat 模型 — recommended.list 返回的是"推荐默认"可能没装,
      // models.list 是 MODEL_PLATFORMS 里真在跑的(本地 bundled + 远端 deepseek/openai 等)。
      // 先按推荐默认匹配实际可用的;匹配不上回退到列表第一个。
      let availableChats: { id: string; isDefault?: boolean }[] = [];
      try {
        const chats = await aiPlatform.models.list({ capability: 'chat' });
        availableChats = chats.map((m) => ({ id: m.id }));
      } catch (e) {
        console.debug('[summary] 取 chat 模型列表失败:', e);
      }
      if (availableChats.length === 0) {
        setError(
          '后端未注册任何 chat 模型。请到「设置 → AI 平台」启用一个本地或远端的 LLM 模型',
        );
        return null;
      }
      // 优先用 recommended.default_for_capability.chat,且该 id 在 availableChats 里
      let modelId = availableChats[0]!.id;
      try {
        const r = await aiPlatform.recommended.list('chat');
        const preferred = r.default_for_capability?.chat;
        if (preferred && availableChats.some((m) => m.id === preferred)) {
          modelId = preferred;
        }
      } catch (e) {
        console.debug('[summary] 取推荐默认失败,用 list 第一个:', e);
      }
      const titlePrefix = opts.title ? `标题: ${opts.title}\n\n` : '';
      const userMsg = `${titlePrefix}笔记内容:\n${text.slice(0, 8000)}`;  // 限 8K 防超 context
      const resp = await aiPlatform.chat.complete({
        model: modelId,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: userMsg },
        ],
        temperature: 0.3,  // 摘要任务,稳定性优先
        max_tokens: 800,
      });
      const out = resp.choices?.[0]?.message?.content?.trim() ?? '';
      if (!out) {
        setError('AI 返回了空内容(模型可能未就绪)');
        return null;
      }
      setSummary(out);
      return out;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`AI 摘要生成失败: ${msg}`);
      return null;
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const clear = React.useCallback(() => {
    setSummary('');
    setError(null);
  }, []);

  return { summary, loading, error, generate, setSummary, clear };
}
