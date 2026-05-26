/**
 * 独立的截图选区窗口 App。
 *
 * **运行环境**:Tauri 子窗 ``screenshot-overlay``,主窗用 ``WebviewWindow`` 全屏
 * 透明 always-on-top 创建出来,加载 URL ``/screenshot-overlay``。``apps/desktop/
 * src/main.tsx`` 在 createRoot 之前 detect 这个路径,**跳过 Shell**(没 router /
 * 鉴权 / sidecar / i18n …),直接挂这个组件 — 启动毫秒级,内存几 MB。
 *
 * **跨窗 IPC**(详见 ``platform-tauri/index.ts:tauriCapture.screenshotInteractive``):
 *   - mount 调 ``chayuan_take_screenshot_buffer`` 拿主窗刚抓的全屏 PNG
 *   - 渲染 fullscreen <img> 当背景(图片像素就是用户当前屏幕)
 *   - 用户拖鼠标矩形选区 + 实时画白边 + dim 蒙版
 *   - Enter / 双击 / 工具栏"完成" → canvas 裁剪 → ``chayuan_submit_screenshot_result(bytes)``
 *   - Esc / 工具栏"取消" / 右键 → submit null
 *   - Rust 端 emit ``chayuan://screenshot/done`` 给主窗 + 自关本窗
 *
 * **样式**:html/body 必须 ``background: transparent`` 才能让 Tauri 透明窗真透
 * 明 — 用 useLayoutEffect 在 mount 时强行写,unmount 时撤回。
 */
import * as React from 'react';

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * IPC 抽象层 — overlay 不直接 import @tauri-apps/api(那是 platform-tauri 的私有
 * 依赖),由 ``apps/desktop/main.tsx`` 在 mount 时注入两个 callback,保证本包
 * 不依赖 Tauri。Web 端理论上也能跑(用 postMessage),目前没用例。
 */
export interface ScreenshotOverlayIpc {
  /** 拉抓屏 PNG 字节(主窗已经 capture_for_overlay 抓好了) */
  loadBuffer(): Promise<Uint8Array>;
  /** 提交结果:bytes=Uint8Array=用户确认;bytes=null=用户取消 */
  submit(bytes: Uint8Array | null): Promise<void>;
}

const MIN_REGION_PX = 4;

