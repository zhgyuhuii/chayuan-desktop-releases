/**
 * PreviewPanel — 渲染当前 store.current,按 mode/collapsed 决定布局。
 *
 * 三种 mode:
 *   - dock:固定贴右,宽度可在左边缘拖拽调整,collapse → 28px 折叠条
 *   - float:应用内浮窗,顶栏抓握平移,角部拖拽调尺寸
 *   - window:不渲染(主窗已切到独立子窗,store.current 也置 null)
 *
 * 渲染区:lazy renderer + Suspense + 错误边界(本地 try/catch by registry)。
 */

import { cn } from '@chayuan/ui';
import { ChevronLeft } from 'lucide-react';
import * as React from 'react';
import { PreviewActionRail } from './PreviewActionRail';
import { PreviewToolbar } from './PreviewToolbar';
import { onPreviewMessage } from './broadcast';
import { detectKind, extLabel } from './detectKind';
import { PREVIEW_DOCK_BOUNDS, PREVIEW_FLOAT_MIN, usePreviewStore } from './previewStore';
import { RENDERER_REGISTRY } from './renderers';
import { ErrorRenderer } from './renderers/ErrorRenderer';
import { LoadingRenderer } from './renderers/LoadingRenderer';
import { previewFileKey } from './types';
import { useFileUrl } from './useFileUrl';
import { usePreviewConfig } from './usePreviewConfig';

const KkFileViewRenderer = React.lazy(() =>
  import('./renderers/KkFileViewRenderer').then((m) => ({ default: m.KkFileViewRenderer })),
);

const COLLAPSED_W = 32;
const HANDLE_W = 6;

export interface PreviewPanelProps {
  /** standalone = 在独立子窗内运行,不展示模式切换 */
  standalone?: boolean;
  /**
   * 内嵌模式:作为父容器的 flex 子项渲染,不再用 fixed-viewport 抽屉。
   * 适合 KbDetailPage 这种把预览嵌在自己布局右列的页面 — 不会遮挡列表/composer。
   * 内嵌模式下 dock/float/collapse 按钮自动隐藏(模式切换在内嵌中无意义)。
   */
  embedded?: boolean;
}

export const PreviewPanel: React.FC<PreviewPanelProps> = ({
  standalone = false,
  embedded = false,
}) => {
  const current = usePreviewStore((s) => s.current);
  const mode = usePreviewStore((s) => s.mode);
  const collapsed = usePreviewStore((s) => s.collapsed);
  const dockWidth = usePreviewStore((s) => s.dockWidth);
  const float = usePreviewStore((s) => s.float);
  const setDockWidth = usePreviewStore((s) => s.setDockWidth);
  const setFloat = usePreviewStore((s) => s.setFloat);
  const setCollapsed = usePreviewStore((s) => s.setCollapsed);
  const close = usePreviewStore((s) => s.close);

  // 跨窗:子窗发来 close → 主窗也清 current
  React.useEffect(() => {
    if (standalone) return;
    return onPreviewMessage((m) => {
      if (m.type === 'closed' && mode === 'window') close();
    });
  }, [standalone, mode, close]);

  if (!current) return null;
  if (mode === 'window' && !standalone && !embedded) return null;

  // 折叠条(只在 dock 模式有意义,float 模式折叠就直接关掉更直观;embedded 不折)
  if (collapsed && mode === 'dock' && !standalone && !embedded) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="fixed right-0 top-1/2 z-[55] flex h-32 w-8 -translate-y-1/2 flex-col items-center justify-center gap-2 rounded-l-xl border border-r-0 border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] text-[var(--cy-text-tertiary)] shadow-lg transition-colors hover:bg-[var(--cy-surface-1)] hover:text-[var(--cy-text-primary)]"
        title={`展开 ${current.fileName}`}
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        <span className="rounded-md bg-gradient-to-br from-indigo-500 to-violet-600 px-1 py-0.5 text-[8px] font-bold text-white">
          {extLabel(current)}
        </span>
        <span className="vertical-rl text-[10px] font-medium [writing-mode:vertical-rl]">
          {truncate(current.fileName, 8)}
        </span>
      </button>
    );
  }

  // 给 float 模式一个右下角拖拽手柄
  const isFloat = mode === 'float' && !standalone && !embedded;

  // 内嵌模式:作为 flex 子项填充父容器,不用 fixed/绝对定位。父容器决定宽高。
  const embeddedStyle: React.CSSProperties = { width: '100%', height: '100%' };
  const containerStyle: React.CSSProperties = embedded
    ? embeddedStyle
    : standalone
      ? { position: 'fixed', inset: 0, zIndex: 1 }
      : isFloat
        ? {
            position: 'fixed',
            left: float.x,
            top: float.y,
            width: float.w,
            height: float.h,
            zIndex: 60,
          }
        : {
            position: 'fixed',
            top: 0,
            right: 0,
            width: dockWidth,
            height: '100vh',
            zIndex: 55,
          };

  return (
    <>
      {/* dock 模式左边缘拖拽手柄(内嵌模式由父容器自己控制宽度,无手柄) */}
      {mode === 'dock' && !standalone && !embedded && (
        <DockResizer width={dockWidth} onChange={setDockWidth} />
      )}
      <div
        style={containerStyle}
        className={cn(
          'flex flex-col overflow-hidden bg-[var(--cy-surface-base)]',
          embedded
            ? 'border-l border-[var(--cy-border-subtle)]'
            : 'border border-[var(--cy-border-subtle)] shadow-2xl',
          isFloat ? 'animate-pop rounded-xl' : 'rounded-none border-y-0 border-r-0',
          standalone && 'border-0',
        )}
      >
        <PreviewToolbar
          file={current}
          standalone={standalone}
          embedded={embedded}
          onDragStart={isFloat ? makeFloatDrag(setFloat, float) : undefined}
        />
        <div className="relative flex-1 overflow-hidden">
          <RendererArea key={previewFileKey(current)} file={current} />
          <PreviewActionRail file={current} />
        </div>
        {/* float 模式右下角 resize 手柄 */}
        {isFloat && <FloatResizer geom={float} onChange={setFloat} />}
      </div>
      <style>{floatPopCss}</style>
    </>
  );
};

