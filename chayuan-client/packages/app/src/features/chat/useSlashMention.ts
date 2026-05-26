/**
 * Composer 的 / 与 @ 触发器解析。
 *
 * - 监听 textarea 的 caret + value，判断光标前是否处于活跃 token；
 * - 触发 token 形如 `/web` 或 `@kb_xxx` —— 命中即返回 active suggestion；
 * - 业务侧只需把返回结构渲染成 popover；选中后调 `replace(token, replacement)`。
 *
 * 设计：纯函数 + hook 接口；不耦合 UI。
 */

import * as React from 'react';
import { useTools, useMcps, useKbs } from '../catalog/useCatalog';
import {
  useComposerScopedStore, useComposerS,
} from '../../store/composer';

const SLASH_COMMANDS = [
  { id: 'web', label: '/web', desc: '强制走联网搜索' },
  { id: 'think', label: '/think', desc: '开启深度思考' },
  { id: 'code', label: '/code', desc: '提示生成代码 artifact' },
  { id: 'clear', label: '/clear', desc: '清除当前对话' },
  { id: 'save', label: '/save', desc: '保存为 markdown' },
] as const;

export type SlashId = (typeof SLASH_COMMANDS)[number]['id'];

export interface SuggestionState {
  kind: 'slash' | 'mention' | null;
  /** 触发位置（@ 或 / 的 caret 索引），用于替换 */
  start: number;
  /** 当前光标位置 */
  end: number;
  /** 已输入的过滤词（不含触发字符） */
  query: string;
  items: Array<{ id: string; label: string; desc?: string; group?: string }>;
}

const EMPTY: SuggestionState = { kind: null, start: 0, end: 0, query: '', items: [] };

/**
 * 解析当前 caret 位置对应的 active token；返回 null = 不显示 popover。
 */
export function parseTrigger(value: string, caret: number): { kind: 'slash' | 'mention'; start: number; query: string } | null {
  // 向前找最近的 `/` 或 `@`；遇到空白则放弃
  let i = caret - 1;
  while (i >= 0) {
    const ch = value[i]!;
    if (ch === ' ' || ch === '\n' || ch === '\t') return null;
    if (ch === '/' || ch === '@') {
      // 必须在行首 / 词边界
      const prev = i === 0 ? '' : value[i - 1];
      if (prev === '' || prev === ' ' || prev === '\n') {
        return { kind: ch === '/' ? 'slash' : 'mention', start: i, query: value.slice(i + 1, caret) };
      }
      return null;
    }
    i--;
  }
  return null;
}

export function useSlashMention(taRef: React.RefObject<HTMLTextAreaElement | null>): {
  state: SuggestionState;
  applySelection(itemId: string): void;
  reset(): void;
} {
  const composer = useComposerScopedStore();
  const draft = useComposerS((s) => s.draft);
  const setDraft = useComposerS((s) => s.setDraft);

  const tools = useTools();
  const mcps = useMcps();
  const kbs = useKbs();

  const caret = taRef.current?.selectionStart ?? draft.length;
  const trigger = parseTrigger(draft, caret);

  const state: SuggestionState = React.useMemo(() => {
    if (!trigger) return EMPTY;
    const lower = trigger.query.toLowerCase();
    if (trigger.kind === 'slash') {
      const items = SLASH_COMMANDS.filter((c) => c.id.includes(lower)).map((c) => ({
        id: `/${c.id}`,
        label: c.label,
        desc: c.desc,
      }));
      return { kind: 'slash', start: trigger.start, end: caret, query: trigger.query, items };
    }
    // mention：聚合 tools / mcps / kbs
    const all = [
      ...(tools.data ?? []).map((t) => ({ id: `@tool:${t.id}`, label: t.title, desc: t.description, group: 'tool' })),
      ...(mcps.data ?? []).map((m) => ({ id: `@mcp:${m.id}`, label: m.title, desc: m.description, group: 'mcp' })),
      ...(kbs.data ?? []).map((k) => ({ id: `@kb:${k.id}`, label: k.title, desc: k.description, group: 'kb' })),
    ].filter((x) => x.label.toLowerCase().includes(lower));
    return { kind: 'mention', start: trigger.start, end: caret, query: trigger.query, items: all };
  }, [trigger, caret, tools.data, mcps.data, kbs.data]);

  const applySelection = (itemId: string) => {
    if (!trigger) return;
    const before = draft.slice(0, trigger.start);
    const after = draft.slice(caret);
    const sep = ' ';
    const next = `${before}${itemId}${sep}${after}`;
    setDraft(next);
    // 顺手把 mention 落到 composer 的 selection，让发送时被收集
    if (itemId.startsWith('@tool:')) {
      const toolId = itemId.slice('@tool:'.length);
      const cur = composer.getState().selectedTools;
      if (!cur.includes(toolId)) composer.getState().setTools([...cur, toolId]);
    } else if (itemId.startsWith('@kb:')) {
      const kbId = itemId.slice('@kb:'.length);
      const cur = composer.getState().selectedKbs;
      if (!cur.includes(kbId)) composer.getState().setKbs([...cur, kbId]);
    } else if (itemId.startsWith('@mcp:')) {
      const mcpId = itemId.slice('@mcp:'.length);
      const cur = composer.getState().selectedMcps;
      if (!cur.includes(mcpId)) composer.getState().setMcps([...cur, mcpId]);
    } else if (itemId === '/think') {
      composer.setState({ deepThink: true });
    }
  };

  const reset = () => undefined; // 触发条件由 caret/value 自然决定

  return { state, applySelection, reset };
}
