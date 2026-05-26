import * as React from 'react';
import { cn } from '../lib/cn';

/**
 * Avatar —— 用户头像 / 欢迎球。
 *
 * 默认渐变(iris-from → iris-via → iris-to),用首字母兜底。
 * 设计稿:Sidebar 头像、Welcome 大球、Topbar 小头像。
 */

export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 显示首字母时取用的名字 */
  name?: string;
  /** 头像图片 URL;失败回退到首字母 */
  src?: string;
  /** 36 / 32 / 28 / 24 / 56 像素;默认 32 */
  size?: number;
  /** 是否显示在线小绿点 */
  status?: 'online' | 'offline';
}

export const Avatar = React.forwardRef<HTMLDivElement, AvatarProps>(
  ({ name, src, size = 32, status, className, style, ...props }, ref) => {
    const [error, setError] = React.useState(false);
    const initial = (name?.[0] ?? 'U').toUpperCase();
    return (
      <div
        ref={ref}
        className={cn(
          'relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-full text-white',
          className,
        )}
        style={{
          width: size,
          height: size,
          fontSize: Math.max(11, Math.round(size * 0.42)),
          background: 'var(--cy-iris-gradient)',
          ...style,
        }}
        {...props}
      >
        {!error && src ? (
          <img
            src={src}
            alt={name ?? 'avatar'}
            className="h-full w-full object-cover"
            onError={() => setError(true)}
          />
        ) : (
          <span className="font-semibold">{initial}</span>
        )}
        {status ? (
          <span
            className={cn(
              'absolute bottom-0 right-0 block rounded-full ring-2 ring-white',
              status === 'online' ? 'bg-emerald-500' : 'bg-zinc-400',
            )}
            style={{ width: Math.max(6, size * 0.25), height: Math.max(6, size * 0.25) }}
          />
        ) : null}
      </div>
    );
  },
);
Avatar.displayName = 'Avatar';
