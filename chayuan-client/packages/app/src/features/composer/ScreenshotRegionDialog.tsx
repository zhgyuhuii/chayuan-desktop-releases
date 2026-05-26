/**
 * 截图选区裁剪对话框。
 *
 * **设计意图**:用户反馈"窗口隐藏后只能瞎截整屏,不知道截了什么"。改成:
 *   1. 抓全屏(察元自己也会进画面 — OK,用户能完整看到)
 *   2. 弹这个全屏 modal 把抓到的图按 contain 缩放展示
 *   3. 用户在图上鼠标拖一个矩形选区
 *   4. 点确认 → canvas 按原图分辨率裁剪 → 输出 Blob 给 useChatScreenshot
 *
 * **坐标系**:
 *   - "渲染坐标" = onscreen px(<img> 在 viewport 里的位置 + 尺寸)
 *   - "原图坐标" = captured PNG 的原始像素(可能 1920x1080 / 4K / 多屏拼接)
 *   两者比例 ratio = naturalSize / displaySize 必须每帧实时算 — 用户拉窗口 /
 *   切显示器都会让缩放变。最终 crop 用原图坐标,保证导出全分辨率。
 *
 * **键盘**:Esc = 取消;Enter = 确认(选区非空时)。
 */
import * as React from 'react';
import { Check, Crop, RotateCcw, X } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';

export interface ScreenshotRegionDialogProps {
  /** 原图 Blob(全屏截图);组件接管 objectURL 生命周期 */
  blob: Blob;
  /** 用户点确认后,裁剪好的 Blob 通过这个回调返回 */
  onConfirm(cropped: Blob): void;
  /** 用户点取消 / Esc / 直接关闭 */
  onCancel(): void;
}

interface Rect {
  /** 渲染坐标(<img> 元素内 px) */
  x: number;
  y: number;
  w: number;
  h: number;
}

const MIN_REGION_PX = 4;  // 太小当成误点不当选区

