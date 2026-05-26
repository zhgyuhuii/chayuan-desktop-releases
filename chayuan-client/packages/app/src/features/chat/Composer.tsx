import * as React from 'react';
import { ArrowUp, Sparkles, Square, Paperclip, Image as ImageIcon } from 'lucide-react';
import { Button, Textarea, cn } from '@chayuan/ui';
import { useTranslation } from '../../i18n';
import { useComposerStore, useComposerS } from '../../store/composer';

export interface ComposerProps {
  isStreaming: boolean;
  placeholder?: string;
  onSend(content: string): void | Promise<void>;
  onStop?: () => void;
  onPickFile?: () => void;
  onPickImage?: () => void;
  onDrop?: (e: React.DragEvent) => void;
  /** 顶部插槽：附件预览 / 能力卡选择数 / 模式提示等 */
  topSlot?: React.ReactNode;
}

/**
 * AI 客户端复合输入区。
 *
 * 关键点：
 * - IME 防误发（compositionstart/end）
 * - 自适应高度（最大 200px）
 * - 草稿持久化（zustand persist）
 * - Cmd/Ctrl + Enter 强制发送、Enter 发送、Shift + Enter 换行
 * - 支持拖拽附件（dragover 高亮）
 */
export const Composer: React.FC<ComposerProps> = ({
  isStreaming,
  placeholder = '问点什么……（Enter 发送，Shift+Enter 换行）',
  onSend,
  onStop,
  onPickFile,
  onPickImage,
  onDrop,
  topSlot,
}) => {
  const { t } = useTranslation();
  const draft = useComposerS((s) => s.draft);
  const setDraft = useComposerS((s) => s.setDraft);
  const deepThink = useComposerS((s) => s.deepThink);
  const toggleDeepThink = useComposerS((s) => s.toggleDeepThink);
  const taRef = React.useRef<HTMLTextAreaElement>(null);
  const composingRef = React.useRef(false);
  const [dragging, setDragging] = React.useState(false);

  const autoResize = React.useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  React.useEffect(autoResize, [autoResize, draft]);

  const submit = React.useCallback(async () => {
    const text = draft.trim();
    if (!text || isStreaming) return;
    await onSend(text);
    setDraft('');
  }, [draft, isStreaming, onSend, setDraft]);

  return (
    <div
      className={cn(
        'rounded-2xl border bg-card shadow-sm transition-colors focus-within:border-primary',
        dragging && 'border-primary ring-2 ring-primary/30',
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        setDragging(false);
        onDrop?.(e);
      }}
    >
      {topSlot}

      <div className="flex items-center gap-2 px-3 pt-2">
        <Button
          variant={deepThink ? 'default' : 'outline'}
          size="sm"
          onClick={toggleDeepThink}
          className="h-7 rounded-full px-3 text-xs"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {t('chat.deepThink')}
        </Button>
      </div>
      <Textarea
        ref={taRef}
        value={draft}
        rows={1}
        placeholder={placeholder}
        className="min-h-12 max-h-52 resize-none border-0 bg-transparent px-4 py-2 shadow-none focus-visible:ring-0"
        onChange={(e) => setDraft(e.target.value)}
        onCompositionStart={() => (composingRef.current = true)}
        onCompositionEnd={() => (composingRef.current = false)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          if (e.shiftKey) return; // 换行
          if (composingRef.current) return; // IME 选词中不发
          e.preventDefault();
          void submit();
        }}
      />
      <div className="flex items-center justify-between gap-2 px-3 pb-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={onPickFile} aria-label="上传文件">
            <Paperclip className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onPickImage} aria-label="上传图片">
            <ImageIcon className="h-4 w-4" />
          </Button>
        </div>
        {isStreaming ? (
          <Button variant="destructive" size="icon" onClick={onStop} aria-label="停止">
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            disabled={!draft.trim()}
            onClick={() => void submit()}
            size="icon"
            aria-label="发送"
            className="rounded-full"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
};
