import * as React from 'react';
import { createRoot } from 'react-dom/client';
import { setPlatform } from '@chayuan/platform-shared';
import { createTauriPlatform } from '@chayuan/platform-tauri';
import { Shell, ScreenshotOverlayApp } from '@chayuan/app';
import '@chayuan/ui/styles.css';
import { ensureSplash, hideSplash } from './splash';
import { initRuntimeSync } from './runtimeSync';

// ── 截图选区子窗口分支(微信 / QQ 风格的系统级截图)─────────────────────
// 主窗用 ``WebviewWindow`` 创建一个 label=screenshot-overlay 的无边框透明窗,
// 加载 ``/screenshot-overlay`` 路径。本入口检测到这个路径,**不挂 Shell**(没
// router / 鉴权 / sidecar / i18n …),也不加 splash — 直接渲染选区组件。
// 启动毫秒级,内存几 MB,关窗即销毁。
const IS_SCREENSHOT_OVERLAY =
  typeof window !== 'undefined' && window.location.pathname.startsWith('/screenshot-overlay');

if (IS_SCREENSHOT_OVERLAY) {
  // overlay 窗不需要主窗的 i18n / Shell / sidecar gate,直接 mount。
  // ipc 在这里就近注入 — @chayuan/app 包没有 @tauri-apps/api 依赖,所以把
  // invoke 包成 ScreenshotOverlayIpc 喂给组件,组件本身 platform-agnostic。
  const el = document.getElementById('app') ?? (() => {
    const e = document.createElement('div');
    e.id = 'app';
    document.body.appendChild(e);
    return e;
  })();
  const ipc = {
    async loadBuffer(): Promise<Uint8Array> {
      const { invoke } = await import('@tauri-apps/api/core');
      const bytes = await invoke<number[]>('chayuan_take_screenshot_buffer');
      return new Uint8Array(bytes);
    },
    async submit(bytes: Uint8Array | null): Promise<void> {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('chayuan_submit_screenshot_result', {
        bytes: bytes ? Array.from(bytes) : null,
      });
    },
  };
  createRoot(el).render(<ScreenshotOverlayApp ipc={ipc} />);
} else {

// 入口最早调用:HTML 已经内联 splash 时本函数 no-op;任何原因导致 dist
// HTML 缺失 splash 节点时(stale -BundleOnly 缓存 / 第三方注入器 / SSR
// mismatch …) 这里立刻程序化补一个,确保用户看到的"首屏永远不空白"。
// 与 #app 同级 sibling,DOM 顺序在前。
ensureSplash();

const APP_VERSION =
  // @ts-expect-error injected by vite define
  typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.1.0';

// 瘦客户端模式（thin desktop）：
// 通过 ``VITE_THIN_CLIENT=1`` 在构建时切换到 "登录到远端服务器" 形态。
// 在这种形态下：
//   * 不附带任何 Python sidecar（产物只有 webview shell + js bundle，~15 MB）
//   * 启动后弹出 ``ServerLoginModal``，由用户填入远端服务地址 + 凭据
//   * 默认 ``apiBase`` 留空（让 ``apiBaseOverride`` 接管）
//
// 单机桌面默认形态(THIN_CLIENT=0):sidecar 永远跑在 127.0.0.1:62581(sidecar.rs
// 写死的 DEFAULT_PORT,IP 固定),写死即可,不再走 SidecarGate → apiBaseOverride
// 这条间接链路 — 减少首屏 race。VITE_API_BASE 仍然支持在 dev 时本地覆盖。
const THIN_CLIENT = String(import.meta.env.VITE_THIN_CLIENT || '') === '1';
const SINGLE_MACHINE_BASE = 'http://127.0.0.1:62581';
const FALLBACK_API_BASE =
  import.meta.env.VITE_API_BASE || (THIN_CLIENT ? '' : SINGLE_MACHINE_BASE);

/**
 * splash 收尾 — 单轨:内联 splash DOM。
 *
 * 主窗口 tauri.conf.json 固定 visible:true,启动即显示;首帧就是
 * index.html 解析出的 #chayuan-splash CSS 动画(零 JS、零 bundle 依赖)。
 * React 首屏 commit 后 Shell 通过 window.__cyHideSplash 触发 hideSplash()
 * 把它 360ms 淡出 + 移除。
 *
 * 旧的"独立 splashscreen 窗口 + invoke('chayuan_close_splashscreen')"双轨
 * 已废弃 —— 两个 webview + 窗口切换闪烁不值当,单窗口内联更干净。
 *
 * 8s 安全兜底:Shell 万一永远挂不上来,也强制移除 splash 让用户看到界面。
 */
function dismissSplash(): void {
  hideSplash();
}
(window as unknown as { __cyHideSplash?: () => void }).__cyHideSplash =
  dismissSplash;
window.setTimeout(dismissSplash, 8000);

(async () => {
  const platform = await createTauriPlatform({
    appName: '察元 AI',
    appVersion: APP_VERSION,
    defaultApiBase: FALLBACK_API_BASE,
  });
  setPlatform(platform);

  const root = createRoot(document.getElementById('app')!);
  root.render(
    <React.StrictMode>
      <Shell
        env={{
          apiBase: FALLBACK_API_BASE,
          thinClient: THIN_CLIENT,
          langfuse: {
            enabled: !!import.meta.env.VITE_LF_PUBLIC_KEY,
            host: import.meta.env.VITE_LF_HOST ?? '',
            publicKey: import.meta.env.VITE_LF_PUBLIC_KEY ?? '',
            projectId: import.meta.env.VITE_LF_PROJECT_ID ?? '',
            env: (import.meta.env.MODE as 'dev' | 'staging' | 'prod') ?? 'dev',
          },
        }}
      />
    </React.StrictMode>,
  );

  try {
    initRuntimeSync(APP_VERSION);
  } catch {
    /* 静默 */
  }
})();
}  // end else (非 overlay 子窗主分支)