export const ScreenshotOverlayApp: React.FC<{ ipc: ScreenshotOverlayIpc }> = ({ ipc }) => {
  const [imgUrl, setImgUrl] = React.useState<string | null>(null);
  const [imgBlob, setImgBlob] = React.useState<Blob | null>(null);
  const [natural, setNatural] = React.useState<{ w: number; h: number } | null>(null);
  const [rect, setRect] = React.useState<Rect | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);
  const dragRef = React.useRef<{ ox: number; oy: number } | null>(null);
  const imgRef = React.useRef<HTMLImageElement>(null);

  // ── 透明背景:Tauri 端 transparent=true 只是开启能力,具体透明度由 CSS 决定
  React.useLayoutEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prev = {
      htmlBg: html.style.background,
      bodyBg: body.style.background,
      bodyMargin: body.style.margin,
      bodyOverflow: body.style.overflow,
    };
    html.style.background = 'transparent';
    body.style.background = 'transparent';
    body.style.margin = '0';
    body.style.overflow = 'hidden';
    return () => {
      html.style.background = prev.htmlBg;
      body.style.background = prev.bodyBg;
      body.style.margin = prev.bodyMargin;
      body.style.overflow = prev.bodyOverflow;
    };
  }, []);

  // ── 从 Rust state 拉抓屏的 PNG 字节
  React.useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    (async () => {
      try {
        const bytes = await ipc.loadBuffer();
        if (cancelled) return;
        // 强转 ArrayBuffer view — TS lib.dom 严格模式下 Uint8Array<ArrayBufferLike>
        // 不能直接当 BlobPart;走 .buffer.slice() 拿一个干净的 ArrayBuffer
        const blob = new Blob([bytes.slice().buffer], { type: 'image/png' });
        setImgBlob(blob);
        createdUrl = URL.createObjectURL(blob);
        setImgUrl(createdUrl);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (createdUrl) {
        try { URL.revokeObjectURL(createdUrl); } catch { /* ignore */ }
      }
    };
  }, []);

  // ── Esc 取消 / Enter 确认
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        void submitCancel();
      } else if (e.key === 'Enter' && rect && rect.w >= MIN_REGION_PX && rect.h >= MIN_REGION_PX) {
        e.preventDefault();
        void submitConfirm();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rect]);

  // ── 鼠标事件
  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) {
      // 右键 = 取消(对齐 QQ 截图操作习惯)
      e.preventDefault();
      void submitCancel();
      return;
    }
    const x = e.clientX;
    const y = e.clientY;
    dragRef.current = { ox: x, oy: y };
    setRect({ x, y, w: 0, h: 0 });
    setDragging(true);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return;
    const { ox, oy } = dragRef.current;
    const cx = e.clientX;
    const cy = e.clientY;
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
    setRect((r) => (r && (r.w < MIN_REGION_PX || r.h < MIN_REGION_PX)) ? null : r);
  };

  // 双击选区 = 确认(像 QQ)
  const onDoubleClick = () => {
    if (rect && rect.w >= MIN_REGION_PX && rect.h >= MIN_REGION_PX) {
      void submitConfirm();
    }
  };

  // ── 裁剪 + 提交
  const submitConfirm = React.useCallback(async () => {
    if (!rect || !imgBlob || !natural || confirming) return;
    setConfirming(true);
    try {
      // window 渲染坐标 = 屏幕逻辑像素;Tauri 创窗时 inner_size=monitor 物理尺寸
      // (但 webview devicePixelRatio 可能 >1,window.innerWidth 单位是 CSS px)。
      // overlay 窗用 width=physical_w 创建,但 webview CSS 尺寸 = physical / dpr。
      // 用户拖框是 CSS 坐标,我们用 (CSS / window.innerW * naturalW) 换算回原图像素。
      const dispW = window.innerWidth;
      const dispH = window.innerHeight;
      const ratioX = natural.w / dispW;
      const ratioY = natural.h / dispH;
      const sx = Math.max(0, Math.floor(rect.x * ratioX));
      const sy = Math.max(0, Math.floor(rect.y * ratioY));
      const sw = Math.min(natural.w - sx, Math.ceil(rect.w * ratioX));
      const sh = Math.min(natural.h - sy, Math.ceil(rect.h * ratioY));
      if (sw <= 0 || sh <= 0) throw new Error('选区无效');

      let bmp: ImageBitmap | HTMLImageElement;
      try {
        bmp = await createImageBitmap(imgBlob);
      } catch {
        if (!imgRef.current) throw new Error('图像未渲染');
        bmp = imgRef.current;
      }
      const canvas = document.createElement('canvas');
      canvas.width = sw;
      canvas.height = sh;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('canvas 2d 失败');
      ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, sw, sh);
      if ('close' in bmp) bmp.close();

      const cropped = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), 'image/png'),
      );
      if (!cropped) throw new Error('canvas.toBlob 返空');
      const buf = await cropped.arrayBuffer();
      await ipc.submit(new Uint8Array(buf));
      // 不需要 window.close — Rust submit 完会自关 overlay
    } catch (e) {
      setConfirming(false);
      console.error('[screenshot overlay crop]', e);
      // 失败时仍提交 null,避免主窗永远 hang
      try { await ipc.submit(null); } catch { /* ignore */ }
    }
  }, [rect, imgBlob, natural, confirming, ipc]);

  const submitCancel = React.useCallback(async () => {
    try { await ipc.submit(null); }
    catch (e) { console.error('[screenshot overlay cancel]', e); }
  }, [ipc]);

  const hasRect = !!rect && rect.w >= MIN_REGION_PX && rect.h >= MIN_REGION_PX;

  return (
    <div
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onDoubleClick={onDoubleClick}
      onContextMenu={(e) => { e.preventDefault(); void submitCancel(); }}
      style={{
        position: 'fixed',
        inset: 0,
        cursor: 'crosshair',
        userSelect: 'none',
        overflow: 'hidden',
      }}
    >
      {/* 背景:抓屏 PNG 全屏铺 */}
      {imgUrl ? (
        <img
          ref={imgRef}
          src={imgUrl}
          alt="screen"
          draggable={false}
          onLoad={(e) => {
            const im = e.currentTarget;
            setNatural({ w: im.naturalWidth, h: im.naturalHeight });
          }}
          style={{
            position: 'absolute', inset: 0,
            width: '100%', height: '100%',
            objectFit: 'fill',  // 覆盖整窗,跟主屏 1:1
            pointerEvents: 'none',
          }}
        />
      ) : (
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'white', fontSize: 14, fontFamily: 'system-ui, sans-serif',
          background: 'rgba(0,0,0,0.6)',
        }}>
          {err ? `截图失败: ${err}` : '正在抓屏...'}
        </div>
      )}

      {/* dim 蒙版:整张 dim,被选区"挖空" */}
      {imgUrl && (
        <div
          style={{
            position: 'absolute', inset: 0,
            background: 'rgba(0,0,0,0.45)',
            pointerEvents: 'none',
            ...(rect ? {
              clipPath: `polygon(
                0 0,
                0 100%,
                ${rect.x}px 100%,
                ${rect.x}px ${rect.y}px,
                ${rect.x + rect.w}px ${rect.y}px,
                ${rect.x + rect.w}px ${rect.y + rect.h}px,
                ${rect.x}px ${rect.y + rect.h}px,
                ${rect.x}px 100%,
                100% 100%,
                100% 0
              )`,
            } : {}),
          }}
        />
      )}

      {/* 选区描边 + 工具栏 */}
      {rect && (
        <>
          <div
            style={{
              position: 'absolute',
              left: rect.x, top: rect.y, width: rect.w, height: rect.h,
              border: '2px solid #fbbf24',
              boxShadow: dragging ? '0 0 0 1px rgba(251,191,36,0.5)' : '0 4px 12px rgba(0,0,0,0.4)',
              pointerEvents: 'none',
            }}
          />
          {hasRect && !dragging && (
            <RegionToolbar
              rect={rect}
              naturalW={natural?.w ?? 0}
              naturalH={natural?.h ?? 0}
              dispW={typeof window !== 'undefined' ? window.innerWidth : 1}
              dispH={typeof window !== 'undefined' ? window.innerHeight : 1}
              confirming={confirming}
              onConfirm={() => void submitConfirm()}
              onCancel={() => void submitCancel()}
              onReset={() => setRect(null)}
            />
          )}
        </>
      )}

      {/* 中央提示:用户初次进来还没拖时,给一行操作说明,3 秒淡出 */}
      {!rect && imgUrl && <CenterHint />}
    </div>
  );
};

