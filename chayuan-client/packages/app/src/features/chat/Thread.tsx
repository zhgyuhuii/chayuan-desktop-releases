import * as React from 'react';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from './useChayuanChat';

export interface ThreadProps {
  messages: ChatMessage[];
  onRegenerate?: () => void;
  onResume?: (approved: boolean) => void;
  onEdit?: (id: string, newContent: string) => void;
  onBranch?: (id: string) => void;
}

/**
 * 消息列表渲染。
 *
 * 性能:
 * - 自动跟随到底(仅当用户已经在底部,否则保持滚动位置)
 * - **windowing**:消息数 > THRESHOLD 时只渲染滚动可视区前后各 OVERSCAN 条;
 *   实现是基于滚动位置 + 单条估算高度做粗略二分,不引 react-virtual,免一项
 *   workspace 依赖。最近 24 条永远渲染(用户最在意的当前对话上下文)。
 * - 流式中追加 token 不会重渲整个列表(MessageBubble 自身处理 streaming 字段)。
 */

const VIRTUAL_THRESHOLD = 60;
const ESTIMATED_ROW_HEIGHT = 220;
const OVERSCAN = 6;
const TAIL_PIN = 24;

export const Thread: React.FC<ThreadProps> = ({ messages, onRegenerate, onResume, onEdit, onBranch }) => {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const stickRef = React.useRef(true);
  const [scrollTop, setScrollTop] = React.useState(0);
  const [viewportH, setViewportH] = React.useState(0);

  React.useLayoutEffect(() => {
    const c = containerRef.current;
    if (!c) return;
    if (stickRef.current) c.scrollTop = c.scrollHeight;
  });

  React.useEffect(() => {
    const c = containerRef.current;
    if (!c) return;
    setViewportH(c.clientHeight);
    const obs = new ResizeObserver(() => setViewportH(c.clientHeight));
    obs.observe(c);
    return () => obs.disconnect();
  }, []);

  const total = messages.length;
  const useVirtual = total > VIRTUAL_THRESHOLD;

  // 计算可见窗口
  let startIdx = 0;
  let endIdx = total;
  let topSpacer = 0;
  let bottomSpacer = 0;

  if (useVirtual && viewportH > 0) {
    const tailStart = Math.max(0, total - TAIL_PIN);
    const winStart = Math.max(0, Math.floor(scrollTop / ESTIMATED_ROW_HEIGHT) - OVERSCAN);
    const winCount = Math.ceil(viewportH / ESTIMATED_ROW_HEIGHT) + OVERSCAN * 2;
    let winEnd = Math.min(total, winStart + winCount);
    // 末尾 24 条强制渲染(常被 stickToBottom 立即用到)
    if (winEnd < tailStart) {
      winEnd = winStart + winCount;  // 视图窗口
    }
    startIdx = Math.min(winStart, tailStart);
    endIdx = Math.max(winEnd, total);
    topSpacer = startIdx * ESTIMATED_ROW_HEIGHT;
    bottomSpacer = Math.max(0, (total - endIdx) * ESTIMATED_ROW_HEIGHT);
  }

  const slice = useVirtual ? messages.slice(startIdx, endIdx) : messages;

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto px-6 py-4"
      onScroll={(e) => {
        const el = e.currentTarget;
        stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
        if (useVirtual) setScrollTop(el.scrollTop);
      }}
    >
      <div className="mx-auto flex max-w-3xl flex-col">
        {topSpacer > 0 && <div style={{ height: topSpacer }} aria-hidden />}
        {slice.map((m) => (
          <div key={m.id} className="message-bubble group">
            <MessageBubble
              message={m}
              onRegenerate={m.role === 'assistant' ? onRegenerate : undefined}
              onResume={m.interrupt ? onResume : undefined}
              onEdit={m.role === 'user' ? onEdit : undefined}
              onBranch={m.role === 'assistant' ? onBranch : undefined}
            />
          </div>
        ))}
        {bottomSpacer > 0 && <div style={{ height: bottomSpacer }} aria-hidden />}
      </div>
    </div>
  );
};
