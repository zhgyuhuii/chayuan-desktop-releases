/**
 * 把对话消息中的 artifact 候选同步到 artifact store。
 *
 * 设计：
 * - 仅 assistant 消息且 streaming 已结束才进入；流式过程中不抓，避免抖动；
 * - 用 message.id + 候选起始行作为 stable key，不会重复创建；
 * - 如果同一条消息多次出现（例如重新生成），按 messageId 替换。
 */

import * as React from 'react';
import { useArtifactStore } from '../../store/artifact';
import { extractArtifactCandidates } from './extract';
import type { ChatMessage } from '../chat/useChayuanChat';

export function useArtifactSync(messages: ChatMessage[]): void {
  const upsert = useArtifactStore((s) => s.upsert);
  const seenRef = React.useRef(new Set<string>());

  React.useEffect(() => {
    for (const m of messages) {
      if (m.role !== 'assistant' || m.streaming || !m.content) continue;
      const cands = extractArtifactCandidates(m.content);
      cands.forEach((c, idx) => {
        const id = `${m.id}::${idx}`;
        if (seenRef.current.has(id)) return;
        seenRef.current.add(id);
        upsert({
          id,
          key: id,
          title: defaultTitle(c.kind, c.language, idx),
          kind: c.kind,
          language: c.language,
          content: c.content,
          messageId: m.id,
          traceId: m.traceId,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        });
      });
    }
  }, [messages, upsert]);
}

function defaultTitle(kind: string, lang: string | undefined, idx: number): string {
  if (kind === 'mermaid') return `图表 ${idx + 1}`;
  if (kind === 'html') return `页面 ${idx + 1}`;
  if (kind === 'json') return `JSON ${idx + 1}`;
  if (lang) return `${lang} 代码 ${idx + 1}`;
  return `代码 ${idx + 1}`;
}
