/**
 * 把笔记保存到指定 doc KB:Tiptap JSON → markdown → upload_docs。
 */
import { kb } from '@chayuan/api';
import { tiptapJsonToMarkdown } from './tiptapToMarkdown';

export async function saveNoteToKB(args: {
  title: string;
  content: any; // Tiptap JSON
  kbName: string;
  /** 可选 AI 摘要 markdown,会嵌到文件头部 — KB 检索时可见,也方便下次手动看 */
  summary?: string;
}): Promise<{ saved_files: string[]; failed: Record<string, string> }> {
  const body = tiptapJsonToMarkdown(args.content);
  // 摘要嵌头部:用 blockquote 包起来 + 明显标题,Tiptap 加载回来也能识别成引用块。
  const head = args.summary && args.summary.trim()
    ? `> **🤖 AI 摘要**\n>\n${args.summary.trim().split('\n').map((l) => '> ' + l).join('\n')}\n\n---\n\n`
    : '';
  const markdown = head + body;
  const safeTitle = (args.title || 'note').replace(/[/\\?%*:|"<>]/g, '-').slice(0, 100);
  const file = new File([markdown], `${safeTitle}.md`, { type: 'text/markdown' });
  const r = await kb.uploadDocs({
    files: [file],
    knowledge_base_name: args.kbName,
    override: true,
    to_vector_store: true,
  });
  return {
    saved_files: r.data?.saved_files ?? [],
    failed: r.data?.failed_saves ?? {},
  };
}
