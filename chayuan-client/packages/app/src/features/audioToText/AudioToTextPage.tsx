/**
 * 音转文 — 上传音频、ASR 转写、按说话人分段、富文本编辑、导出 TXT / MD / Word。
 *
 * 流程:
 *   1. 用户拖入或选音频(mp3 / wav / m4a / webm / ogg / flac / opus)
 *   2. 调 modality.transcribeFile(file, { diarize: true }) — 后端先试 FunASR
 *      sidecar(CAM++ 说话人分离),失败回退 whisper.cpp 单 speaker
 *   3. 按 segment.speaker 分组写入 TipTap:每个 speaker 块前缀 H3 "A: / B: /
 *      C:";没有 diarization 信息时直接当成一整段
 *   4. 用户可在编辑器里改文字、调说话人标签;工具栏 Word / MD / TXT 导出
 *
 * 设计权衡:
 *   - 富文本栈复用 NoteEditor / ImageToTextPage 的 TipTap 组合,零新增依赖
 *   - 说话人分段在前端做,不依赖后端必须返"已格式化文本";后端只需保证
 *     segments[].speaker 准确,前端独立染色 + 分段
 *   - speaker_diarization_available=false 时只产一个块,UI 也不显示色块标签 —
 *     避免让用户误以为"分到一个人"
 */
