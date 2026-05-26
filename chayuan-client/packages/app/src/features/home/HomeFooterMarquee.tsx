/**
 * 底部状态栏内嵌走马灯 —— 版权 / 源码授权 / slogan 横向无限循环。
 *
 * 用在 `AppFooter` 中间段(左侧版权静态、右侧模型服务状态点静态、
 * 它在中间动态滚动)。点击整条弹 FeedbackDialog 出"关注我们"二维码。
 *
 * 设计:
 *   - 高 28px 跟 AppFooter (`h-7`) 完全贴合,无 border/padding 加成
 *   - 文字 11px / `text-[var(--cy-text-tertiary)]`,跟 footer 默认色一致
 *   - hover 暂停 + 文字变 secondary 提示可点
 *   - prefers-reduced-motion 静止显示,不打扰
 *   - track 复制两遍,CSS `chayuan-marquee` keyframes 平移 -50% 无缝
 *
 * 名字保留 HomeFooterMarquee 是历史原因(原本设计在 HomePage 底部);
 * 实际现在挂在全局 footer。
 */

import * as React from 'react';
import { FeedbackDialog } from '../help/FeedbackDialog';

const MARQUEE_ITEMS = [
  '源码授权 · 商业合作请联系我们',
  '察元 AI 助手 · 让办公场景里每个人都能用上 AI',
];

// 复制两份让 -50% 平移无缝
const TRACK_ITEMS = [...MARQUEE_ITEMS, ...MARQUEE_ITEMS];

export const HomeFooterMarquee: React.FC = () => {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <div
        onClick={() => setOpen(true)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        aria-label="点击关注我们 —— 公众号二维码"
        title="点击关注我们"
        className="
          group relative h-full min-w-0 flex-1 cursor-pointer overflow-hidden
          text-[11px] text-[var(--cy-text-tertiary)] select-none
          hover:text-[var(--cy-text-secondary)] transition-colors
        "
      >
        <div
          className="
            flex h-full w-max items-center gap-6 whitespace-nowrap
            motion-safe:animate-[chayuan-marquee_32s_linear_infinite]
            group-hover:[animation-play-state:paused]
          "
        >
          {TRACK_ITEMS.map((text, i) => (
            <span key={i} className="inline-flex items-center gap-6">
              <span>{text}</span>
              <span aria-hidden className="opacity-50">·</span>
            </span>
          ))}
        </div>
      </div>
      <FeedbackDialog open={open} onOpenChange={setOpen} />
    </>
  );
};
