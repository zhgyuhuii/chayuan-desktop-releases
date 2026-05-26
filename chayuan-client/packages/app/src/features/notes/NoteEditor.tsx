/**
 * Tiptap 富文本笔记编辑器主组件。
 *
 * 功能:
 *   - StarterKit + Image + Link + Placeholder
 *   - 标题 + 工具栏(B/I/U/H1/H2/列表/引用/代码)+ 麦克风(实时 ASR 插入)+ 保存按钮
 *   - localStorage 草稿持久化(useNoteDraft)
 *   - Ctrl/Cmd+S 触发保存弹窗
 */
import * as React from 'react';
import { useEditor, EditorContent, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import {
  Bold, Code, FileAudio, Heading1, Heading2, Image as ImageIcon, Italic, Link as LinkIcon,
  List, ListOrdered, Loader2, Mic, MicOff, Paperclip, Quote, RefreshCw, Save, Sparkles,
  Strikethrough, X,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { modality } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { useMicSimpleRecorder } from '../composer/useMicSimpleRecorder';
import { RecorderBar } from '../composer/RecorderBar';
import { useNoteDraft } from './useNoteDraft';
import { SaveNoteDialog } from './SaveNoteDialog';
import { AsrVolatileMark } from './AsrVolatileMark';
import { useNoteSummary } from './useNoteSummary';
import { SummaryPanel } from './SummaryPanel';
import {
  convertEditorChinese, convertText, detectChineseScript, persistChineseScript,
  type ChineseScript,
} from './chineseScript';
import { notifyInfo, notifySuccess, reportError } from '../../store/errorDialog';

const defaultTitle = () => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `AI 笔记 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export interface NoteEditorProps {
  /** 保存成功后回调(M4 用 — 跳转到 KB 详情) */
  onSaved?(kbName: string): void;
  /** localStorage 草稿 key(默认 chayuan:note-draft:new)*/
  draftKey?: string;
}

export const NoteEditor: React.FC<NoteEditorProps> = ({ onSaved, draftKey }) => {
  const draft = useNoteDraft(draftKey);
  const initial = React.useMemo(() => draft.load(), [draft]);
  const [title, setTitle] = React.useState(() => initial?.title ?? defaultTitle());
  const [saveOpen, setSaveOpen] = React.useState(false);
  const defaultKb = React.useMemo(() => {
    try { return localStorage.getItem('chayuan:notes:last-kb') ?? undefined; }
    catch { return undefined; }
  }, []);

  // AI 摘要 — 默认值取草稿里持久化的;打开时面板自动恢复
  const summaryHook = useNoteSummary(initial?.summary);
  const [summaryOpen, setSummaryOpen] = React.useState<boolean>(!!initial?.summary);

  const editor = useEditor({
    extensions: [
      // Tiptap v3 起 StarterKit 自带 Link;关掉它用下面自己 configure 的版本,
      // 否则 "Duplicate extension names found: ['link']" 警告 + 配置可能被前者覆盖
      StarterKit.configure({ link: false }),
      Image.configure({ inline: false }),
      Link.configure({ openOnClick: false }),
      Placeholder.configure({
        placeholder: '开始写笔记…(支持 Markdown 快捷键;点麦克风可实时语音录入)',
      }),
      // ASR 实时校准:volatile 文字带这个 mark,灰色斜体显示,下次 chunk 来时
      // delete + 重新 insert 当前 hypothesis 的新 volatile
      AsrVolatileMark,
    ],
    content: initial?.content ?? '',
    onUpdate({ editor }) {
      // 笔记内容变化时,summary 跟着一起保存(避免丢)
      draft.save(title, editor.getJSON(), summaryHook.summary || undefined);
    },
  });

  // summary 变了也写草稿(用最新 editor json + title)
  React.useEffect(() => {
    if (editor) draft.save(title, editor.getJSON(), summaryHook.summary || undefined);
    // editor 是稳定 ref,主要靠 summary 变化触发
  }, [summaryHook.summary, editor, title, draft]);

  const onGenerateSummary = React.useCallback(async () => {
    if (!editor) return;
    setSummaryOpen(true);
    await summaryHook.generate(editor.getJSON(), { title });
  }, [editor, title, summaryHook]);

  // 标题变化也写草稿(连带 summary 一起,防止改标题时丢摘要)
  React.useEffect(() => {
    if (editor) draft.save(title, editor.getJSON(), summaryHook.summary || undefined);
  }, [title, editor, draft, summaryHook.summary]);

  // ASR 新模式:不实时转,**录完整段送模型**(避免 chunk 边界 hallucination)。
  // 录音过程只看频谱条 + 时长(RecorderBar),停止后 onRecorded 触发整段转写。
  const [transcribingRecording, setTranscribingRecording] = React.useState(false);
  const mic = useMicSimpleRecorder({
    onRecorded: async (blob, mimeType) => {
      if (!editor) return;
      setTranscribingRecording(true);
      try {
        // 整段送 modality.transcribeFile — 比逐 chunk transcribe 准确得多,
        // whisper 内部 VAD 在整段上工作正常,不会 hallucinate "謝謝" 之类。
        // 给文件起个名好让后端 _guess_audio_ext 选对扩展。
        const ext = mimeType.includes('webm') ? 'webm' : 'wav';
        const filename = `recording-${Date.now()}.${ext}`;
        const file = new File([blob], filename, { type: mimeType });
        console.debug(`[mic-simple] 整段送转 ${filename} (${blob.size} bytes, ${mimeType})`);
        const r = await modality.transcribeFile(file, { language: 'zh' });
        if (!r.text) {
          const detail = r.diagnostics || '未识别到任何语音。检查:① 麦克风音量;② ASR 服务可用性(设置 → 本地模型服务);③ 是否真的有说话';
          notifyInfo('未识别到语音', detail, 12000);
          return;
        }
        // **前端强转简/繁兜底** — 后端 zhconv 没装时还能拿到目标形态输出。
        // opencc-js 词组级转换,准确度高于后端 zhconv 字级。chineseScript 默认
        // 跟随系统语言(navigator.language),用户在简/繁按钮切过的话用持久化值。
        const convertedText = convertText(r.text, chineseScript);
        // 拆 paragraph node 插入末尾(避免 insertContent(string) 走 HTML 解析吃换行)
        const lines = convertedText.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        const nodes = lines.map((line) => ({
          type: 'paragraph' as const,
          content: [{ type: 'text' as const, text: line }],
        }));
        if (nodes.length > 0) {
          editor.chain().focus('end').insertContent(nodes).run();
        }
        notifySuccess(
          '录音转写完成',
          `${convertedText.length} 字 · ${(blob.size / 1024).toFixed(0)} KB`,
          4000,
        );
        // 记下这次录音,允许"重新转文字"重跑
        setLastUploadedAudio(file);
      } catch (e) {
        reportError(e, '录音转写失败');
      } finally {
        setTranscribingRecording(false);
      }
    },
    onError: (err) => reportError(err, '录音失败'),
  });

  // 简体 / 繁体切换 — 把整个 editor 文档过一遍 opencc-js,setContent 重建。
  // 默认 'cn'(笔记主诉求简体);后端 ASR sidecar 有时返繁体,用户点 简 修正。
  // setContent 会重置 cursor;能接受。volatile mark 内的文字也会被转换,体验自然。
  // 默认跟随系统语言:navigator.language 含 Hant/TW/HK/MO → 'tw',否则 'cn'。
  // 用户在 UI 上点过简/繁切换会持久化到 localStorage,detectChineseScript 优先读它。
  const [chineseScript, setChineseScript] = React.useState<ChineseScript>(() => detectChineseScript());
  const applyChineseScript = React.useCallback((target: ChineseScript) => {
    if (!editor) return;
    if (target === chineseScript) return;
    const json = editor.getJSON();
    const converted = convertEditorChinese(json, target);
    editor.commands.setContent(converted as Parameters<typeof editor.commands.setContent>[0], {
      emitUpdate: true,
    });
    setChineseScript(target);
    persistChineseScript(target);  // 记住用户选择,下次启动按此默认
  }, [editor, chineseScript]);

  // 上传语音文件转写
  const [uploadingFile, setUploadingFile] = React.useState(false);
  // 最近一次上传的语音文件 — 用户可点 header 上的「重新转文字」用它再跑一次,
  // 不用再选文件。File 对象在浏览器内存里,session 关闭就丢(可接受 — 这不是
  // 持久化"附件",而是"刚上传的那个文件,允许重转"的纯 UX 便捷)。
  const [lastUploadedAudio, setLastUploadedAudio] = React.useState<File | null>(null);

  // 转写 + 插入逻辑提到独立 callback,onUploadAudio / onRetranscribe 共用
  const doTranscribeAndInsert = React.useCallback(async (file: File) => {
    if (!editor) return;
    setUploadingFile(true);
    try {
      const r = await modality.transcribeFile(file, { language: 'zh' });
      if (!r.text) {
        const detail = r.diagnostics || '后端未返回任何文本。检查:① ASR 服务是否就绪;② 音频是否包含可识别语音;③ 设置 → 本地模型服务 → 调用日志';
        notifyInfo('未识别到语音文字', detail, 15000);
        return;
      }
      // 关键:Tiptap insertContent(string) 把 string 当 HTML 解析,'\n' 不被
      // 识别为换行 → 多段文字会挤成一坨,甚至 '<','>' 等字符被当 tag 吃掉。
      // 改用 **ProseMirror JSON nodes**,每行一个 paragraph,Tiptap 直接构造
      // 文档结构,无 HTML 解析、无字符吞噬。
      const rawLines: string[] = [];
      if (r.speaker_diarization_available && r.segments.length > 1) {
        const speakerLabels = new Map<string, string>();
        let next = 0;
        for (const s of r.segments) {
          if (!speakerLabels.has(s.speaker)) {
            speakerLabels.set(s.speaker, String.fromCharCode(65 + next));
            next += 1;
          }
        }
        for (const s of r.segments) {
          const label = speakerLabels.get(s.speaker) ?? '?';
          rawLines.push(`Speaker ${label}:${s.text.replace(/\r?\n+/g, ' ')}`);
        }
      } else {
        rawLines.push(r.text.replace(/\r?\n+/g, ' '));
      }
      // **前端强转目标中文形态** — 跟 onRecorded 一样,后端 zhconv 没装 / 出繁体
      // 时这里兜底转。chineseScript 默认跟随系统语言。
      const lines = rawLines.map((line) => convertText(line, chineseScript));
      // 把每行包成 paragraph node 数组,一次性 insert
      const nodes = lines
        .filter((l) => l.length > 0)
        .map((line) => ({
          type: 'paragraph' as const,
          content: [{ type: 'text' as const, text: line }],
        }));
      console.debug(
        `[upload-asr] ${lines.length} lines, total ${r.text.length} chars,` +
          ` diarization=${r.speaker_diarization_available}`,
      );
      if (nodes.length > 0) {
        editor.chain().focus('end').insertContent(nodes).run();
      }
      const meta = r.speaker_diarization_available
        ? `共 ${r.segments.length} 段,识别到 ${new Set(r.segments.map((s) => s.speaker)).size} 个说话人`
        : `共 ${r.text.length} 字`;
      notifySuccess('录音转写完成', meta, 4000);
      // 记下这次文件,header 上会出现「重新转文字」芯片(用户可点再跑一遍,
      // 比如 ASR 服务刚刚 fail / 想换语言重转 / 想验证 sidecar 修好了)
      setLastUploadedAudio(file);
    } catch (e) {
      reportError(e, '上传录音转写失败');
    } finally {
      setUploadingFile(false);
    }
  }, [editor, chineseScript]);

  const onUploadAudio = React.useCallback(async () => {
    if (!editor || uploadingFile) return;
    let file: File | null = null;
    try {
      const picked = await getPlatform().fs.pickFiles({
        multiple: false,
        accept: 'audio/*',
      });
      if (!picked.length) return;
      file = picked[0] as File;
    } catch (e) {
      reportError(e, '选择文件失败');
      return;
    }
    await doTranscribeAndInsert(file);
  }, [editor, uploadingFile, doTranscribeAndInsert]);

  const onRetranscribe = React.useCallback(async () => {
    if (!editor || uploadingFile || !lastUploadedAudio) return;
    await doTranscribeAndInsert(lastUploadedAudio);
  }, [editor, uploadingFile, lastUploadedAudio, doTranscribeAndInsert]);

  // Ctrl/Cmd+S 触发保存
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        setSaveOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  if (!editor) return null;

  return (
    <div className="flex h-full bg-[var(--cy-surface-base)]">
      <div className="flex h-full flex-1 flex-col min-w-0">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--cy-border-subtle)] px-4 py-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={defaultTitle()}
          className="flex-1 min-w-[200px] border-0 bg-transparent text-base font-semibold focus:outline-none"
        />
        <div
          className="inline-flex h-8 items-center rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-0.5 text-xs"
          role="group"
          aria-label="简体繁体切换"
        >
          <button
            type="button"
            onClick={() => applyChineseScript('cn')}
            className={cn(
              'flex h-7 items-center justify-center rounded px-2 transition',
              chineseScript === 'cn'
                ? 'bg-[var(--cy-brand-500)] text-white shadow-sm'
                : 'text-[var(--cy-text-secondary)] hover:text-[var(--cy-text-primary)]',
            )}
            title="把整个笔记转为简体(含正在录的语音文字)"
            aria-pressed={chineseScript === 'cn'}
          >
            简
          </button>
          <button
            type="button"
            onClick={() => applyChineseScript('tw')}
            className={cn(
              'flex h-7 items-center justify-center rounded px-2 transition',
              chineseScript === 'tw'
                ? 'bg-[var(--cy-brand-500)] text-white shadow-sm'
                : 'text-[var(--cy-text-secondary)] hover:text-[var(--cy-text-primary)]',
            )}
            title="把整个笔记转为繁体(台湾标准)"
            aria-pressed={chineseScript === 'tw'}
          >
            繁
          </button>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => (mic.recording ? mic.stop() : void mic.start())}
          className={cn('h-8 text-xs', mic.recording && 'bg-red-500/15 text-red-600')}
          title={mic.recording ? `录音中 · ${mic.elapsed}s — 点击停止` : '语音录入'}
        >
          {mic.recording ? (
            <><MicOff className="h-3.5 w-3.5 animate-pulse" /> {mic.elapsed}s</>
          ) : (
            <><Mic className="h-3.5 w-3.5" /> 语音</>
          )}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void onUploadAudio()}
          disabled={uploadingFile || mic.recording}
          className="h-8 text-xs"
          title="上传录音文件 — 整段转写为文字插入笔记。说话人区分(diarization)暂未启用,所有内容归一个说话人"
        >
          {uploadingFile ? (
            <><Loader2 className="h-3.5 w-3.5 animate-spin" /> 转写中…</>
          ) : (
            <><FileAudio className="h-3.5 w-3.5" /> 上传录音</>
          )}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void onGenerateSummary()}
          disabled={summaryHook.loading || !editor}
          className="h-8 text-xs"
          title={
            summaryHook.summary
              ? '重新生成 AI 摘要(覆盖现有,右侧面板展示)'
              : 'AI 根据笔记内容自动生成 1-2 句摘要 + 3-5 个要点(右侧面板)'
          }
        >
          {summaryHook.loading ? (
            <><Loader2 className="h-3.5 w-3.5 animate-spin" /> 生成中</>
          ) : (
            <><Sparkles className="h-3.5 w-3.5" /> AI 摘要</>
          )}
        </Button>
        <Button size="sm" onClick={() => setSaveOpen(true)} className="h-8 text-xs">
          <Save className="h-3.5 w-3.5" /> 保存到知识中心
        </Button>
      </header>

      {lastUploadedAudio && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]/50 px-4 py-1.5 text-xs">
          <span
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-1 text-[var(--cy-text-secondary)]"
            title={`本次会话刚上传的录音文件 · ${(lastUploadedAudio.size / 1024).toFixed(0)} KB`}
          >
            <Paperclip className="h-3 w-3" />
            <span className="max-w-[240px] truncate font-mono text-[11px]">{lastUploadedAudio.name}</span>
          </span>
          <button
            type="button"
            onClick={() => void onRetranscribe()}
            disabled={uploadingFile || mic.recording}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-1 text-[var(--cy-brand-700)] hover:bg-[var(--cy-brand-500)]/10 disabled:opacity-50"
            title="重新对该录音跑一次 ASR,新文字会接到文档末尾(便于 sidecar 修好后重试 / 验证)"
          >
            {uploadingFile ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            重新转文字
          </button>
          <button
            type="button"
            onClick={() => setLastUploadedAudio(null)}
            disabled={uploadingFile}
            className="inline-flex items-center justify-center rounded-md p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)] disabled:opacity-50"
            title="清掉这个语音附件(只移除芯片,不影响已插入的文字)"
            aria-label="清除附件"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

      <Toolbar editor={editor} />

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-3xl">
          <EditorContent
            editor={editor}
            className="prose prose-sm dark:prose-invert max-w-none min-h-[60vh] focus:outline-none [&_.is-editor-empty]:before:content-[attr(data-placeholder)] [&_.is-editor-empty]:before:text-[var(--cy-text-tertiary)] [&_.is-editor-empty]:before:float-left [&_.is-editor-empty]:before:h-0 [&_.is-editor-empty]:before:pointer-events-none [&_*:focus]:outline-none"
          />
        </div>
      </div>

      <RecorderBar
        recording={mic.recording}
        paused={mic.paused}
        elapsedSec={mic.elapsed}
        stream={mic.stream}
        transcribing={transcribingRecording}
        onPause={mic.pause}
        onResume={mic.resume}
        onStop={mic.stop}
        onCancel={mic.cancel}
      />
      </div>{/* /editor column */}

      <SummaryPanel
        open={summaryOpen}
        summary={summaryHook.summary}
        loading={summaryHook.loading}
        error={summaryHook.error}
        onClose={() => setSummaryOpen(false)}
        onRegenerate={() => void onGenerateSummary()}
      />

      <SaveNoteDialog
        open={saveOpen}
        title={title}
        content={editor.getJSON()}
        summary={summaryHook.summary}
        defaultKbName={defaultKb}
        onClose={() => setSaveOpen(false)}
        onSaved={(kbName) => {
          draft.clear();
          setSaveOpen(false);
          onSaved?.(kbName);
        }}
      />
    </div>
  );
};

const ToolBtn: React.FC<{
  onClick(): void; active?: boolean; title: string; children: React.ReactNode;
}> = ({ onClick, active, title, children }) => (
  <button
    type="button"
    onClick={onClick}
    title={title}
    className={cn(
      'inline-flex h-7 w-7 items-center justify-center rounded text-[var(--cy-text-secondary)] transition',
      'hover:bg-[var(--cy-surface-1)] hover:text-[var(--cy-text-primary)]',
      active && 'bg-[var(--cy-brand-500)]/15 text-[var(--cy-brand-700)]',
    )}
  >
    {children}
  </button>
);

const Toolbar: React.FC<{ editor: Editor }> = ({ editor }) => (
  <div className="flex flex-wrap items-center gap-0.5 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-1">
    <ToolBtn onClick={() => editor.chain().focus().toggleBold().run()}
             active={editor.isActive('bold')} title="加粗 Ctrl+B"><Bold className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleItalic().run()}
             active={editor.isActive('italic')} title="斜体"><Italic className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleStrike().run()}
             active={editor.isActive('strike')} title="删除线"><Strikethrough className="h-3.5 w-3.5" /></ToolBtn>
    <span className="mx-1 h-4 w-px bg-[var(--cy-border-subtle)]" />
    <ToolBtn onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
             active={editor.isActive('heading', { level: 1 })} title="标题 1"><Heading1 className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
             active={editor.isActive('heading', { level: 2 })} title="标题 2"><Heading2 className="h-3.5 w-3.5" /></ToolBtn>
    <span className="mx-1 h-4 w-px bg-[var(--cy-border-subtle)]" />
    <ToolBtn onClick={() => editor.chain().focus().toggleBulletList().run()}
             active={editor.isActive('bulletList')} title="无序列表"><List className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleOrderedList().run()}
             active={editor.isActive('orderedList')} title="有序列表"><ListOrdered className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleBlockquote().run()}
             active={editor.isActive('blockquote')} title="引用"><Quote className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn onClick={() => editor.chain().focus().toggleCodeBlock().run()}
             active={editor.isActive('codeBlock')} title="代码块"><Code className="h-3.5 w-3.5" /></ToolBtn>
    <span className="mx-1 h-4 w-px bg-[var(--cy-border-subtle)]" />
    <ToolBtn
      onClick={() => {
        const href = prompt('链接地址(https://…)') ?? '';
        if (!href) return;
        editor.chain().focus().setLink({ href }).run();
      }}
      active={editor.isActive('link')}
      title="插入链接"><LinkIcon className="h-3.5 w-3.5" /></ToolBtn>
    <ToolBtn
      onClick={() => {
        const src = prompt('图片 URL') ?? '';
        if (!src) return;
        editor.chain().focus().setImage({ src }).run();
      }}
      title="插入图片"><ImageIcon className="h-3.5 w-3.5" /></ToolBtn>
  </div>
);
