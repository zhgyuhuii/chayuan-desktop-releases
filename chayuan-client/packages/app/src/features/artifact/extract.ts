/**
 * 从对话内容中识别可 promote 为 Artifact 的代码块。
 *
 * 触发条件（启发式）：
 *  - 围栏代码块 ```lang…``` 行数 >= MIN_LINES（默认 8）
 *  - 显式语言为 mermaid / html / svg / json 时无视行数阈值
 *
 * 返回每个候选片段的 { kind, language, content, startLine }。
 */

const MIN_LINES = 8;
const ARTIFACT_LANGS = new Set(['mermaid', 'html', 'svg', 'json']);

export type ArtifactKind = 'code' | 'markdown' | 'mermaid' | 'html' | 'json';

export interface ExtractedArtifact {
  kind: ArtifactKind;
  language?: string;
  content: string;
  startLine: number;
}

export function extractArtifactCandidates(text: string): ExtractedArtifact[] {
  const out: ExtractedArtifact[] = [];
  const lines = text.split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? '';
    const m = /^```\s*([\w+-]*)\s*$/.exec(line);
    if (m) {
      const lang = (m[1] || '').toLowerCase();
      const start = i;
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i]!.startsWith('```')) {
        buf.push(lines[i]!);
        i++;
      }
      i++; // 跳过结束 ```
      const body = buf.join('\n');
      const isLong = buf.length >= MIN_LINES;
      const isSpecial = ARTIFACT_LANGS.has(lang);
      if (!isLong && !isSpecial) continue;

      let kind: ArtifactKind = 'code';
      if (lang === 'mermaid') kind = 'mermaid';
      else if (lang === 'html' || lang === 'svg') kind = 'html';
      else if (lang === 'json') kind = 'json';

      out.push({ kind, language: lang || undefined, content: body, startLine: start });
      continue;
    }
    i++;
  }
  return out;
}
