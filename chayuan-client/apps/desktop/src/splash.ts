/**
 * Splash 启动动画 — 单一真源。
 *
 * 双重挂载策略(对齐 VSCode / Linear / Tauri 官方示例的工程实践):
 *
 *   层 1 (HTML inline,见 index.html):body 第一个子元素 + <head> 内联 CSS。
 *   优点是 HTML 解析到 body 就立刻可见,bundle 还没下载就有动画。
 *
 *   层 2 (本模块 ensureSplash,自检注入):main.tsx 入口最早调用,如果发现
 *   ``#chayuan-splash`` 不存在(stale dist / 第三方注入器移除 / SSR mismatch
 *   …任何意外原因),立刻 createElement 一份等价节点 + 注入 CSS。同 id 同
 *   class,fade out 路径完全复用。
 *
 * 真正的隐藏统一走 ``hideSplash``:由 Shell 在 useLayoutEffect 首屏 commit 时
 * 触发(react first paint),CSS transition 360ms 淡出,420ms 后从 DOM 移除。
 * 8s 安全兜底由 main.tsx 注册,防 Shell 永远挂不上来时 splash 永卡。
 */

const SPLASH_ID = 'chayuan-splash';
const STYLE_ID = 'chayuan-splash-style';
const FADING_CLASS = 'cy-splash-fading';

// CSS 与 index.html 内联段保持一致,本模块作为 fallback 注入时使用。
// 改动两处都要同步;考虑过抽出成 .css 文件 import,但那样首屏要等 CSS
// stylesheet 下载,失去 splash 即时可见的意义,因此宁可重复维护。
const SPLASH_CSS = `
#chayuan-splash {
  position: fixed;
  inset: 0;
  z-index: 2147483646;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background:
    radial-gradient(ellipse at center,
      rgba(99,102,241,0.20), rgba(14,165,233,0.10) 45%, transparent 70%),
    #0f172a;
  opacity: 1;
  transition: opacity 360ms ease-out;
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
    "Microsoft YaHei", "Helvetica Neue", sans-serif;
}
#chayuan-splash.cy-splash-fading { opacity: 0; pointer-events: none; }
#chayuan-splash * { box-sizing: border-box; }

.cy-splash-ring {
  position: absolute;
  width: 132px; height: 132px;
  border-radius: 50%;
  background: conic-gradient(from 0deg, #6366f1, #06b6d4, #ec4899, #f59e0b, #6366f1);
  animation: cy-splash-spin 2.4s linear infinite;
  filter: blur(0.5px);
  -webkit-mask: radial-gradient(circle, transparent 56px, black 58px, black 64px, transparent 66px);
          mask: radial-gradient(circle, transparent 56px, black 58px, black 64px, transparent 66px);
}
.cy-splash-orbits { position: absolute; animation: cy-splash-spin-rev 3.8s linear infinite; }
.cy-splash-orbit { position: absolute; display: block; border-radius: 50%; }
.cy-splash-o1 {
  width: 10px; height: 10px; background: #6366f1;
  box-shadow: 0 0 12px #6366f1, 0 0 22px rgba(99,102,241,0.55);
  animation: cy-splash-orbit-1 2.2s linear infinite;
}
.cy-splash-o2 {
  width: 8px; height: 8px; background: #06b6d4;
  box-shadow: 0 0 10px #06b6d4, 0 0 20px rgba(6,182,212,0.55);
  animation: cy-splash-orbit-2 2.8s linear infinite;
}
.cy-splash-o3 {
  width: 7px; height: 7px; background: #ec4899;
  box-shadow: 0 0 10px #ec4899, 0 0 20px rgba(236,72,153,0.55);
  animation: cy-splash-orbit-3 3.4s linear infinite;
}
.cy-splash-core {
  position: relative;
  display: flex; align-items: center; justify-content: center;
  overflow: hidden;
  width: 56px; height: 56px;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, #a5b4fc, #6366f1 60%, #4338ca);
  box-shadow: 0 0 24px rgba(99,102,241,0.55), inset 0 0 12px rgba(255,255,255,0.35);
  animation: cy-splash-pulse 1.6s ease-in-out infinite;
}
.cy-splash-shimmer {
  position: absolute; top: 0; bottom: 0; width: 60%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.55), transparent);
  animation: cy-splash-shimmer 1.8s ease-in-out infinite;
  filter: blur(2px);
}
.cy-splash-tag {
  position: absolute;
  bottom: 8vh; left: 0; right: 0;
  text-align: center;
  font-size: 13px; font-weight: 500; line-height: 1.6;
  color: rgba(226,232,240,0.85);
  letter-spacing: 0.06em;
}
.cy-splash-tag-sub { margin-top: 6px; font-size: 11px; color: rgba(148,163,184,0.85); }

@keyframes cy-splash-spin     { to { transform: rotate(360deg); } }
@keyframes cy-splash-spin-rev { to { transform: rotate(-360deg); } }
@keyframes cy-splash-pulse {
  0%,100% { transform: scale(1);    opacity: .85; }
  50%     { transform: scale(1.18); opacity: 1; }
}
@keyframes cy-splash-orbit-1 {
  from { transform: rotate(0deg)   translateX(28px) rotate(0deg); }
  to   { transform: rotate(360deg) translateX(28px) rotate(-360deg); }
}
@keyframes cy-splash-orbit-2 {
  from { transform: rotate(120deg) translateX(40px) rotate(-120deg); }
  to   { transform: rotate(480deg) translateX(40px) rotate(-480deg); }
}
@keyframes cy-splash-orbit-3 {
  from { transform: rotate(240deg) translateX(34px) rotate(-240deg); }
  to   { transform: rotate(600deg) translateX(34px) rotate(-600deg); }
}
@keyframes cy-splash-shimmer {
  0%   { transform: translateX(-120%); }
  100% { transform: translateX( 120%); }
}
`;

