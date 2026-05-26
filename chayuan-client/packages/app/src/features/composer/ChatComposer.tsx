/**
 * ChatComposer —— 站点级核心组件(参考图视觉锚点)。
 *
 * 跨页复用:首页(中心) / Chat(底部) / KB / Skill / Marketplace / AI Space。
 *
 * 视觉:
 *   - GlowFrame 霓虹外发光(粉/蓝/绿)
 *   - 顶行:模型选择 pill + 深度思考开关 pill
 *   - 中行:Textarea(IME 安全 + 自适应)
 *   - 底行:工具(剪刀/文件夹/图片)+ 麦克风;右侧黑色圆形发送按钮
 *
 * 行为:
 *   - 草稿持久化(已有 useComposerStore)
 *   - Enter 发送 / Shift+Enter 换行 / Cmd+Enter 强制发送
 *   - IME 防误发
 *   - 流式中按钮变停止
 */

import { GlowFrame, Pill, cn } from '@chayuan/ui';
import {
  ArrowUp,
  Image as ImageIcon,
  Mic,
  Paperclip,
  Scissors,
  Sparkles,
  Square,
} from 'lucide-react';
import * as React from 'react';
import { useTranslation } from '../../i18n';
import { useComposerS, useComposerScopedStore } from '../../store/composer';
import { modality } from '@chayuan/api';
import { classifyModalityCapability } from '@chayuan/transport';
import { getComposerMode } from './composerMode';
import { ComposerModeBadge } from './ComposerModeBadge';
import { ImageGenParamBar } from './ImageGenParamBar';
import { VideoGenParamBar } from './VideoGenParamBar';
import { TtsVoiceParamBar } from './TtsVoiceParamBar';
import { McpPickerPill, ToolsPickerPill } from './CapabilityPickers';
import { ComposerModelPill } from './ComposerModelPill';
import { KnowledgePickerPill, type KnowledgePickerPillProps } from './KnowledgePickerPill';
import { SearchModePill } from './SearchModePill';
import { useMicSimpleRecorder } from './useMicSimpleRecorder';
import { ChatRecorderPanel } from './ChatRecorderPanel';
import { pushVoiceAttachment } from './voiceAttachments';
import { reportError } from '../../store/errorDialog';
import { convertText, detectChineseScript } from '../notes/chineseScript';
import type { ComposerModelPillProps } from './ComposerModelPill';

export interface ChatComposerProps {
  isStreaming: boolean;
  placeholder?: string;
  onSend(content: string): void | Promise<void>;
  onStop?: () => void;
  onPickFile?: () => void;
  onPickImage?: () => void;
  /** ASR 模式专用音频文件选择;不提供时 ASR 模式下 Paperclip 回退到 onPickFile */
  onPickAudio?: () => void;
  onScreenshot?: () => void;
  onMicrophone?: () => void;
  /** 顶行附加内容(附件预览 / 已选工具数等) */
  topSlot?: React.ReactNode;
  /** 放在模型选择器前的场景化控件，例如 KB 选择器。 */
  leadingControls?: React.ReactNode;
  modelPillProps?: ComposerModelPillProps;
  knowledgePillProps?: KnowledgePickerPillProps;
  showKnowledgePicker?: boolean;
  /** 是否禁用霓虹光晕(KB/Skill 等场景可降级为普通框)*/
  glow?: boolean;
  /** 自动 focus */
  autoFocus?: boolean;
  /** 强制最小高度(px) */
  minHeight?: number;
  /** 是否展示 KB 检索专属控件(精准/全面/速度) */
  showRetrievalControls?: boolean;
  /** 放在检索模式 chip 后面的场景化控件。 */
  retrievalTrailingControl?: React.ReactNode;
  /** 浮窗等窄空间隐藏通用工具/MCP,避免关键控件被挤掉。 */
  hideCapabilityControls?: boolean;
  /** 文档助手等窄空间使用图标化控件和单行工具栏。 */
  compactControls?: boolean;
}

