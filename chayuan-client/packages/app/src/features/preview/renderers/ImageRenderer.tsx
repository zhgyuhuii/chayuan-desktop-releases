/**
 * 图像预览:支持鼠标滚轮缩放 / 拖拽平移 / 双击 1:1 / 适应窗口。
 *
 * 性能:transform 硬件加速,大图不重排;wheel 节流避免抖动。
 */

import { Maximize2, Minus, Plus, RotateCcw } from 'lucide-react';
import * as React from 'react';
import type { RendererProps } from '../types';

export const ImageRenderer: React.FC<RendererProps> = ({ url, file, onReady, onError }) => {
  const [scale, setScale] = React.useState(1);
  const [offset, setOffset] = React.useState({ x: 0, y: 0 });
  const [loaded, setLoaded] = React.useState(false);

  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const draggingRef = React.useRef<null | { sx: number; sy: number; ox: number; oy: number }>(null);

  const reset = () => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  };
  const fit = () => reset();

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => clamp(s * (e.deltaY < 0 ? 1.12 : 0.9), 0.1, 8));
  };
  const onMouseDown = (e: React.MouseEvent) => {
    draggingRef.current = { sx: e.clientX, sy: e.clientY, ox: offset.x, oy: offset.y };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    const d = draggingRef.current;
    if (!d) return;
    setOffset({ x: d.ox + (e.clientX - d.sx), y: d.oy + (e.clientY - d.sy) });
  };
  const onMouseUp = () => {
    draggingRef.current = null;
  };
  const onDoubleClick = () => setScale((s) => (s === 1 ? 2 : 1));

  return (
    <div className="relative flex h-full w-full flex-col bg-[radial-gradient(circle_at_center,_var(--cy-surface-1)_0%,_var(--cy-surface-base)_100%)]">
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden"
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onDoubleClick={onDoubleClick}
        style={{ cursor: draggingRef.current ? 'grabbing' : scale > 1 ? 'grab' : 'default' }}
      >
        <div
          className="flex h-full w-full items-center justify-center transition-transform duration-75"
          style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={file.fileName}
            className="max-h-full max-w-full select-none object-contain shadow-lg"
            draggable={false}
            onLoad={() => {
              setLoaded(true);
              onReady?.();
            }}
            onError={() => onError?.(new Error('图片加载失败'))}
            style={{ visibility: loaded ? 'visible' : 'hidden' }}
          />
        </div>
      </div>
      <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]/85 px-2 py-1 shadow backdrop-blur">
        <Btn onClick={() => setScale((s) => clamp(s * 0.9, 0.1, 8))} title="缩小">
          <Minus className="h-3.5 w-3.5" />
        </Btn>
        <span className="min-w-[3.5em] text-center text-xs tabular-nums text-[var(--cy-text-secondary)]">
          {(scale * 100).toFixed(0)}%
        </span>
        <Btn onClick={() => setScale((s) => clamp(s * 1.12, 0.1, 8))} title="放大">
          <Plus className="h-3.5 w-3.5" />
        </Btn>
        <span className="mx-1 h-4 w-px bg-[var(--cy-border-subtle)]" />
        <Btn onClick={fit} title="适应">
          <Maximize2 className="h-3.5 w-3.5" />
        </Btn>
        <Btn onClick={reset} title="重置">
          <RotateCcw className="h-3.5 w-3.5" />
        </Btn>
      </div>
    </div>
  );
};

const Btn: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = (p) => (
  <button
    type="button"
    {...p}
    className="inline-flex h-7 w-7 items-center justify-center rounded-full text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
  />
);

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}
