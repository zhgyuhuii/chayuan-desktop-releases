/**
 * Splash 启动动画 — 与 apps/desktop/src/splash.ts 保持一致;web 端单文件副本。
 * 改动须双方同步。详见 desktop 端文件头注释。
 */

const SPLASH_ID = 'chayuan-splash';
const STYLE_ID = 'chayuan-splash-style';
const FADING_CLASS = 'cy-splash-fading';

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

  // 插到 body 第一个子元素位置,确保比 #app 抢先绘制;详见 desktop 端注释。
  const insert = (): void => {
    if (!document.body) return;
    if (document.getElementById(SPLASH_ID)) return;
    document.body.insertBefore(splash, document.body.firstChild);
  };

  if (document.body) {
    insert();
  } else {
    document.addEventListener('DOMContentLoaded', insert, { once: true });
  }
}

export function hideSplash(): void {
  if (typeof document === 'undefined') return;
  const splash = document.getElementById(SPLASH_ID);
  if (!splash || splash.classList.contains(FADING_CLASS)) return;
  splash.classList.add(FADING_CLASS);
  window.setTimeout(() => splash.remove(), 420);
}
