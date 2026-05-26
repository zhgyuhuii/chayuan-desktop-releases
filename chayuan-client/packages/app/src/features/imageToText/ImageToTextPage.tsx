/**
 * 图转文 — 上传图像、跑 OCR、富文本编辑、导出 TXT / MD / Word。
 *
 * 流程:
 *   1. 用户拖入或点击选图(支持多张,依次处理后段落拼接)
 *   2. 调 modality.ocr() 走 RapidOCR sidecar(端口 18380)
 *   3. 把 OCR 文本按行切成段落写入 TipTap 编辑器(保留排版)
 *   4. 用户编辑文本后,工具栏导出 .txt / .md / .doc
 *
 * 设计权衡:
 *   - 编辑器复用 NoteEditor 同样的 TipTap 栈(StarterKit + Image + Link + Placeholder),
 *     无需新增 npm 依赖
 *   - Word 导出走 application/msword + HTML body —— Word/WPS 都能正常打开为 .doc 文档,
 *     免去 docx ZIP 库 200+KB 体积。.docx 严格场景用户可在 WPS 里另存为
 *   - Markdown 导出用 editor.getJSON() 走 doc 树自实现 — 浅 walker 即可覆盖
 *     StarterKit 支持的全部节点(heading/paragraph/list/blockquote/code/image/link/text)
 */
