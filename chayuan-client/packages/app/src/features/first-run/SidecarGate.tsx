/**
 * Sidecar 启动门禁(Phase 3)。
 *
 * 在 ``FirstRunSetup`` 之后、主路由之前;Shell 把数据目录交给 sidecar,
 * 这里负责显示进度/失败 UI 并在 ``ready`` 后回调 ``onReady(baseUrl)``。
 *
 * 行为:
 *   - ``platform.sidecar`` 不存在(Web 构建)→ 直接 onReady,使用默认 ``baseUrl``
 *     (兜底:Web 构建一般不依赖本地后端,Shell 已有 ``apiBaseOverride`` 路径)
 *   - ``status() === 'disabled'``(dev 模式)→ 直接 onReady,前端走 62581 等
 *     开发者自己跑 ``poetry run chayuan start -a``
 *   - 否则:订阅事件 + 调 ``start()``,UI 显示启动日志,直至 state=ready 调
 *     ``onReady``;state=failed 时停在错误屏(显示退出码 / 最后 log + "重试")
 */

import { Button } from '@chayuan/ui';
import { AlertTriangle } from 'lucide-react';
import * as React from 'react';
import { getPlatform } from '@chayuan/platform-shared';
import type { SidecarStatus } from '@chayuan/platform-shared';
import { CHAYUAN_LOGO_URL } from '../../lib/brandAssets';

export interface SidecarGateProps {
  /** FirstRunSetup 选定的数据目录;sidecar 通过 CHAYUAN_ROOT 注入子进程 */
  dataDir: string;
  /** sidecar ready 后回调,Shell 据此 configureClient + 挂主路由 */
  onReady(baseUrl: string): void;
}

