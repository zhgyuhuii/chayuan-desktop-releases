/**
 * 模态路由生成的图像产物渲染卡。
 *
 * 数据源:v5 ``file`` part(``mediaType.startsWith('image/')``),
 * URL 是后端 artifacts 路由(``/v1/artifacts/<sha>.<ext>``),内容寻址 + immutable
 * cache,可直接交给 ``<img>``。
 *
 * 行为:
 *   - 默认缩略图(max-w / max-h);点击放大查看(原生 dialog,无外部库)
 *   - 右上 hover 显示「下载」按钮 → save-as 本地
 *   - 加载失败显示占位 + 错误提示(URL 失效 / 后端宕)
 *   - 显示 metadata.prompt(若有)作为下方说明,便于追溯生成参数
 */

import * as React from 'react';
import { Download, X as XIcon, ImageOff } from 'lucide-react';
import { cn } from '@chayuan/ui';

export interface ImageCardProps {
  url: string;
  /** 通常 image/png / image/jpeg / image/webp */
  mediaType: string;
  /** v5 file part 的扩展 metadata — width/height/prompt/seed/sha256 等 */
  metadata?: Record<string, unknown>;
  /** 自定义 className,用于消息气泡内对齐 */
  className?: string;
}

export const ImageCard: React.FC<ImageCardProps> = ({ url, mediaType, metadata, className }) => {
  const [open, setOpen] = React.useState(false);
  const [errored, setErrored] = React.useState(false);

  const w = typeof metadata?.width === 'number' ? (metadata.width as number) : undefined;
  const h = typeof metadata?.height === 'number' ? (metadata.height as number) : undefined;
  const prompt = typeof metadata?.prompt === 'string' ? (metadata.prompt as string) : undefined;
  const sha = typeof metadata?.sha256 === 'string' ? (metadata.sha256 as string) : undefined;
  const model = typeof metadata?.model === 'string' ? (metadata.model as string) : undefined;

  const filename = (() => {
    if (sha) {
      const ext = mediaType.split('/')[1] || 'png';
      return `${sha.slice(0, 16)}.${ext}`;
    }
    return url.split('/').pop() || 'image.png';
  })();

  const onDownload = async () => {
    try {
      const resp = await fetch(url);
      const blob = await resp.blob();
      const dl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = dl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(dl);
    } catch {
      // 退化:浏览器自己处理 (会打开图)
      window.open(url, '_blank');
    }
  };

  if (errored) {
    return (
      <div
        className={cn(
          'inline-flex flex-col items-center justify-center rounded-lg border border-dashed border-rose-300',
          'bg-rose-50/40 p-4 text-xs text-rose-700 dark:bg-rose-950/20 dark:text-rose-300',
          className,
        )}
      >
        <ImageOff className="mb-1 h-5 w-5" />
        图像加载失败(链接可能已过期)
      </div>
    );
  }

  return (
    <>
      <figure className={cn('group/img relative inline-block max-w-full', className)}>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={cn(
            'block overflow-hidden rounded-lg border border-[var(--cy-border-subtle)]',
            'bg-[var(--cy-surface-1)] transition-shadow hover:shadow-md',
          )}
          aria-label={prompt ? `查看大图:${prompt}` : '查看大图'}
        >
          <img
            src={url}
            alt={prompt || '生成图像'}
            loading="lazy"
            width={w}
            height={h}
            style={{ maxWidth: 360, maxHeight: 360 }}
            className="block h-auto w-auto object-contain"
            onError={() => setErrored(true)}
          />
        </button>

        {/* 右上 hover 操作:下载 */}
        <button
          type="button"
          onClick={onDownload}
          aria-label="下载图像"
          title="下载图像"
          className={cn(
            'absolute right-2 top-2 rounded-md bg-black/55 p-1.5 text-white opacity-0 backdrop-blur',
            'transition-opacity group-hover/img:opacity-100 focus-visible:opacity-100',
          )}
        >
          <Download className="h-3.5 w-3.5" />
        </button>

        {(prompt || w || model) && (
          <figcaption className="mt-1 text-[11px] leading-tight text-[var(--cy-text-tertiary)]">
            {prompt && <span className="line-clamp-2">{prompt}</span>}
            <span className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] opacity-80">
              {model && <span>{model}</span>}
              {w && h && <span>{w}×{h}</span>}
            </span>
          </figcaption>
        )}
      </figure>

      {/* 简易 lightbox — 不引外部依赖,backdrop 点击或 ESC 关闭 */}
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setOpen(false);
          }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
        >
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="关闭"
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          >
            <XIcon className="h-5 w-5" />
          </button>
          <img
            src={url}
            alt={prompt || '生成图像'}
            className="max-h-[90vh] max-w-[90vw] object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
};
