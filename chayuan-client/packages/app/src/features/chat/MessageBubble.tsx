import * as React from 'react';
import { Copy, RefreshCw, ThumbsUp, ThumbsDown, ExternalLink, Pencil, GitBranch, ChevronDown, Sparkles } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { deepLinkTrace, logEvent, logScore, Events } from '@chayuan/observability';
import { getPlatform } from '@chayuan/platform-shared';
import { StreamMarkdown } from '../../lib/markdown';
import type { ChatMessage, ChatCitation } from './useChayuanChat';
import { ToolCallsRow } from './toolcards/ToolCallsRow';
import { chatCitationsToSources } from '@chayuan/api';
import { KbResultsView } from '../kb/components/KbResultsView';
import { consumeVoiceAttachment } from '../composer/voiceAttachments';
import { VoiceMessagePlayer } from './VoiceMessagePlayer';
import { ImageCard } from './parts/ImageCard';
import { TaskProgressPart } from './parts/TaskProgressPart';

export interface MessageBubbleProps {
  message: ChatMessage;
  onRegenerate?: () => void;
  onResume?: (approved: boolean) => void;
  onEdit?: (id: string, newContent: string) => void;
  onBranch?: (id: string) => void;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message, onRegenerate, onResume, onEdit, onBranch }) => {
  const isUser = message.role === 'user';
  const [feedback, setFeedback] = React.useState<1 | -1 | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(message.content);
  React.useEffect(() => setDraft(message.content), [message.content]);

  // 检查这条用户消息是不是 ChatComposer 录音发送过来的 — 用 content 匹配
  // voiceAttachments 队列;命中拿 Blob 给 <audio> 播。
  //
  // ⚠ React StrictMode 坑(上轮修复未完全):dev 模式 useEffect mount → cleanup
  //   → mount 双跑;**任何**绑在 effect cleanup 里的 revoke 都会在第二次 mount
  //   前把 URL 销毁。上轮把 revoke 拆到第二个空-deps effect 仍逃不掉 — 那个
  //   effect 同样会 mount→cleanup→mount,cleanup 时 revoke audioUrlRef → 失效。
  //
  // 正解:用 blobRef 持有 Blob;**每次 mount 都 createObjectURL 一次**,
  //   cleanup 时 revoke 这次创建的 URL。strict-mode 第二次 mount 拿到新 URL
  //   替换 state 里被 revoke 的旧值,audio 元素 src 始终有效。consumedRef
  //   保证 Blob 只从队列 consume 一次。
  const consumedRef = React.useRef<string | null>(null);
  const blobRef = React.useRef<Blob | null>(null);
  const [audioUrl, setAudioUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!isUser) return;
    if (consumedRef.current !== message.id) {
      blobRef.current = consumeVoiceAttachment(message.content);
      consumedRef.current = message.id;
    }
    if (!blobRef.current) return;
    const url = URL.createObjectURL(blobRef.current);
    setAudioUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [message.id, isUser, message.content]);

  const onCopy = async () => {
    await getPlatform().clipboard.writeText(message.content);
  };

  const submitFeedback = async (value: 1 | -1) => {
    if (!message.traceId) return;
    setFeedback(value);
    logEvent(Events.ChatFeedback, { traceId: message.traceId, metadata: { value } });
    await logScore({
      traceId: message.traceId,
      messageId: message.id,
      name: 'user_feedback',
      value,
    });
  };

  const openTrace = async () => {
    if (!message.traceId) return;
    const url = deepLinkTrace(message.traceId);
    if (url) await getPlatform().shell.openExternal(url);
  };

  return (
    <div className={cn('flex gap-3 py-3', isUser && 'flex-row-reverse')}>
      <div
        className={cn(
          'relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-xs font-semibold',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-gradient-to-br from-purple-500 to-indigo-600 text-white',
        )}
      >
        {!isUser && message.streaming && (
          <span className="absolute inset-0 animate-ping rounded-full bg-indigo-400/35" />
        )}
        <span className="relative">{isUser ? 'U' : 'AI'}</span>
      </div>
      <div className={cn('flex max-w-[80%] flex-col gap-2', isUser && 'items-end')}>
        {!isUser && message.citations?.length ? (
          <CitationsRow citations={message.citations} streaming={!!message.streaming} />
        ) : null}

        {!isUser && message.mounted && Number(message.mounted.mount_count ?? 0) > 0 ? (
          <MountedContextPill summary={message.mounted} sources={message.mountedSources ?? []} />
        ) : null}

        {message.reasoning && (
          <details className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground" open={!!message.streaming}>
            <summary className="cursor-pointer select-none">思考过程</summary>
            <div className="mt-2 whitespace-pre-wrap leading-relaxed">{message.reasoning}</div>
          </details>
        )}
        {message.toolCalls?.length ? (
          <ToolCallsRow toolCalls={message.toolCalls} streaming={!!message.streaming} />
        ) : null}

        {message.error ? (
          <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{message.error}</div>
        ) : editing ? (
          <div className={cn('flex flex-col gap-2 rounded-2xl border bg-card p-2 shadow-sm', isUser && 'items-end')}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="min-h-20 w-full resize-y rounded-md border bg-background p-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              autoFocus
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => {
                  setEditing(false);
                  if (draft.trim() && draft !== message.content) onEdit?.(message.id, draft);
                }}
              >
                保存并重发
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(message.content); }}>
                取消
              </Button>
            </div>
          </div>
        ) : (
          <div
            className={cn(
              'rounded-2xl px-4 py-2 text-sm leading-relaxed',
              isUser ? 'bg-primary text-primary-foreground' : 'bg-card shadow-sm',
            )}
          >
            {!message.content && !message.files?.length && !message.taskProgress && message.streaming ? (
              <TypingIndicator />
            ) : (
              <StreamMarkdown content={message.content || ''} streaming={message.streaming} />
            )}
            {/* 模态路由异步任务进度条 — 仅在尚未完成 + 还在 streaming 时展示;
                完成后(percent=100 且有 files 落地)或非 streaming 时收起 */}
            {message.taskProgress
              && message.streaming
              && message.taskProgress.percent < 100 && (
                <div className="mt-2">
                  <TaskProgressPart
                    percent={message.taskProgress.percent}
                    message={message.taskProgress.message}
                    eta_s={message.taskProgress.eta_s}
                    task_id={message.taskProgress.task_id}
                  />
                </div>
              )}
            {audioUrl && <VoiceMessagePlayer audioUrl={audioUrl} />}
            {/* 模态路由生成产物(t2i/t2v/tts 等)— 按 mediaType 分发渲染。
                老消息 files 字段为 undefined,什么都不渲染,完全向后兼容。 */}
            {message.files && message.files.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {message.files.map((f, i) => {
                  if (f.mediaType.startsWith('image/')) {
                    return (
                      <ImageCard
                        key={f.url + i}
                        url={f.url}
                        mediaType={f.mediaType}
                        metadata={f.metadata}
                      />
                    );
                  }
                  if (f.mediaType.startsWith('audio/')) {
                    // TTS / 模态生成的音频:带说明小字(音色 / 模型 / 字数),
                    // 便于用户区分"这是哪段合成"。
                    const voice = typeof f.metadata?.voice === 'string' ? f.metadata.voice : null;
                    const model = typeof f.metadata?.model === 'string' ? f.metadata.model : null;
                    const speed = typeof f.metadata?.speed === 'number' ? f.metadata.speed : null;
                    const chars = typeof f.metadata?.text_chars === 'number' ? f.metadata.text_chars : null;
                    return (
                      <div key={f.url + i} className="flex flex-col gap-0.5">
                        <audio src={f.url} controls className="max-w-full rounded-md" />
                        {(voice || model || chars) && (
                          <div className="flex flex-wrap gap-x-2 text-[10px] leading-tight text-[var(--cy-text-tertiary)]">
                            {model && <span>{model}</span>}
                            {voice && <span>音色:{voice}</span>}
                            {speed != null && Math.abs(speed - 1.0) > 1e-3 && (
                              <span>语速:{speed.toFixed(2)}×</span>
                            )}
                            {chars && <span>{chars} 字</span>}
                          </div>
                        )}
                      </div>
                    );
                  }
                  if (f.mediaType.startsWith('video/')) {
                    return (
                      <video
                        key={f.url + i}
                        src={f.url}
                        controls
                        className="max-w-[480px] rounded-md"
                      />
                    );
                  }
                  // 其它类型(PDF 等):简单下载链接兜底
                  return (
                    <a
                      key={f.url + i}
                      href={f.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-blue-600 underline"
                    >
                      下载附件 ({f.mediaType})
                    </a>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {message.interrupt && onResume && (
          <div className="rounded-lg border-2 border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950">
            <p className="font-medium">需要您的批准</p>
            <p className="mt-1 text-xs text-muted-foreground">{message.interrupt.reason ?? '即将执行需要确认的操作'}</p>
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={() => onResume(true)}>
                批准
              </Button>
              <Button size="sm" variant="outline" onClick={() => onResume(false)}>
                拒绝
              </Button>
            </div>
          </div>
        )}

        {isUser && !message.streaming && onEdit && (
          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 [.message-bubble:hover_&]:opacity-100">
            <Button variant="ghost" size="icon" onClick={() => setEditing(true)} aria-label="编辑并重发">
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onCopy} aria-label="复制">
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}

        {!isUser && !message.streaming && (
          <div className="flex items-center gap-1 opacity-0 transition-opacity hover:opacity-100 group-hover:opacity-100 [.message-bubble:hover_&]:opacity-100">
            <Button variant="ghost" size="icon" onClick={onCopy} aria-label="复制">
              <Copy className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onRegenerate} aria-label="重新生成">
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            {onBranch && (
              <Button variant="ghost" size="icon" onClick={() => onBranch(message.id)} aria-label="从此分叉">
                <GitBranch className="h-3.5 w-3.5" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void submitFeedback(1)}
              aria-label="赞"
              className={cn(feedback === 1 && 'text-primary')}
            >
              <ThumbsUp className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void submitFeedback(-1)}
              aria-label="踩"
              className={cn(feedback === -1 && 'text-destructive')}
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </Button>
            {message.traceId && (
              <Button variant="ghost" size="icon" onClick={() => void openTrace()} aria-label="查看 trace">
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

const MountedContextPill: React.FC<{
  summary: NonNullable<ChatMessage['mounted']>;
  sources: NonNullable<ChatMessage['mountedSources']>;
}> = ({ summary, sources }) => {
  const [open, setOpen] = React.useState(false);
  const mountCount = Number(summary.mount_count ?? 0) || 0;
  const fewshotCount = Number(summary.fewshot_count ?? 0) || 0;
  const boostCount = Number(summary.boost_count ?? 0) || 0;
  const safetyCount = Number(summary.safety_rule_count ?? 0) || 0;
  return (
    <div className="max-w-full rounded-xl border border-purple-200/70 bg-purple-50/70 px-3 py-2 text-xs text-purple-900 dark:border-purple-800 dark:bg-purple-950/40 dark:text-purple-100">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <Sparkles className="h-3.5 w-3.5 shrink-0" />
        <span className="font-medium">已使用训练数据挂载</span>
        <span className="text-purple-700 dark:text-purple-200">
          {mountCount} 个挂载 · 样例 {fewshotCount} · 排序信号 {boostCount} · 规则 {safetyCount}
        </span>
        <ChevronDown className={cn('ml-auto h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
      </button>
      {open ? (
        <div className="mt-2 space-y-1 border-t border-purple-200/70 pt-2 text-[11px] text-purple-800 dark:border-purple-800 dark:text-purple-100">
          {sources.length ? (
            sources.slice(0, 6).map((source, idx) => (
              <div key={`${source.mount_id || idx}-${source.artifact_type || ''}`} className="flex flex-wrap gap-1">
                <span className="font-medium">{String(source.name || source.mount_id || `挂载 ${idx + 1}`)}</span>
                {source.artifact_type ? <span>· {String(source.artifact_type)}</span> : null}
                {Array.isArray(source.sample_ids) && source.sample_ids.length ? (
                  <span>· 样本 {source.sample_ids.slice(0, 3).join(', ')}</span>
                ) : null}
              </div>
            ))
          ) : (
            <div>训练数据中心的已发布偏好、样例或排序信号已参与本次回答。</div>
          )}
        </div>
      ) : null}
    </div>
  );
};

/**
 * 引用源渲染:把 citations 拆成两组渲染
 *   - KB 文件(kb_name + file_name 都非空)→ 可预览的文件 chip,点击开右侧 PreviewPanel
 *   - URL / 其它 → 老样式外链 chip
 *
 * 复用要点:同一文件可能产生多条 citation(不同 chunk),前端去重以"kb_name/file_name"
 * 为 key,显示成单个 chip 上的 "命中 N 段" 标签;score 取最高。
 */
const TypingIndicator: React.FC = () => (
  <span className="inline-flex items-center gap-1 py-1" aria-label="AI 正在思考">
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        className="h-1.5 w-1.5 rounded-full bg-indigo-500"
        style={{
          animation: 'cyTypingDot 1s ease-in-out infinite',
          animationDelay: `${i * 140}ms`,
        }}
      />
    ))}
    <style>{`@keyframes cyTypingDot{0%,80%,100%{opacity:.35;transform:translateY(0) scale(.9)}40%{opacity:1;transform:translateY(-3px) scale(1)}}`}</style>
  </span>
);

const CitationsRow: React.FC<{ citations: ChatCitation[]; streaming: boolean }> = ({ citations, streaming }) => {
  const sources = React.useMemo(() => chatCitationsToSources(citations), [citations]);
  if (sources.length === 0) return null;
  return <KbResultsView sources={sources} streaming={streaming} />;
};
