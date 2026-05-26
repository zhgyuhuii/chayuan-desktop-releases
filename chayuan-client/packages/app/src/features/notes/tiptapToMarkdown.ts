/**
 * Tiptap JSON → Markdown 转换(MVP)。
 *
 * 支持:paragraph / heading(1-3) / bulletList / orderedList / listItem /
 *      blockquote / codeBlock / horizontalRule / hardBreak / text 的 bold/italic/strike/code,
 *      link / image。其它节点用纯文本回退。
 *
 * 不支持(MVP):table(StarterKit 不带)、taskList(同)、自定义节点。
 */

interface TipNode {
  type: string;
  attrs?: Record<string, any>;
  content?: TipNode[];
  text?: string;
  marks?: Array<{ type: string; attrs?: Record<string, any> }>;
}

export function tiptapJsonToMarkdown(doc: any): string {
  if (!doc || !doc.content) return '';
  return (doc.content as TipNode[]).map((n) => renderBlock(n)).join('\n\n').trim() + '\n';
}

function renderBlock(node: TipNode): string {
  switch (node.type) {
    case 'paragraph':
      return renderInline(node.content);
    case 'heading': {
      const level = Math.max(1, Math.min(6, node.attrs?.level ?? 1));
      return `${'#'.repeat(level)} ${renderInline(node.content)}`;
    }
    case 'bulletList':
      return (node.content ?? []).map((li) => `- ${renderBlock(li).replace(/\n/g, '\n  ')}`).join('\n');
    case 'orderedList':
      return (node.content ?? [])
        .map((li, i) => `${i + 1}. ${renderBlock(li).replace(/\n/g, '\n   ')}`)
        .join('\n');
    case 'listItem':
      return (node.content ?? []).map((c) => renderBlock(c)).join('\n');
    case 'blockquote':
      return (node.content ?? []).map((c) => `> ${renderBlock(c)}`).join('\n');
    case 'codeBlock': {
      const lang = node.attrs?.language ?? '';
      return `\`\`\`${lang}\n${(node.content ?? []).map((c) => c.text ?? '').join('')}\n\`\`\``;
    }
    case 'horizontalRule':
      return '---';
    case 'image':
      return `![${node.attrs?.alt ?? ''}](${node.attrs?.src ?? ''})`;
    case 'hardBreak':
      return '\n';
    default:
      return renderInline(node.content);
  }
}

function renderInline(content?: TipNode[]): string {
  if (!content) return '';
  return content.map((n) => renderTextNode(n)).join('');
}

function renderTextNode(node: TipNode): string {
  if (node.type === 'hardBreak') return '  \n';
  if (node.type === 'image') return `![${node.attrs?.alt ?? ''}](${node.attrs?.src ?? ''})`;
  if (node.type !== 'text') return renderInline(node.content);
  let t = node.text ?? '';
  for (const m of node.marks ?? []) {
    if (m.type === 'bold') t = `**${t}**`;
    else if (m.type === 'italic') t = `*${t}*`;
    else if (m.type === 'strike') t = `~~${t}~~`;
    else if (m.type === 'code') t = `\`${t}\``;
    else if (m.type === 'link') {
      const href = m.attrs?.href ?? '';
      t = `[${t}](${href})`;
    }
  }
  return t;
}