// ──────────────────────────────────────────────────────────────
// Renderer 区域
// ──────────────────────────────────────────────────────────────

const RendererArea: React.FC<{ file: ReturnType<typeof usePreviewStore.getState>['current'] }> = ({
  file,
}) => {
  const f = file!;
  const kind = detectKind(f);
  const { url, fetchBlob } = useFileUrl(f);
  const { kkFileViewUrl } = usePreviewConfig();
  const [errored, setErrored] = React.useState<Error | null>(null);
  const [refreshKey, setRefreshKey] = React.useState(0);

  // 切换文件清错误
  React.useEffect(() => {
    setErrored(null);
  }, [f]);

  if (!url) return <LoadingRenderer label="准备访问令牌…" />;
  if (errored) {
    return (
      <ErrorRenderer
        error={errored}
        onRetry={() => {
          setErrored(null);
          setRefreshKey((k) => k + 1);
        }}
      />
    );
  }

  // kkFileView 已配置 → 全格式接管(覆盖 100+ 格式,Office/WPS/3D/CAD/视频转码 etc.)
  // 没配置 → 走内置 RENDERER_REGISTRY,保持原有行为不变
  const BuiltinRenderer = RENDERER_REGISTRY[kind];

  return (
    <React.Suspense fallback={<LoadingRenderer />}>
      <RendererErrorBoundary onError={setErrored}>
        {kkFileViewUrl ? (
          <KkFileViewRenderer
            key={refreshKey}
            kkBase={kkFileViewUrl}
            file={f}
            url={url}
            fetchBlob={fetchBlob}
            onError={(e) => setErrored(e)}
          />
        ) : (
          <BuiltinRenderer
            key={refreshKey}
            file={f}
            url={url}
            fetchBlob={fetchBlob}
            onError={(e) => setErrored(e)}
          />
        )}
      </RendererErrorBoundary>
    </React.Suspense>
  );
};

class RendererErrorBoundary extends React.Component<
  { children: React.ReactNode; onError: (e: Error) => void },
  { hasError: boolean }
> {
  override state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  override componentDidCatch(err: Error) {
    this.props.onError(err);
  }
  override render() {
    return this.state.hasError ? null : this.props.children;
  }
}

// ──────────────────────────────────────────────────────────────
// Resizers
// ──────────────────────────────────────────────────────────────

const DockResizer: React.FC<{ width: number; onChange: (w: number) => void }> = ({
  width,
  onChange,
}) => {
  const [drag, setDrag] = React.useState(false);
  React.useEffect(() => {
    if (!drag) return;
    const onMove = (e: MouseEvent) => {
      onChange(window.innerWidth - e.clientX);
    };
    const onUp = () => setDrag(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [drag, onChange]);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={width}
      aria-valuemin={PREVIEW_DOCK_BOUNDS.min}
      aria-valuemax={PREVIEW_DOCK_BOUNDS.max}
      onMouseDown={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      style={{ right: width - HANDLE_W / 2 }}
      className="fixed top-0 z-[56] h-screen cursor-col-resize"
    >
      <div
        className="ml-[2px] h-full w-[2px] bg-transparent transition-colors hover:bg-[var(--cy-brand-400)]"
        style={{ width: HANDLE_W }}
      />
    </div>
  );
};

interface FloatGeom {
  x: number;
  y: number;
  w: number;
  h: number;
}

const FloatResizer: React.FC<{ geom: FloatGeom; onChange: (g: Partial<FloatGeom>) => void }> = ({
  geom,
  onChange,
}) => {
  const [drag, setDrag] = React.useState(false);
  React.useEffect(() => {
    if (!drag) return;
    const onMove = (e: MouseEvent) => {
      onChange({
        w: Math.max(PREVIEW_FLOAT_MIN.w, e.clientX - geom.x),
        h: Math.max(PREVIEW_FLOAT_MIN.h, e.clientY - geom.y),
      });
    };
    const onUp = () => setDrag(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [drag, geom.x, geom.y, onChange]);
  return (
    <div
      onMouseDown={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      title="拖拽调整大小"
      className="absolute bottom-0 right-0 z-10 h-4 w-4 cursor-nwse-resize text-[var(--cy-text-tertiary)] hover:text-[var(--cy-brand-500)]"
    >
      <svg viewBox="0 0 16 16" className="h-full w-full">
        <path d="M14 6 L6 14 M14 10 L10 14" stroke="currentColor" strokeWidth="1.4" fill="none" />
      </svg>
    </div>
  );
};

function makeFloatDrag(
  setFloat: (g: Partial<FloatGeom>) => void,
  geom: FloatGeom,
): (e: React.MouseEvent) => void {
  return (e) => {
    if ((e.target as HTMLElement).closest('button, input, a')) return;
    e.preventDefault();
    const sx = e.clientX,
      sy = e.clientY;
    const ox = geom.x,
      oy = geom.y;
    const onMove = (ev: MouseEvent) => {
      setFloat({ x: ox + ev.clientX - sx, y: oy + ev.clientY - sy });
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
}

const floatPopCss = `
@keyframes cy-preview-pop { from { transform: translateY(8px) scale(.97); opacity: 0 } to { transform: translateY(0) scale(1); opacity: 1 } }
.animate-pop { animation: cy-preview-pop .18s ease-out }
`;

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}