export const ChatComposer: React.FC<ChatComposerProps> = ({
  isStreaming,
  placeholder,
  onSend,
  onStop,
  onPickFile,
  onPickImage,
  onPickAudio,
  onScreenshot,
  onMicrophone,
  topSlot,
  leadingControls,
  modelPillProps,
  knowledgePillProps,
  showKnowledgePicker = true,
  glow = true,
  autoFocus = false,
  minHeight = 72,
  showRetrievalControls = false,
  retrievalTrailingControl,
  hideCapabilityControls = false,
  compactControls = false,
}) => {
  const { t } = useTranslation();
  const draft = useComposerS((s) => s.draft);
  const setDraft = useComposerS((s) => s.setDraft);
  const deepThink = useComposerS((s) => s.deepThink);
  const toggleDeepThink = useComposerS((s) => s.toggleDeepThink);
  const modelId = useComposerS((s) => s.modelId);
  const selectedKuCount = useComposerS((s) => s.selectedKuIds.length);
  // 检查当前附件中有几张图,给 image_edit 模式判"必填参考图"用
  const imageAttachmentCount = useComposerS((s) => s.attachments.filter((a) => a.type === 'image').length);
  // ASR 模式判"必填音频"用
  const audioAttachmentCount = useComposerS((s) => s.attachments.filter((a) => a.type === 'audio').length);
  const taRef = React.useRef<HTMLTextAreaElement>(null);
  const composingRef = React.useRef(false);
  // 用 store 实例引用,避免 onPartial 回调中的 stale closure
  const composerStore = useComposerScopedStore();

  // 录音 + 转写 → 发送:整段录完送 transcribe-file,准确率远高于实时分片。
  // 流程:用户点麦 → 录音面板出现 → 录制中可暂停/继续/取消 → 点「发送」→
  //   stop MediaRecorder → onRecorded 给完整 webm Blob →
  //   transcribeFile 得到文本 → setDraft(text) + submit → push blob 到
  //   voiceAttachments(MessageBubble 挂载用户消息时 consume 渲染播放按钮)
  const [recorderTranscribing, setRecorderTranscribing] = React.useState(false);
  // pending blob ref:onRecorded 拿到 blob 后,需要在 transcribe + submit 完成后
  // 再 push 到 voiceAttachments,使队列 push 时机 ≤ MessageBubble 挂载新消息的时机
  const pendingSendRef = React.useRef(false);

  const mic = useMicSimpleRecorder({
    onRecorded: async (blob, _mimeType) => {
      const shouldSend = pendingSendRef.current;
      pendingSendRef.current = false;
      if (!shouldSend) return;  // 用户走 cancel 路径,不送
      setRecorderTranscribing(true);
      try {
        const ext = _mimeType.includes('webm') ? 'webm' : 'wav';
        const file = new File([blob], `chat-recording-${Date.now()}.${ext}`, { type: _mimeType });
        const r = await modality.transcribeFile(file, { language: 'zh' });
        const rawText = (r.text ?? '').trim();
        if (!rawText) {
          reportError(new Error(r.diagnostics || '未识别到任何语音'), '录音转写失败');
          return;
        }
        // **强转中文形态跟随系统语言** — 后端 zhconv 没装时 whisper 输出繁体直
        // 接落到对话框,跟 NoteEditor 一致用 opencc-js 兜底。detectChineseScript
        // 优先读 localStorage(用户在笔记里点过简/繁),否则读 navigator.language。
        const text = convertText(rawText, detectChineseScript());
        // 文字先填进 draft 让用户看见;然后压队,最后触发 submit
        composerStore.getState().setDraft(text);
        pushVoiceAttachment(text, blob);  // 在 submit 之前 push,MessageBubble 挂载时能 consume 到
        // 直接调 submit(用 ref 拿到内层 submit 句柄)
        await submitRef.current?.();
      } catch (e) {
        reportError(e, '录音发送失败');
      } finally {
        setRecorderTranscribing(false);
      }
    },
    onError: (err) => reportError(err, '录音失败'),
  });

  const handleMicClick = () => {
    // 外部传了 onMicrophone 时优先用外部(允许 caller 覆盖)
    if (onMicrophone) {
      onMicrophone();
      return;
    }
    if (!mic.recording) void mic.start();
    // 录音中再点 mic 按钮不做事;面板上自己有 暂停/取消/发送 三个按钮
  };

  const handleRecorderSubmit = () => {
    if (recorderTranscribing) return;
    pendingSendRef.current = true;
    mic.stop();  // 触发 onRecorded → 走 transcribe + submit
  };
  const handleRecorderCancel = () => {
    pendingSendRef.current = false;
    mic.cancel();
  };

  // 根据当前选中模型推 capability,联动 UI:
  //   - badge:输入框顶上一颗 pill 告诉用户当前模式(对话/文生图/语音合成 …)
  //   - placeholder:不同模式不同提示文案
  //   - submitLabel:发送按钮文案("生成图像 ✨" / "合成语音 🔊" …)
  // chat 模式即默认值,所有现有行为(KB / 工具 / 普通对话)完全不变。
  const composerMode = React.useMemo(() => {
    const cap = classifyModalityCapability(modelId);
    return getComposerMode(cap);
  }, [modelId]);
  const ph =
    placeholder
    ?? (composerMode.capability === 'chat'
          ? t('chat.inputPlaceholder', { model: modelId })
          : composerMode.placeholder);

  React.useEffect(() => {
    if (autoFocus) taRef.current?.focus();
  }, [autoFocus]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: draft changes must recalc textarea height.
  React.useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.max(minHeight - 24, Math.min(el.scrollHeight, 220))}px`;
  }, [draft, minHeight]);

  const submit = async () => {
    // 从 store 实时读 draft,而不是闭包 — 录音发送链路里 setDraft(transcript)
    // 后立即调 submit,React 还没 rerender,闭包的 draft 还是旧空值会 early return。
    const text = composerStore.getState().draft.trim();
    if (isStreaming) return;
    // ASR 模式允许空 prompt(音频本身就是输入,prompt 只是可选热词);其它模式仍要求非空
    const allowEmpty = composerMode.attachmentRule === 'required-audio';
    if (!text && !allowEmpty) return;
    // 行业惯例(ChatGPT / Claude / Cursor):点发送立刻清空输入框 + 让消息进列表,
    // SSE 流式由 onSend 在后台跑。之前 await onSend 完才 setDraft 会让用户在
    // 整段流式期间看见输入框还有内容,体验割裂。
    setDraft('');
    try {
      await onSend(text);
    } catch (e) {
      // onSend 抛错(网络断 / 鉴权过期 / 后端 503)→ 把内容回填到输入框,
      // 让用户能编辑后再次发送,而不是直接丢失
      setDraft(text);
      throw e;
    }
  };
  // 暴露给录音 onRecorded 回调用 — 它在 hook 内部,不能直接读 submit 的最新闭包
  const submitRef = React.useRef<() => Promise<void>>(submit);
  submitRef.current = submit;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void submit();
      return;
    }
    if (e.key !== 'Enter') return;
    if (e.shiftKey) return;
    if (composingRef.current) return;
    e.preventDefault();
    void submit();
  };

  const Inner = (
    <div
      className={cn(
        'flex flex-col',
        compactControls ? 'p-3' : 'p-4',
        glow ? 'rounded-3xl' : 'rounded-2xl border border-[var(--cy-border-subtle)]',
      )}
    >
      {topSlot && <div className="mb-2">{topSlot}</div>}

      {/* 当前模式标识 — chat 模式默认隐藏,非 chat(文生图/语音合成/...)显示
          一颗 pill,告诉用户输入框接下来要"画什么/说什么/转写什么"。 */}
      {composerMode.capability !== 'chat' && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <ComposerModeBadge mode={composerMode} hideWhenChat={false} />
          {/* 模式专属参数条 — paramKind 决定渲染哪一个;asr_lang 留 PR-7+ */}
          {composerMode.paramKind === 'image_size_n' && <ImageGenParamBar compact={compactControls} />}
          {composerMode.paramKind === 'video_size_dur' && <VideoGenParamBar compact={compactControls} />}
          {composerMode.paramKind === 'tts_voice' && <TtsVoiceParamBar compact={compactControls} />}
        </div>
      )}

      {(mic.recording || recorderTranscribing) && (
        <ChatRecorderPanel
          recording={mic.recording}
          paused={mic.paused}
          elapsedSec={mic.elapsed}
          stream={mic.stream}
          transcribing={recorderTranscribing}
          onPause={mic.pause}
          onResume={mic.resume}
          onCancel={handleRecorderCancel}
          onSubmit={handleRecorderSubmit}
        />
      )}

      <textarea
        ref={taRef}
        value={draft}
        rows={1}
        placeholder={ph}
        className={cn(
          'w-full resize-none border-0 bg-transparent text-sm text-[var(--cy-text-primary)] placeholder:text-[var(--cy-text-tertiary)]',
          'focus-visible:outline-none',
        )}
        style={{ minHeight: minHeight - 24 }}
        onChange={(e) => setDraft(e.target.value)}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
        }}
        onKeyDown={onKeyDown}
      />

      <div className="mt-3 flex min-w-0 flex-wrap items-center justify-between gap-2 pb-0.5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          {leadingControls}
          {/* 模态模式声明 hidePills 的(t2i/t2v/tts/...)隐藏 KB 选择 —
              这些模式不走 chat 链路,KB 注入无意义。chat 模式不受影响。 */}
          {showKnowledgePicker && !composerMode.hidePills?.includes('knowledge') && (
            <KnowledgePickerPill
              {...knowledgePillProps}
              iconOnly={compactControls || knowledgePillProps?.iconOnly}
            />
          )}
          <ComposerModelPill {...modelPillProps} iconOnly={compactControls || modelPillProps?.iconOnly} />

          <Pill
            tone={deepThink ? 'ink' : 'outline'}
            size="sm"
            active={deepThink}
            onClick={toggleDeepThink}
            aria-label={t('chat.deepThink')}
            aria-pressed={deepThink}
            title={t('chat.deepThink')}
            className="h-8 w-8 shrink-0 justify-center px-0"
          >
            <Sparkles className="h-3 w-3" />
          </Pill>

          {!hideCapabilityControls && (
            <>
              {/* 能力多选,工具/MCP 各占一枚 pill;KB 不在通用对话出现 */}
              <ToolsPickerPill />
              <McpPickerPill />
            </>
          )}
          {/* 检索模式 chip:仅在知识/办公对话中显示 */}
          {showRetrievalControls && selectedKuCount > 0 && <SearchModePill forceVisible />}
          {retrievalTrailingControl}
        </div>

        <div className="flex flex-shrink-0 items-center gap-1">
          {/* 截图按钮当前隐藏(2026-05-17 用户反馈)— 系统级 overlay 链路还在
              迭代,先不暴露入口避免误用。onScreenshot prop / useChatScreenshot
              hook / platform.capture 全保留,后续接回来只要解开这段注释。 */}
          {false && onScreenshot && (
            <IconBtn aria-label={t('chat.scissors')} title={t('chat.scissors')} onClick={onScreenshot}>
              <Scissors className="h-4 w-4" />
            </IconBtn>
          )}
          <IconBtn
            aria-label={
              composerMode.attachmentRule === 'required-audio'
                ? '选择音频文件'
                : t('chat.uploadFile')
            }
            title={
              composerMode.attachmentRule === 'required-audio'
                ? '选择要转写的音频文件(mp3 / wav / m4a / ogg / opus)'
                : t('chat.uploadFile')
            }
            onClick={
              // ASR 模式优先走音频专属 picker(accept=audio/*);未提供时回退普通 file picker
              composerMode.attachmentRule === 'required-audio' && onPickAudio
                ? onPickAudio
                : onPickFile
            }
          >
            <Paperclip className="h-4 w-4" />
          </IconBtn>
          <IconBtn aria-label={t('chat.uploadImage')} title={t('chat.uploadImage')} onClick={onPickImage}>
            <ImageIcon className="h-4 w-4" />
          </IconBtn>
          <IconBtn
            aria-label={t('chat.voice')}
            title={
              mic.recording || recorderTranscribing
                ? '录音中 — 上方面板有发送/暂停/取消按钮'
                : '点击开始录音(整段录完后送模型转写发送)'
            }
            onClick={handleMicClick}
            disabled={mic.recording || recorderTranscribing}
            className={
              mic.recording || recorderTranscribing
                ? 'bg-red-500/15 text-red-600'
                : undefined
            }
          >
            <Mic className="h-4 w-4" />
          </IconBtn>

          {isStreaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label={t('chat.stop')}
              className={cn(
                'ml-2 flex h-9 w-9 items-center justify-center rounded-full',
                'bg-red-500 text-white hover:bg-red-600',
              )}
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <SendButton
              draft={draft}
              composerMode={composerMode}
              imageAttachmentCount={imageAttachmentCount}
              audioAttachmentCount={audioAttachmentCount}
              onClick={() => void submit()}
              chatSendLabel={t('chat.send')}
            />
          )}
        </div>
      </div>
    </div>
  );

  if (!glow) return Inner;
  return (
    <GlowFrame intensity="md" radius={24}>
      {Inner}
    </GlowFrame>
  );
};

interface SendButtonProps {
  draft: string;
  composerMode: ReturnType<typeof getComposerMode>;
  imageAttachmentCount: number;
  audioAttachmentCount: number;
  chatSendLabel: string;
  onClick: () => void;
}

/**
 * 发送按钮 — 把"何时禁用 / 鼠标悬浮提示什么 / 哪种模式叫什么"全收敛进来,
 * 避免 ChatComposer 主体里再写一坨条件 JSX 引起 parse 歧义。
 *
 * ASR 模式特别处理:文本框可以为空(prompt 只是给上游可选的"专业术语提示",
 * 没有它也能跑),所以不靠 draft 判禁用,只靠"是否已选音频"。
 */
const SendButton: React.FC<SendButtonProps> = ({
  draft, composerMode, imageAttachmentCount, audioAttachmentCount, chatSendLabel, onClick,
}) => {
  const needsImage =
    composerMode.attachmentRule === 'required-image' && imageAttachmentCount === 0;
  const needsAudio =
    composerMode.attachmentRule === 'required-audio' && audioAttachmentCount === 0;
  const draftEmpty = !draft.trim();
  // ASR 模式不要求 prompt;其它模式要求 prompt 非空
  const blocked = needsImage || needsAudio || (
    composerMode.attachmentRule !== 'required-audio' && draftEmpty
  );
  const title = needsImage
    ? '请先上传参考图,再点发送'
    : needsAudio
      ? '请先选择要转写的音频文件,再点发送'
      : (composerMode.capability === 'chat' ? chatSendLabel : composerMode.submitLabel);
  return (
    <button
      type="button"
      disabled={blocked}
      onClick={onClick}
      aria-label={title}
      title={title}
      className={cn(
        'ml-2 flex h-9 w-9 items-center justify-center rounded-full transition-colors',
        blocked
          ? 'cursor-not-allowed bg-[var(--cy-surface-2)] text-[var(--cy-text-tertiary)]'
          : 'bg-[var(--cy-ink-700)] text-white hover:bg-[var(--cy-ink-800)]',
      )}
    >
      <ArrowUp className="h-4 w-4" />
    </button>
  );
};

const IconBtn: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({
  className,
  ...props
}) => (
  <button
    type="button"
    className={cn(
      'flex h-8 w-8 items-center justify-center rounded-full text-[var(--cy-text-secondary)] transition-colors',
      'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      className,
    )}
    {...props}
  />
);
