/**
 * GlowFrame —— 霓虹光晕外发光容器(参考图 ChatComposer 视觉锚点)。
 *
 * 设计:
 *   - 边缘发光:粉(左下)+ 蓝(右下)+ 绿(顶部)三色 radial-gradient。
 *   - 内层 surface 圆角 16,white;深色模式下走 surface-1。
 *   - reduced-motion 偏好时不展示呼吸动画。
 *
 * 用法:
 *   <GlowFrame intensity="md">
 *     <ChatComposer ... />
 *   </GlowFrame>
 */

import * as React from 'react';
import { cn } from '../lib/cn';

export interface GlowFrameProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 模糊强度 */
  intensity?: 'sm' | 'md' | 'lg';
  /** 是否常亮(false 则鼠标 focus-within / hover 时才发光) */
  alwaysOn?: boolean;
  /** 边框圆角 */
  radius?: number;
}

export const GlowFrame = React.forwardRef<HTMLDivElement, GlowFrameProps>(
  ({ intensity = 'md', alwaysOn = true, radius = 24, className, children, ...props }, ref) => {
    const blurMap = { sm: 'var(--cy-glow-blur-sm)', md: 'var(--cy-glow-blur-md)', lg: 'var(--cy-glow-blur-lg)' };
    return (
      <div
        ref={ref}
        className={cn(
          'group relative isolate motion-safe:transition-shadow',
          className,
        )}
        style={{ borderRadius: radius }}
        data-glow={alwaysOn ? 'on' : 'hover'}
        {...props}
      >
        {/* 霓虹光晕层(在内容下方,通过 isolate 形成新层叠上下文) */}
        <div
          aria-hidden
          className={cn(
            'pointer-events-none absolute -inset-px -z-10',
            !alwaysOn && 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
            'motion-safe:transition-opacity',
          )}
          style={{
            background: 'var(--cy-glow-composer)',
            filter: `blur(${blurMap[intensity]})`,
            borderRadius: radius + 6,
          }}
        />
        {/* 内层 surface */}
        <div
          className="relative bg-[var(--cy-surface-base)]"
          style={{ borderRadius: radius }}
        >
          {children}
        </div>
      </div>
    );
  },
);
GlowFrame.displayName = 'GlowFrame';