import * as React from 'react';
import { useEditor, EditorContent, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import {
  Bold, Code, FileDown, FileAudio, Heading1, Heading2, Italic, Link as LinkIcon,
  List, ListOrdered, Loader2, Mic, Quote, RefreshCw, Strikethrough, Trash2, Upload, Users, X,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { modality, type TranscribeFileResult, type TranscribeFileSegment } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@chayuan/ui';
import { notifyInfo, notifySuccess, reportError } from '../../store/errorDialog';
import { resolveLocale, useSettingsStore } from '../../store/settings';

/**
 * 把 chayuan UI locale 映射成 whisper / FunASR 能识别的 ASR 语言码。
 * - zh-CN → zh(简体中文,后端会走 zhconv 二次保证简体)
 * - en/ja/ko/de/fr → 原样(ISO 639-1)
 * - 未知 → 'auto' 让 whisper 自动检测
 */
function localeToAsrLanguage(locale: string): string {
  if (locale === 'zh-CN' || locale === 'zh') return 'zh';
  if (['en', 'ja', 'ko', 'de', 'fr'].includes(locale)) return locale;
  return 'auto';
}

interface AsrItem {
  id: string;
  file: File;
  /** 本地音频预览 URL */
  url: string;
  status: 'queued' | 'running' | 'done' | 'error';
  result?: TranscribeFileResult;
  error?: string;
  startedAt?: number;
}

/** 说话人 → 显示用字母 A/B/C 的映射(顺序按出现顺序,不按 ID 数字) */
function buildSpeakerLabels(segments: TranscribeFileSegment[]): Map<string, string> {
  const map = new Map<string, string>();
  let idx = 0;
  for (const seg of segments) {
    const k = seg.speaker || 'speaker_0';
    if (!map.has(k)) {
      map.set(k, String.fromCharCode(65 + Math.min(idx, 25)));  // A...Z
      idx += 1;
    }
  }
  return map;
}

const uid = () => Math.random().toString(36).slice(2, 10);

const defaultTitle = () => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `音转文 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const AUDIO_EXT_RE = /\.(mp3|wav|m4a|aac|webm|ogg|oga|opus|flac|amr|wma)$/i;
const isAudioFile = (f: File) =>
  f.type.startsWith('audio/') || AUDIO_EXT_RE.test(f.name);

export const AudioToTextPage: React.FC = () => {
  const [items, setItems] = React.useState<AsrItem[]>([]);
  const [title, setTitle] = React.useState(() => defaultTitle());
  const [busy, setBusy] = React.useState(false);
  // 读"设置 → 语言"偏好,转成 ASR 用的语言码;'system' 走 resolveLocale 拿
  // 真实系统 locale。这条订阅让用户切语言后下次转写立刻生效,无需重启。
  const localePref = useSettingsStore((s) => s.locale);
  const asrLanguage = React.useMemo(
    () => localeToAsrLanguage(resolveLocale(localePref)),
    [localePref],
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: false }),
      Image.configure({ inline: false }),
      Link.configure({ openOnClick: false }),
      Placeholder.configure({
        placeholder: '上传音频,转写文本会按"A: / B: / C:" 自动分段出现在这里 — 可直接编辑、改说话人标签、再导出 Word / Markdown / TXT。',
      }),
    ],
    content: '',
  });

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

  /** 把单条 ASR 结果按 speaker 分组写入编辑器末尾 */
  const insertAsr = React.useCallback((file: File, result: TranscribeFileResult) => {
    if (!editor) return;
    const text = (result.text || '').trim();
    const segments = result.segments || [];
    if (!text && segments.length === 0) return;

    editor.chain().focus('end').run();

    // 没拿到真实 diarization → 整段一次性写入,不加 speaker 标签;
    // 多个文件 / 多次转写时,加 H2 标题分隔来源
    if (!result.speaker_diarization_available || segments.length === 0) {
      const blocks: object[] = [
        { type: 'heading', attrs: { level: 2 },
          content: [{ type: 'text', text: file.name }] },
      ];
      const lines = (text || segments.map((s) => s.text).join('\n'))
        .split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      for (const line of lines) {
        blocks.push({ type: 'paragraph', content: [{ type: 'text', text: line }] });
      }
      editor.chain().focus('end').insertContent(blocks).run();
      return;
    }

    // 有 diarization → 按 speaker 分段;同一说话人连续多段合并成一个 H3 块
    const labels = buildSpeakerLabels(segments);
    type Turn = { speaker: string; label: string; lines: string[] };
    const turns: Turn[] = [];
    for (const seg of segments) {
      const line = (seg.text || '').trim();
      if (!line) continue;
      const lbl = labels.get(seg.speaker) || 'A';
      const last = turns[turns.length - 1];
      if (last && last.speaker === seg.speaker) {
        last.lines.push(line);
      } else {
        turns.push({ speaker: seg.speaker, label: lbl, lines: [line] });
      }
    }

    const blocks: object[] = [
      { type: 'heading', attrs: { level: 2 },
        content: [{ type: 'text', text: `${file.name} · ${labels.size} 位说话人` }] },
    ];
    for (const t of turns) {
      // H3 显示 "A:" / "B:" 等;后面紧跟 paragraph
      blocks.push({
        type: 'heading', attrs: { level: 3 },
        content: [{ type: 'text', text: `${t.label}:` }],
      });
      for (const line of t.lines) {
        blocks.push({ type: 'paragraph', content: [{ type: 'text', text: line }] });
      }
    }
    editor.chain().focus('end').insertContent(blocks).run();
  }, [editor]);

  /** 跑 ASR — 顺序执行避免 sidecar 模型挤压 */
  const runAsr = React.useCallback(async (toProcess: AsrItem[]) => {
    if (toProcess.length === 0) return;
    setBusy(true);
    for (const it of toProcess) {
      setItems((prev) => prev.map((x) => x.id === it.id
        ? { ...x, status: 'running', startedAt: Date.now() }
        : x));
      try {
        const res = await modality.transcribeFile(it.file, {
          diarize: true,
          language: asrLanguage,
        });
        setItems((prev) => prev.map((x) => x.id === it.id
          ? { ...x, status: 'done', result: res }
          : x));
        if (res.text || (res.segments || []).length > 0) {
          insertAsr(it.file, res);
          if (res.speaker_diarization_available && (res.speaker_count ?? 0) > 1) {
            notifySuccess(`已识别 ${res.speaker_count} 位说话人`);
          }
        } else {
          notifyInfo(`${it.file.name} 没有识别到内容`);
        }
      } catch (e) {
        const msg = (e instanceof Error ? e.message : String(e));
        setItems((prev) => prev.map((x) => x.id === it.id
          ? { ...x, status: 'error', error: msg }
          : x));
        if (msg.includes('503') || msg.toLowerCase().includes('not ready')) {
          reportError(e, 'ASR 服务未就绪 — 检查「设置 → 本地模型服务」是否启动了 ASR / FunASR');
          break;
        }
        reportError(e, `转写失败:${it.file.name}`);
      }
    }
    setBusy(false);
  }, [insertAsr, asrLanguage]);

  const addFiles = React.useCallback((files: File[]) => {
    if (files.length === 0) return;
    const audios = files.filter(isAudioFile);
    if (audios.length === 0) {
      notifyInfo('请选音频文件(mp3 / wav / m4a / webm / ogg / flac …)');
      return;
    }
    const fresh: AsrItem[] = audios.map((f) => ({
      id: uid(),
      file: f,
      url: URL.createObjectURL(f),
      status: 'queued',
    }));
    setItems((prev) => [...prev, ...fresh]);
    void runAsr(fresh);
  }, [runAsr]);

  const onPickFiles = React.useCallback(async () => {
    try {
      const picked = await getPlatform().fs.pickFiles({
        multiple: true,
        // 'audio/*' 在 Tauri 端 optsToFilters 没覆盖,直接给具体扩展名
        accept: ['.mp3', '.wav', '.m4a', '.aac', '.webm', '.ogg', '.opus', '.flac', '.amr'],
      });
      if (picked.length > 0) addFiles(picked as unknown as File[]);
    } catch (e) {
      reportError(e, '选文件失败');
    }
  }, [addFiles]);

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

  const onRetryItem = React.useCallback((id: string) => {
    const it = items.find((x) => x.id === id);
    if (!it) return;
    void runAsr([it]);
  }, [items, runAsr]);

  const onClearAll = React.useCallback(async () => {
    // 走 platform.dialog.confirm — 见 ImageToTextPage 同处注释
    const ok = await getPlatform().dialog.confirm('清空所有音频和编辑内容?', { title: '清空' });
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
        <aside className="col-span-3 flex min-h-0 flex-col rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
          <div
            onDragOver={onDragOver}
            onDrop={onDrop}
            className={cn(
              'm-3 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-6 text-center transition-colors',
              'hover:border-[var(--cy-brand-400)] hover:bg-[var(--cy-brand-50)] dark:hover:bg-[var(--cy-brand-900)]/20',
            )}
          >
            <FileAudio className="h-7 w-7 text-[var(--cy-brand-500)]" />
            <p className="text-xs text-[var(--cy-text-secondary)]">拖入音频或</p>
            <Button
              size="sm"
              onClick={() => void onPickFiles()}
              disabled={busy}
              className="h-7 gap-1 text-xs"
            >
              <Upload className="h-3.5 w-3.5" />
              选音频
            </Button>
            <p className="mt-1 text-[10px] text-[var(--cy-text-tertiary)]">
              mp3 / wav / m4a / webm / ogg / flac
            </p>
            <p className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-emerald-700 dark:text-emerald-300">
              <Users className="h-3 w-3" />
              自动尝试说话人分离(需 FunASR sidecar)
            </p>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {items.length === 0 ? (
              <p className="px-1 py-2 text-center text-[11px] text-[var(--cy-text-tertiary)]">
                还没有音频
              </p>
            ) : (
              <ul className="space-y-2">
                {items.map((it) => (
                  <AsrItemCard
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

// ─── Header ────────────────────────────────────────────────────────────

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
        <Mic className="h-4 w-4 flex-none text-[var(--cy-brand-500)]" />
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

// ─── ASR 单条卡片 ─────────────────────────────────────────────────────

const AsrItemCard: React.FC<{
  item: AsrItem;
  onRemove(): void;
  onRetry(): void;
}> = ({ item, onRemove, onRetry }) => {
  const spkCount = item.result?.speaker_count ?? 0;
  return (
    <li className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2">
      <div className="flex gap-2">
        <div className="flex h-14 w-14 flex-none items-center justify-center rounded-md bg-[var(--cy-brand-50)] dark:bg-[var(--cy-brand-900)]/30">
          <FileAudio className="h-6 w-6 text-[var(--cy-brand-500)]" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-[var(--cy-text-primary)]" title={item.file.name}>
            {item.file.name}
          </p>
          <p className="text-[10px] text-[var(--cy-text-tertiary)]">
            {(item.file.size / 1024).toFixed(0)} KB
          </p>
          <StatusLine item={item} />
          {item.status === 'done' && item.result?.speaker_diarization_available && spkCount > 0 && (
            <p className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-emerald-700 dark:text-emerald-300">
              <Users className="h-3 w-3" />
              {spkCount} 位说话人
            </p>
          )}
          {item.status === 'done' && !item.result?.speaker_diarization_available && (
            <p className="mt-0.5 text-[10px] text-amber-700 dark:text-amber-300">
              未分话人(FunASR 未启动)
            </p>
          )}
          <audio src={item.url} controls className="mt-1 h-7 w-full" preload="metadata" />
        </div>
        <div className="flex flex-none flex-col gap-1">
          {(item.status === 'done' || item.status === 'error') && (
            <button
              type="button"
              onClick={onRetry}
              title="重新转写"
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

const StatusLine: React.FC<{ item: AsrItem }> = ({ item }) => {
  if (item.status === 'queued') {
    return <p className="text-[10px] text-[var(--cy-text-tertiary)]">排队中…</p>;
  }
  if (item.status === 'running') {
    return (
      <p className="inline-flex items-center gap-1 text-[10px] text-[var(--cy-brand-600)]">
        <Loader2 className="h-3 w-3 animate-spin" /> 转写中(首次模型加载可能 30s+)…
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
  return <p className="text-[10px] text-emerald-600">已转写</p>;
};

// ─── 工具栏 + 导出(跟 ImageToTextPage 同形,DRY 留给未来抽公共 lib) ──────

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

// ─── 导出工具(与 ImageToTextPage 共用同套实现;暂时复制以避免外部 lib) ───

function safeFileName(title: string): string {
  return (title || '音转文').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80) || '音转文';
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
  const doc = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<title>${escapeHtml(title)}</title>
<style>
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 14px; line-height: 1.6; }
  h1 { font-size: 22px; }
  h2 { font-size: 18px; }
  h3 { font-size: 16px; color: #2563eb; }
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
  const lines = inner.split('\n');
  return `${indent}${marker} ${lines[0] ?? ''}${lines.slice(1).map((l) => `\n${indent}  ${l}`).join('')}`;
}

function inlineToMd(nodes?: PMNode[]): string {
  if (!nodes) return '';
  return nodes.map((n) => {
    if (n.type === 'text') {
      let t = n.text ?? '';
      const marks = n.marks ?? [];
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