export const ScreenshotRegionDialog: React.FC<ScreenshotRegionDialogProps> = ({
  blob, onConfirm, onCancel,
}) => {
  // objectURL 生命周期:dialog mount 时创建,unmount 时 revoke
  const url = React.useMemo(() => URL.createObjectURL(blob), [blob]);
  React.useEffect(() => {
    return () => { try { URL.revokeObjectURL(url); } catch { /* ignore */ } };
  }, [url]);

  // 原图 naturalSize — 第一次 onLoad 拿到后存下来,canvas crop 时用
  const imgRef = React.useRef<HTMLImageElement>(null);
  const [natural, setNatural] = React.useState<{ w: number; h: number } | null>(null);

  // 选区(渲染坐标);null 表示尚未拖
  const [rect, setRect] = React.useState<Rect | null>(null);
  // 拖动中的临时 origin;mouseup 时 commit 进 rect
  const dragRef = React.useRef<{ ox: number; oy: number } | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);

  // 全屏 Esc / Enter
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
      } else if (e.key === 'Enter' && rect && rect.w >= MIN_REGION_PX && rect.h >= MIN_REGION_PX) {
        e.preventDefault();
        void doCrop();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // doCrop / rect 依赖在 closure 里读,自己捕新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rect, onCancel]);

  // ── 拖动事件(渲染坐标) ────────────────────────────────────────
  const imgRect = () => imgRef.current?.getBoundingClientRect();

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    const r = imgRect();
    if (!r) return;
    const x = Math.max(0, Math.min(r.width, e.clientX - r.left));
    const y = Math.max(0, Math.min(r.height, e.clientY - r.top));
    dragRef.current = { ox: x, oy: y };
    setRect({ x, y, w: 0, h: 0 });
    setDragging(true);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return;
    const r = imgRect();
    if (!r) return;
    const cx = Math.max(0, Math.min(r.width, e.clientX - r.left));
    const cy = Math.max(0, Math.min(r.height, e.clientY - r.top));
    const { ox, oy } = dragRef.current;
    setRect({
      x: Math.min(ox, cx),
      y: Math.min(oy, cy),
      w: Math.abs(cx - ox),
      h: Math.abs(cy - oy),
    });
  };

  const onMouseUp = () => {
    dragRef.current = null;
    setDragging(false);
    // 太小的拖动当成误点 — 直接清掉,让用户重选
    setRect((r) => (r && (r.w < MIN_REGION_PX || r.h < MIN_REGION_PX)) ? null : r);
  };

  const onReset = () => setRect(null);

  // ── 裁剪 ────────────────────────────────────────────────────────
  const doCrop = React.useCallback(async () => {
    if (!rect || !natural || !imgRef.current) return;
    if (confirming) return;
    setConfirming(true);
    try {
      const displayW = imgRef.current.clientWidth;
      const displayH = imgRef.current.clientHeight;
      if (displayW === 0 || displayH === 0) throw new Error('图片尚未渲染');
      const ratioX = natural.w / displayW;
      const ratioY = natural.h / displayH;
      // 渲染坐标 → 原图坐标(向 floor 对齐;尺寸向上保守一点避免漏 1px)
      const sx = Math.max(0, Math.floor(rect.x * ratioX));
      const sy = Math.max(0, Math.floor(rect.y * ratioY));
      const sw = Math.min(natural.w - sx, Math.ceil(rect.w * ratioX));
      const sh = Math.min(natural.h - sy, Math.ceil(rect.h * ratioY));
      if (sw <= 0 || sh <= 0) throw new Error('选区无效');

      // 用 createImageBitmap 比 Image+drawImage 快(后者要走 DOM decode 链);
      // 不支持时 fallback 到 <img>
      let bmpOrImg: ImageBitmap | HTMLImageElement;
      try {
        bmpOrImg = await createImageBitmap(blob);
      } catch {
        bmpOrImg = imgRef.current;
      }
      const canvas = document.createElement('canvas');
      canvas.width = sw;
      canvas.height = sh;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('canvas 2d 上下文初始化失败');
      ctx.drawImage(bmpOrImg, sx, sy, sw, sh, 0, 0, sw, sh);
      if ('close' in bmpOrImg) bmpOrImg.close();

      const cropped = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), 'image/png'),
      );
      if (!cropped) throw new Error('canvas.toBlob 返回空');
      onConfirm(cropped);
    } catch (e) {
      setConfirming(false);
      // 用户视角:点了确认没反应 — 至少弹个 console + alert
      console.error('[screenshot crop]', e);
      window.alert(e instanceof Error ? e.message : String(e));
    }
  }, [rect, natural, blob, confirming, onConfirm]);

  // ── 取消时点击空白判定 ─────────────────────────────────────────
  // 点击 dim 蒙版触发取消;点击图 / toolbar 不触发
  const onBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onCancel();
  };

  const hasRect = !!rect && rect.w >= MIN_REGION_PX && rect.h >= MIN_REGION_PX;

  return (
    <div
      onClick={onBackdropClick}
      className="fixed inset-0 z-[100] flex flex-col bg-black/85 backdrop-blur-sm"
    >
      {/* 顶部工具栏 */}
      <div className="flex flex-none items-center justify-between gap-3 border-b border-white/10 bg-black/40 px-4 py-2 text-white">
        <div className="flex items-center gap-2 text-sm">
          <Crop className="h-4 w-4 text-amber-300" />
          <span className="font-medium">截图 — 在画面上拖动选择区域</span>
          {hasRect && (
            <span className="ml-2 rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] text-white/80">
              {Math.round(rect!.w * (natural?.w ?? 0) / (imgRef.current?.clientWidth || 1))}
              {' × '}
              {Math.round(rect!.h * (natural?.h ?? 0) / (imgRef.current?.clientHeight || 1))} px
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {hasRect && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onReset}
              className="h-7 gap-1 text-xs text-white/80 hover:bg-white/10 hover:text-white"
              title="重选(R)"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              重选
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onCancel}
            className="h-7 gap-1 text-xs text-white/80 hover:bg-white/10 hover:text-white"
            title="取消(Esc)"
          >
            <X className="h-3.5 w-3.5" />
            取消
          </Button>
          <Button
            size="sm"
            onClick={() => void doCrop()}
            disabled={!hasRect || confirming}
            className="h-7 gap-1 text-xs"
            title="确认(Enter)"
          >
            <Check className="h-3.5 w-3.5" />
            {confirming ? '裁剪中…' : '确认'}
          </Button>
        </div>
      </div>

      {/* 中央图片 + 选区蒙层 */}
      <div
        onClick={onBackdropClick}
        className="relative flex min-h-0 flex-1 items-center justify-center p-6"
      >
        <div
          className="relative inline-block max-h-full max-w-full select-none"
          style={{ cursor: dragging ? 'crosshair' : 'crosshair' }}
        >
          <img
            ref={imgRef}
            src={url}
            alt="截图"
            draggable={false}
            onLoad={(e) => {
              const im = e.currentTarget;
              setNatural({ w: im.naturalWidth, h: im.naturalHeight });
            }}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseUp}
            className="block max-h-[calc(100vh-7rem)] max-w-full object-contain shadow-2xl"
            style={{ userSelect: 'none' }}
          />
          {/* 渲染选区 — 用绝对定位 div 叠在图上(图本身是 inline-block,
              这个 div 用图相对坐标即可) */}
          {rect && (
            <>
              {/* 四面半透明蒙版:把"未选区"压暗,突出选区 */}
              <div
                aria-hidden
                className="pointer-events-none absolute inset-0"
                style={{
                  background:
                    `linear-gradient(rgba(0,0,0,0.55), rgba(0,0,0,0.55))`,
                  clipPath:
                    `polygon(0 0, 0 100%, ${rect.x}px 100%, ${rect.x}px ${rect.y}px, ${rect.x + rect.w}px ${rect.y}px, ${rect.x + rect.w}px ${rect.y + rect.h}px, ${rect.x}px ${rect.y + rect.h}px, ${rect.x}px 100%, 100% 100%, 100% 0)`,
                }}
              />
              {/* 选区描边 + 角标 */}
              <div
                aria-hidden
                className={cn(
                  'pointer-events-none absolute border-2 border-amber-300',
                  dragging ? 'shadow-[0_0_0_1px_rgba(252,211,77,0.6)]' : 'shadow-lg',
                )}
                style={{
                  left: rect.x, top: rect.y, width: rect.w, height: rect.h,
                }}
              />
            </>
          )}
        </div>
      </div>

      {/* 底部提示 */}
      <div className="pointer-events-none flex flex-none items-center justify-center gap-4 border-t border-white/10 bg-black/40 py-1.5 text-[10px] text-white/60">
        <span>鼠标拖动 = 选区</span>
        <span>Enter = 确认</span>
        <span>Esc = 取消</span>
        <span>R = 重选</span>
      </div>
    </div>
  );
};