// ─── 选区工具栏(选区右下角浮动,跟 QQ 一致) ───────────────────────────

const RegionToolbar: React.FC<{
  rect: Rect;
  naturalW: number;
  naturalH: number;
  dispW: number;
  dispH: number;
  confirming: boolean;
  onConfirm(): void;
  onCancel(): void;
  onReset(): void;
}> = ({ rect, naturalW, naturalH, dispW, dispH, confirming, onConfirm, onCancel, onReset }) => {
  // 工具栏放在选区右下角下方;靠近底部时翻到选区上方
  const TOOLBAR_W = 280;
  const TOOLBAR_H = 36;
  const PAD = 8;
  const winW = typeof window !== 'undefined' ? window.innerWidth : 1920;
  const winH = typeof window !== 'undefined' ? window.innerHeight : 1080;
  // 默认右下方
  let left = rect.x + rect.w - TOOLBAR_W;
  let top = rect.y + rect.h + PAD;
  if (top + TOOLBAR_H > winH) {
    // 选区贴底了 → 浮到选区内部右下角(像 Lightshot)
    top = Math.max(0, rect.y + rect.h - TOOLBAR_H - PAD);
    left = Math.max(0, rect.x + rect.w - TOOLBAR_W - PAD);
  }
  left = Math.max(0, Math.min(left, winW - TOOLBAR_W));
  top = Math.max(0, Math.min(top, winH - TOOLBAR_H));

  // 真实裁剪尺寸(原图像素)显示
  const realW = Math.round(rect.w * (naturalW / dispW));
  const realH = Math.round(rect.h * (naturalH / dispH));

  const btnStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    height: 28,
    padding: '0 10px',
    borderRadius: 6,
    fontSize: 12,
    fontFamily: 'system-ui, sans-serif',
    cursor: 'pointer',
    border: 'none',
    color: 'white',
  };

  return (
    <div
      onMouseDown={(e) => e.stopPropagation()}  // 别让工具栏触发新一次拖框
      onClick={(e) => e.stopPropagation()}
      style={{
        position: 'absolute',
        left, top,
        width: TOOLBAR_W,
        height: TOOLBAR_H,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '0 8px',
        background: 'rgba(20,20,20,0.92)',
        borderRadius: 8,
        boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
        backdropFilter: 'blur(8px)',
      }}
    >
      <span style={{
        flex: 1, color: 'rgba(255,255,255,0.6)',
        fontSize: 11, fontFamily: 'monospace',
      }}>
        {realW} × {realH}
      </span>
      <button type="button" onClick={onReset}
        style={{ ...btnStyle, background: 'rgba(255,255,255,0.1)' }}
        title="重选 (R)"
      >
        重选
      </button>
      <button type="button" onClick={onCancel}
        style={{ ...btnStyle, background: 'rgba(255,255,255,0.1)' }}
        title="取消 (Esc / 右键)"
      >
        取消
      </button>
      <button type="button" onClick={onConfirm} disabled={confirming}
        style={{
          ...btnStyle,
          background: confirming ? 'rgba(34,197,94,0.5)' : '#22c55e',
          fontWeight: 600,
        }}
        title="确认 (Enter / 双击)"
      >
        {confirming ? '裁剪中…' : '✓ 确认'}
      </button>
    </div>
  );
};

const CenterHint: React.FC = () => {
  const [show, setShow] = React.useState(true);
  React.useEffect(() => {
    const t = setTimeout(() => setShow(false), 3500);
    return () => clearTimeout(t);
  }, []);
  if (!show) return null;
  return (
    <div style={{
      position: 'absolute',
      left: '50%', top: '50%',
      transform: 'translate(-50%, -50%)',
      padding: '10px 20px',
      background: 'rgba(0,0,0,0.7)',
      color: 'white',
      borderRadius: 8,
      fontSize: 13,
      fontFamily: 'system-ui, sans-serif',
      pointerEvents: 'none',
      animation: 'fadeOut 3.5s forwards',
    }}>
      鼠标拖动选区  ·  Enter 确认  ·  Esc / 右键取消
    </div>
  );
};