export const SidecarGate: React.FC<SidecarGateProps> = ({ dataDir, onReady }) => {
  const [status, setStatus] = React.useState<SidecarStatus | null>(null);
  const onReadyRef = React.useRef(onReady);
  React.useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  const startOnce = React.useCallback(async () => {
    const sidecar = getPlatform().sidecar;
    if (!sidecar) {
      // Web / 未注入 → 直接放行(默认 apiBase 由 ShellEnv 提供)
      onReadyRef.current('');
      return;
    }
    try {
      const initial = await sidecar.status();
      setStatus(initial);
      // ready / disabled:直接放行进主界面
      if (initial.state === 'ready' || initial.state === 'disabled') {
        onReadyRef.current(initial.baseUrl);
        return;
      }
      const next = await sidecar.start({ dataDir });
      setStatus(next);
      if (next.state === 'ready' || next.state === 'disabled') {
        onReadyRef.current(next.baseUrl);
      }
    } catch (e) {
      setStatus((s) =>
        s
          ? { ...s, state: 'failed', error: (e as Error)?.message ?? String(e) }
          : null,
      );
    }
  }, [dataDir]);

  React.useEffect(() => {
    const sidecar = getPlatform().sidecar;
    if (!sidecar) {
      // Web → 立即放行
      onReadyRef.current('');
      return;
    }
    let unlisten: (() => void) | null = null;
    void (async () => {
      // 故意不订阅 onLog —— sidecar stdout 含随机生成的配置面板凭据
      // (密码 / login-route-token),不应让前端任何 UI 持有。诊断走 status.error
      // 一行就够。
      unlisten = await sidecar.subscribe({
        onState: async (state) => {
          // 状态变化:重新拉一次 status 拿最新 baseUrl / error
          const cur = await sidecar.status().catch(() => null);
          if (cur) setStatus(cur);
          if (state === 'ready' || state === 'disabled') {
            onReadyRef.current(cur?.baseUrl ?? '');
          }
        },
      });
      await startOnce();
    })();
    return () => {
      try {
        unlisten?.();
      } catch {
        /* noop */
      }
    };
  }, [startOnce]);

  // 渲染:starting / failed(ready / disabled 已在 effect 里 onReady,组件即将卸载)
  const state = status?.state ?? 'idle';

  if (state === 'failed') {
    return (
      <div className="relative h-screen w-screen overflow-hidden bg-[var(--cy-surface-2,#f8fafc)]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_25%,rgba(248,113,113,0.10),transparent_32%),radial-gradient(circle_at_75%_75%,rgba(251,146,60,0.10),transparent_30%)]" />
        <div className="absolute inset-0 flex items-center justify-center px-6">
          <div className="w-full max-w-xl rounded-3xl border border-[var(--cy-border-subtle,#e2e8f0)] bg-[var(--cy-surface-base,#fff)]/95 p-8 shadow-[var(--cy-shadow-lg,0_24px_80px_rgba(15,23,42,0.12))] backdrop-blur-xl">
            <div className="mb-3 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-red-500 text-white">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-[var(--cy-text-primary,#0f172a)]">
                  后端服务启动失败
                </h1>
                <p className="text-xs text-[var(--cy-text-tertiary,#94a3b8)]">
                  数据目录:{status?.dataDir || dataDir}
                </p>
              </div>
            </div>
            {status?.error && (
              <pre className="mt-3 whitespace-pre-wrap break-words rounded-xl bg-[var(--cy-surface-1,#f1f5f9)] p-3 text-xs text-[var(--cy-text-secondary,#475569)]">
                {status.error}
              </pre>
            )}
            <div className="mt-6 flex justify-end">
              <Button size="sm" onClick={() => void startOnce()}>
                重试启动
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[var(--cy-surface-2,#f8fafc)]">
      {/* 渐变光斑背景 —— pulse 慢呼吸增加层次感 */}
      <div
        className="absolute inset-0 animate-pulse bg-[radial-gradient(circle_at_30%_25%,rgba(99,102,241,0.18),transparent_38%),radial-gradient(circle_at_75%_75%,rgba(14,165,233,0.18),transparent_35%)]"
        style={{ animationDuration: '6s' }}
      />

      <div className="absolute inset-0 flex flex-col items-center justify-center gap-8 px-6">
        {/* 中央品牌 logo —— 脉动光晕 + 真实 logo.png */}
        <div className="relative">
          {/* 外圈光晕(脉动) */}
          <div
            className="absolute -inset-8 animate-ping rounded-full bg-[var(--cy-brand-500,#6366f1)]/30 blur-2xl"
            style={{ animationDuration: '2.4s' }}
          />
          {/* 主体卡片 — 用项目 logo.png(packages/app/src/images/logo.png) */}
          <div className="relative flex h-24 w-24 items-center justify-center rounded-3xl bg-white shadow-[0_20px_60px_rgba(99,102,241,0.45)] ring-1 ring-[var(--cy-border-subtle,#e2e8f0)]">
            <img
              src={CHAYUAN_LOGO_URL}
              alt="察元 AI"
              className="h-16 w-16 object-contain"
              draggable={false}
            />
          </div>
        </div>

        {/* 标题 + 动态点点 */}
        <div className="text-center">
          <h1 className="text-xl font-semibold text-[var(--cy-text-primary,#0f172a)] sm:text-2xl">
            正在启动
            <span className="ml-1 inline-flex">
              <span className="animate-bounce" style={{ animationDelay: '-0.3s' }}>.</span>
              <span className="animate-bounce" style={{ animationDelay: '-0.15s' }}>.</span>
              <span className="animate-bounce">.</span>
            </span>
          </h1>
          <p className="mt-2 text-sm text-[var(--cy-text-tertiary,#94a3b8)]">
            首次启动较慢，请稍候
          </p>
        </div>

        {/* 不确定进度条:无限循环左→右滑 */}
        <div className="relative h-1.5 w-72 overflow-hidden rounded-full bg-[var(--cy-surface-1,#e2e8f0)]">
          <div
            className="absolute inset-y-0 w-1/3 rounded-full bg-gradient-to-r from-[var(--cy-brand-500,#6366f1)] to-[var(--cy-brand-700,#4338ca)] cy-loader-slide"
          />
        </div>

      </div>

      {/* keyframes —— 进度条左右循环;Tailwind 没现成 indeterminate animation 用内联 */}
      <style>{`
        @keyframes cy-loader-slide {
          0%   { transform: translateX(-100%); }
          50%  { transform: translateX(220%); }
          100% { transform: translateX(-100%); }
        }
        .cy-loader-slide {
          animation: cy-loader-slide 1.8s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
};