import * as React from 'react';
import { useEditor, EditorContent, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import {
  Bold, Code, FileDown, Heading1, Heading2, ImagePlus, Italic, Link as LinkIcon,
  List, ListOrdered, Loader2, Quote, RefreshCw, ScanText, Strikethrough, Trash2, Upload, X,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { modality, type OcrResult } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@chayuan/ui';
import { notifyInfo, notifySuccess, reportError } from '../../store/errorDialog';

interface OcrItem {
  /** UUID,React key */
  id: string;
  file: File;
  /** 本地预览 URL(objectURL) */
  url: string;
  status: 'queued' | 'running' | 'done' | 'error';
  result?: OcrResult;
  error?: string;
  /** OCR 实际开始的毫秒时间戳 */
  startedAt?: number;
}

const uid = () => Math.random().toString(36).slice(2, 10);

const defaultTitle = () => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `图转文 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const ImageToTextPage: React.FC = () => {
  const [items, setItems] = React.useState<OcrItem[]>([]);
  const [title, setTitle] = React.useState(() => defaultTitle());
  const [busy, setBusy] = React.useState(false);
  const dropRef = React.useRef<HTMLDivElement>(null);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: false }),
      Image.configure({ inline: false }),
      Link.configure({ openOnClick: false }),
      Placeholder.configure({
        placeholder: '识别结果会出现在这里 — 你可以直接编辑、加标题、调整排版,然后导出 Word/Markdown/TXT。',
      }),
    ],
    content: '',
  });

  // 卸载时回收所有 objectURL
  React.useEffect(() => {
    return () => {
      for (const it of items) {
        try { URL.revokeObjectURL(it.url); } catch { /* ignore */ }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const removeItem = React.useCallback((id: string) => {
    setItems((prev) => {
      const found = prev.find((it) => it.id === id);
      if (found) {
        try { URL.revokeObjectURL(found.url); } catch { /* ignore */ }
      }
      return prev.filter((it) => it.id !== id);
    });
  }, []);

  /** 把 OCR 文本插到编辑器末尾 — 按行切段,保留原始换行 */
  const insertOcrText = React.useCallback((file: File, text: string) => {
    if (!editor) return;
    const lines = (text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0) return;
    // 把图名作为 H2 分隔(多张图时方便识别来源);单张图不加
    editor.chain().focus('end').run();
    editor
      .chain()
      .focus('end')
      .insertContent(lines.map((line) => ({
        type: 'paragraph',
        content: [{ type: 'text', text: line }],
      })))
      .run();
  }, [editor]);

  /** 跑 OCR;按顺序 await,避免后端 sidecar 并发挤压 */
  const runOcr = React.useCallback(async (toProcess: OcrItem[]) => {
    if (toProcess.length === 0) return;
    setBusy(true);
    for (const it of toProcess) {
      // mark running
      setItems((prev) => prev.map((x) => x.id === it.id
        ? { ...x, status: 'running', startedAt: Date.now() }
        : x));
      try {
        const res = await modality.ocr(it.file);
        const text = (res?.text ?? '').trim();
        setItems((prev) => prev.map((x) => x.id === it.id
          ? { ...x, status: 'done', result: res }
          : x));
        if (text) {
          insertOcrText(it.file, text);
        } else {
          notifyInfo(`${it.file.name} 没有识别到文字`);
        }
      } catch (e) {
        const msg = (e instanceof Error ? e.message : String(e));
        setItems((prev) => prev.map((x) => x.id === it.id
          ? { ...x, status: 'error', error: msg }
          : x));
        if (msg.includes('OCR sidecar not ready') || msg.includes('503')) {
          reportError(e, 'OCR 服务未就绪 — 到「设置 → 本地模型服务 → OCR 文字识别」启动');
          break;  // 后端没起,后面继续也是失败
        }
        reportError(e, `识别失败:${it.file.name}`);
      }
    }
    setBusy(false);
  }, [insertOcrText]);

  /** 添加文件 → 进队列 → 自动启动 OCR */
  const addFiles = React.useCallback((files: File[]) => {
    if (files.length === 0) return;
    // 1. MIME 优先 — 浏览器原生 File 走这里
    // 2. 扩展名兜底 — Tauri pickFiles 老版本 File.type 为空,会全被 MIME 滤掉,
    //    导致用户"明明选了 jpg 还报请选图片文件"。新版 Tauri 已经推 MIME,
    //    保留这条兜底纯属防御
    const isImg = (f: File) =>
      f.type.startsWith('image/') ||
      /\.(jpe?g|png|webp|bmp|gif|tiff?|avif|heic|heif|svg|ico)$/i.test(f.name);
    const imgFiles = files.filter(isImg);
    if (imgFiles.length === 0) {
      notifyInfo('请选图片文件(jpg / png / webp / bmp …)');
      return;
    }
    const fresh: OcrItem[] = imgFiles.map((f) => ({
      id: uid(),
      file: f,
      url: URL.createObjectURL(f),
      status: 'queued',
    }));
    setItems((prev) => [...prev, ...fresh]);
    void runOcr(fresh);
  }, [runOcr]);

  const onPickFiles = React.useCallback(async () => {
    try {
      const picked = await getPlatform().fs.pickFiles({
        multiple: true,
        accept: ['image/*'],
      });
      if (picked.length > 0) addFiles(picked as unknown as File[]);
    } catch (e) {
      reportError(e, '选文件失败');
    }
  }, [addFiles]);

  // 拖拽 — 用 platform 的 readDropped 解析,跟其它上传组件保持一致
  const onDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };
  const onDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const files = await getPlatform().fs.readDropped(e.nativeEvent);
      addFiles(files);
    } catch (err) {
      reportError(err, '读拖入文件失败');
    }
  };

  // 重新识别单条
  const onRetryItem = React.useCallback((id: string) => {
    const it = items.find((x) => x.id === id);
    if (!it) return;
    void runOcr([it]);
  }, [items, runOcr]);

  const onClearAll = React.useCallback(async () => {
    // 走 platform.dialog.confirm(plugin-dialog.ask),不要裸 confirm():
    // Tauri 2 webview 把 window.confirm 重定向到 plugin:dialog|confirm,某些
    // Tauri 2.x 版本即使授权 dialog:allow-confirm 也会 ACL 拒。
    const ok = await getPlatform().dialog.confirm('清空所有图片和编辑内容?', { title: '清空' });
    if (!ok) return;
    for (const it of items) {
      try { URL.revokeObjectURL(it.url); } catch { /* ignore */ }
    }
    setItems([]);
    editor?.chain().clearContent().run();
  }, [items, editor]);

  return (
    <div className="flex h-full flex-col bg-[var(--cy-surface-base)]">
      <Header
        title={title}
        onTitleChange={setTitle}
        canExport={!!editor && !editor.isEmpty}
        editor={editor}
        onClearAll={onClearAll}
        anyContent={items.length > 0 || (!!editor && !editor.isEmpty)}
      />

      <div className="grid min-h-0 flex-1 grid-cols-12 gap-3 p-3">
        {/* 左侧:上传 + 已识别图列表 */}
        <aside className="col-span-3 flex min-h-0 flex-col rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
          <div
            ref={dropRef}
            onDragOver={onDragOver}
            onDrop={onDrop}
            className={cn(
              'm-3 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-6 text-center transition-colors',
              'hover:border-[var(--cy-brand-400)] hover:bg-[var(--cy-brand-50)] dark:hover:bg-[var(--cy-brand-900)]/20',
            )}
          >
            <ImagePlus className="h-7 w-7 text-[var(--cy-brand-500)]" />
            <p className="text-xs text-[var(--cy-text-secondary)]">拖入图片或</p>
            <Button
              size="sm"
              onClick={() => void onPickFiles()}
              disabled={busy}
              className="h-7 gap-1 text-xs"
            >
              <Upload className="h-3.5 w-3.5" />
              选图片
            </Button>
            <p className="mt-1 text-[10px] text-[var(--cy-text-tertiary)]">
              支持 jpg / png / webp / bmp,可一次多张
            </p>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {items.length === 0 ? (
              <p className="px-1 py-2 text-center text-[11px] text-[var(--cy-text-tertiary)]">
                还没有图片
              </p>
            ) : (
              <ul className="space-y-2">
                {items.map((it) => (
                  <OcrItemCard
                    key={it.id}
                    item={it}
                    onRemove={() => removeItem(it.id)}
                    onRetry={() => onRetryItem(it.id)}
                  />
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* 右侧:富文本编辑器 */}
        <main className="col-span-9 flex min-h-0 flex-col rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
          {editor && <EditorToolbar editor={editor} />}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <EditorContent
              editor={editor}
              className="prose prose-sm max-w-none focus-within:outline-none [&_.ProseMirror]:min-h-[60vh] [&_.ProseMirror]:outline-none [&_.ProseMirror_p.is-editor-empty:first-child::before]:pointer-events-none [&_.ProseMirror_p.is-editor-empty:first-child::before]:float-left [&_.ProseMirror_p.is-editor-empty:first-child::before]:h-0 [&_.ProseMirror_p.is-editor-empty:first-child::before]:text-[var(--cy-text-tertiary)] [&_.ProseMirror_p.is-editor-empty:first-child::before]:content-[attr(data-placeholder)]"
            />
          </div>
        </main>
      </div>
    </div>
  );
};

// ─── 顶部 Header(标题 + 导出菜单) ──────────────────────────────────────────

const Header: React.FC<{
  title: string;
  onTitleChange(v: string): void;
  canExport: boolean;
  editor: Editor | null;
  onClearAll(): void;
  anyContent: boolean;
}> = ({ title, onTitleChange, canExport, editor, onClearAll, anyContent }) => {
  return (
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-2.5">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <ScanText className="h-4 w-4 flex-none text-[var(--cy-brand-500)]" />
        <input
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="标题"
          className="min-w-0 flex-1 bg-transparent text-sm font-medium text-[var(--cy-text-primary)] focus-visible:outline-none"
        />
      </div>
      <div className="flex items-center gap-2">
        {anyContent && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onClearAll}
            className="h-7 gap-1 text-xs text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            清空
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              disabled={!canExport}
              className="h-7 gap-1 text-xs"
            >
              <FileDown className="h-3.5 w-3.5" />
              导出
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => editor && exportText(editor, title)}>
              纯文本 .txt
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => editor && exportMarkdown(editor, title)}>
              Markdown .md
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => editor && exportWord(editor, title)}>
              Word .doc
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
};

// ─── 单条 OCR 项卡片 ─────────────────────────────────────────────────────

const OcrItemCard: React.FC<{
  item: OcrItem;
  onRemove(): void;
  onRetry(): void;
}> = ({ item, onRemove, onRetry }) => {
  const elapsedMs = item.result?.elapsed_ms;
  return (
    <li className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2">
      <div className="flex gap-2">
        <img
          src={item.url}
          alt={item.file.name}
          className="h-16 w-16 flex-none rounded-md object-cover"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-[var(--cy-text-primary)]" title={item.file.name}>
            {item.file.name}
          </p>
          <StatusLine item={item} />
          {elapsedMs != null && item.status === 'done' && (
            <p className="mt-0.5 text-[10px] text-[var(--cy-text-tertiary)]">
              耗时 {(elapsedMs / 1000).toFixed(1)}s · {item.result?.box_count ?? 0} 行
            </p>
          )}
        </div>
        <div className="flex flex-none flex-col gap-1">
          {(item.status === 'done' || item.status === 'error') && (
            <button
              type="button"
              onClick={onRetry}
              title="重新识别"
              className="rounded-md p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            >
              <RefreshCw className="h-3 w-3" />
            </button>
          )}
          <button
            type="button"
            onClick={onRemove}
            title="移除"
            className="rounded-md p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-destructive"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>
    </li>
  );
};

const StatusLine: React.FC<{ item: OcrItem }> = ({ item }) => {
  if (item.status === 'queued') {
    return <p className="text-[10px] text-[var(--cy-text-tertiary)]">排队中…</p>;
  }
  if (item.status === 'running') {
    return (
      <p className="inline-flex items-center gap-1 text-[10px] text-[var(--cy-brand-600)]">
        <Loader2 className="h-3 w-3 animate-spin" /> 识别中…
      </p>
    );
  }
  if (item.status === 'error') {
    return (
      <p className="truncate text-[10px] text-destructive" title={item.error}>
        失败:{item.error?.slice(0, 60)}
      </p>
    );
  }
  return <p className="text-[10px] text-emerald-600">已识别</p>;
};

// ─── TipTap 工具栏(从 NoteEditor 抄,精简版) ───────────────────────────

const EditorToolbar: React.FC<{ editor: Editor }> = ({ editor }) => {
  const btn = (active: boolean) => cn(
    'inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-secondary)] transition-colors',
    active ? 'bg-[var(--cy-brand-500)]/15 text-[var(--cy-brand-700)]'
      : 'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
  );
  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-1">
      <button type="button" className={btn(editor.isActive('bold'))} onClick={() => editor.chain().focus().toggleBold().run()} title="加粗">
        <Bold className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('italic'))} onClick={() => editor.chain().focus().toggleItalic().run()} title="斜体">
        <Italic className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('strike'))} onClick={() => editor.chain().focus().toggleStrike().run()} title="删除线">
        <Strikethrough className="h-3.5 w-3.5" />
      </button>
      <Sep />
      <button type="button" className={btn(editor.isActive('heading', { level: 1 }))} onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()} title="一级标题">
        <Heading1 className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('heading', { level: 2 }))} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} title="二级标题">
        <Heading2 className="h-3.5 w-3.5" />
      </button>
      <Sep />
      <button type="button" className={btn(editor.isActive('bulletList'))} onClick={() => editor.chain().focus().toggleBulletList().run()} title="无序列表">
        <List className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('orderedList'))} onClick={() => editor.chain().focus().toggleOrderedList().run()} title="有序列表">
        <ListOrdered className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('blockquote'))} onClick={() => editor.chain().focus().toggleBlockquote().run()} title="引用">
        <Quote className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={btn(editor.isActive('codeBlock'))} onClick={() => editor.chain().focus().toggleCodeBlock().run()} title="代码块">
        <Code className="h-3.5 w-3.5" />
      </button>
      <Sep />
      <button
        type="button"
        className={btn(editor.isActive('link'))}
        title="插入链接"
        onClick={() => {
          const prev = editor.getAttributes('link').href as string | undefined;
          const url = window.prompt('链接地址', prev || 'https://');
          if (url === null) return;
          if (url === '') {
            editor.chain().focus().unsetLink().run();
            return;
          }
          editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
        }}
      >
        <LinkIcon className="h-3.5 w-3.5" />
      </button>
    </div>
  );
};

const Sep = () => <span className="mx-1 h-4 w-px bg-[var(--cy-border-subtle)]" />;

// ─── 导出工具 ────────────────────────────────────────────────────────────

function safeFileName(title: string): string {
  return (title || '图转文').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80) || '图转文';
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportText(editor: Editor, title: string): void {
  const text = editor.getText();
  if (!text.trim()) {
    notifyInfo('编辑器为空,无内容可导出');
    return;
  }
  downloadBlob(new Blob([text], { type: 'text/plain;charset=utf-8' }), `${safeFileName(title)}.txt`);
  notifySuccess('已导出 TXT');
}

function exportMarkdown(editor: Editor, title: string): void {
  const md = jsonToMarkdown(editor.getJSON());
  if (!md.trim()) {
    notifyInfo('编辑器为空,无内容可导出');
    return;
  }
  // 加 H1 标题(用户起的页面标题)
  const out = `# ${title}\n\n${md.trim()}\n`;
  downloadBlob(new Blob([out], { type: 'text/markdown;charset=utf-8' }), `${safeFileName(title)}.md`);
  notifySuccess('已导出 Markdown');
}

function exportWord(editor: Editor, title: string): void {
  const html = editor.getHTML();
  if (!html.replace(/<[^>]*>/g, '').trim()) {
    notifyInfo('编辑器为空,无内容可导出');
    return;
  }
  // Word 兼容的 HTML — Word/WPS 都能识别这个 mso 命名空间,并按 .doc 文档解析
  const doc = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<title>${escapeHtml(title)}</title>
<style>
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 14px; line-height: 1.6; }
  h1 { font-size: 22px; }
  h2 { font-size: 18px; }
  blockquote { border-left: 3px solid #ddd; padding-left: 12px; color: #666; }
  pre { background: #f5f5f5; padding: 8px; border-radius: 4px; }
  code { background: #f5f5f5; padding: 2px 4px; border-radius: 3px; }
</style>
</head>
<body>
<h1>${escapeHtml(title)}</h1>
${html}
</body>
</html>`;
  downloadBlob(new Blob([doc], { type: 'application/msword' }), `${safeFileName(title)}.doc`);
  notifySuccess('已导出 Word');
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c] as string);
}

// ─── TipTap JSON → Markdown ─────────────────────────────────────────────
// 浅 walker,覆盖 StarterKit + Image + Link 的全部节点类型。

interface PMNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: PMNode[];
  text?: string;
  marks?: Array<{ type: string; attrs?: Record<string, unknown> }>;
}

function jsonToMarkdown(doc: unknown): string {
  const root = doc as PMNode | undefined;
  if (!root || !root.content) return '';
  return root.content.map((n) => nodeToMd(n, 0)).join('\n\n');
}

function nodeToMd(n: PMNode, depth: number): string {
  switch (n.type) {
    case 'heading': {
      const level = Math.min(6, Number(n.attrs?.level ?? 1));
      return `${'#'.repeat(level)} ${inlineToMd(n.content)}`;
    }
    case 'paragraph':
      return inlineToMd(n.content);
    case 'bulletList':
      return (n.content ?? []).map((li) => listItemToMd(li, depth, false)).join('\n');
    case 'orderedList':
      return (n.content ?? []).map((li, i) => listItemToMd(li, depth, true, i + 1)).join('\n');
    case 'listItem':
      // 通常被父 list 处理;独立出现走 fallback
      return (n.content ?? []).map((c) => nodeToMd(c, depth)).join('\n');
    case 'blockquote':
      return (n.content ?? [])
        .map((c) => nodeToMd(c, depth))
        .join('\n')
        .split('\n')
        .map((line) => `> ${line}`)
        .join('\n');
    case 'codeBlock': {
      const lang = String(n.attrs?.language ?? '');
      return `\`\`\`${lang}\n${(n.content ?? []).map((c) => c.text ?? '').join('')}\n\`\`\``;
    }
    case 'image': {
      const src = String(n.attrs?.src ?? '');
      const alt = String(n.attrs?.alt ?? '');
      return `![${alt}](${src})`;
    }
    case 'horizontalRule':
      return '---';
    case 'hardBreak':
      return '  \n';
    default:
      return inlineToMd(n.content);
  }
}

function listItemToMd(li: PMNode, depth: number, ordered: boolean, n = 1): string {
  const indent = '  '.repeat(depth);
  const marker = ordered ? `${n}.` : '-';
  const inner = (li.content ?? []).map((c, i) => {
    if (c.type === 'bulletList' || c.type === 'orderedList') {
      return nodeToMd(c, depth + 1);
    }
    return i === 0 ? inlineToMd(c.content) : nodeToMd(c, depth);
  }).join('\n');
  // 第一行带 marker,其余按 indent 缩进
  const lines = inner.split('\n');
  return `${indent}${marker} ${lines[0] ?? ''}${lines.slice(1).map((l) => `\n${indent}  ${l}`).join('')}`;
}

function inlineToMd(nodes?: PMNode[]): string {
  if (!nodes) return '';
  return nodes.map((n) => {
    if (n.type === 'text') {
      let t = n.text ?? '';
      const marks = n.marks ?? [];
      // 顺序:link 在最外,然后 code,然后 bold/italic/strike
      for (const m of marks) {
        if (m.type === 'code') t = `\`${t}\``;
        else if (m.type === 'bold') t = `**${t}**`;
        else if (m.type === 'italic') t = `*${t}*`;
        else if (m.type === 'strike') t = `~~${t}~~`;
      }
      const link = marks.find((m) => m.type === 'link');
      if (link?.attrs?.href) t = `[${t}](${String(link.attrs.href)})`;
      return t;
    }
    if (n.type === 'hardBreak') return '  \n';
    return inlineToMd(n.content);
  }).join('');
}