const SPLASH_INNER_HTML = `
  <div class="cy-splash-ring"></div>
  <div class="cy-splash-orbits">
    <span class="cy-splash-orbit cy-splash-o1"></span>
    <span class="cy-splash-orbit cy-splash-o2"></span>
    <span class="cy-splash-orbit cy-splash-o3"></span>
  </div>
  <div class="cy-splash-core">
    <span class="cy-splash-shimmer"></span>
  </div>
  <div class="cy-splash-tag">
    察元 AI
    <div class="cy-splash-tag-sub">正在加载…</div>
  </div>
`;

/**
 * 自检注入 splash —— main.tsx 入口最早调用。
 * 幂等:HTML 已经把 splash 节点 + CSS 内联好的话,本函数 no-op;
 * 任何缺失情况下,程序化补齐 CSS + 把节点插到 body 第一个子元素的位置
 * (与 ``<div id="app">`` 同级,与原 HTML 形态视觉等价)。
 */
export function ensureSplash(): void {
  if (typeof document === 'undefined') return;

  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = SPLASH_CSS;
    document.head.appendChild(style);
  }

  if (document.getElementById(SPLASH_ID)) return;

  const splash = document.createElement('div');
  splash.id = SPLASH_ID;
  splash.setAttribute('aria-busy', 'true');
  splash.setAttribute('aria-live', 'polite');
  splash.innerHTML = SPLASH_INNER_HTML;

  // 插到 body 第一个子元素的位置,确保比 #app 抢先绘制 —— Tauri WebView 增量
  // paint 时,如果 #app(100vh×100vw)先入 DOM,会先画一帧空 #app,下一帧才
  // 把 splash 叠上,中间有可见空白瞬态。splash 抢先入 DOM,首帧就是动画。
  // React 通过 getElementById('app') 拿挂载点,与 DOM 顺序无关。
  const insert = (): void => {
    if (!document.body) return;
    if (document.getElementById(SPLASH_ID)) return; // 并发 race 防御
    document.body.insertBefore(splash, document.body.firstChild);
  };

  if (document.body) {
    insert();
  } else {
    document.addEventListener('DOMContentLoaded', insert, { once: true });
  }
}

/**
 * 隐藏 splash —— Shell 在 useLayoutEffect 首屏 commit 时通过
 * ``window.__cyHideSplash`` 触发;main.tsx 的 8s 安全兜底也走这里。
 * 幂等:重复调用不会触发二次淡出。
 */
export function hideSplash(): void {
  if (typeof document === 'undefined') return;
  const splash = document.getElementById(SPLASH_ID);
  if (!splash || splash.classList.contains(FADING_CLASS)) return;
  splash.classList.add(FADING_CLASS);
  window.setTimeout(() => splash.remove(), 420);
}
