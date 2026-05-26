/**
 * TanStack Router 路由树(M1 多 Tab 简化版)。
 *
 * 关键变更:
 *   - 所有"工作区一级路径"(/home /chat /kb /marketplace /history /settings /admin/* /tools /mcp)
 *     都共用同一个 Chrome + TabHost。
 *   - 登录改为 LoginModal(参考图 登录页.jpg);无独立全屏登录页。
 *   - 游客可直接进首页;受限页(admin)未登录 → 自动唤起登录弹框。
 *
 * 路由:
 *   /                         redirect → /home
 *   /login                    兼容旧 URL:redirect → /home,Chrome 自动开弹框
 *   /$splat                   工作区,Chrome 渲染 TabHost
 */

import type { QueryClient } from '@tanstack/react-query';
import {
  type AnyRoute,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
  useNavigate,
} from '@tanstack/react-router';
import * as React from 'react';
import { useAuthStore } from '../store/auth';
import { useLoginModalStore } from '../store/loginModal';

const Chrome = React.lazy(() => import('./Chrome').then((m) => ({ default: m.Chrome })));

/**
 * 文件预览独立子窗(Tauri WebviewWindow / web popup)的根。
 * 不走 Chrome 也不走 LoginModal — 复用主窗鉴权(同 origin)。
 */
const PreviewStandaloneRoute = React.lazy(() =>
  import('../features/preview').then((m) => ({ default: m.PreviewStandaloneRoute })),
);

/** 96-7:模型对抗永道拖出后的独立窗口 */
const DetachedLaneRoute = React.lazy(() =>
  import('../features/model-arena/DetachedLaneRoute').then((m) => ({
    default: m.DetachedLaneRoute,
  })),
);

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <React.Suspense fallback={<RouteFallback />}>
      <Outlet />
    </React.Suspense>
  ),
});

/**
 * /login 兼容路由:跳到 /home,并在主页打开 LoginModal。
 * 已登录则直接进 /home 不开弹框。
 */
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  beforeLoad: async () => {
    const auth = useAuthStore.getState();
    if (!auth.hydrated) await auth.hydrate();
    if (!auth.isLoggedIn) {
      // 异步打开,避免在 redirect 抛出前同步触发渲染
      queueMicrotask(() => useLoginModalStore.getState().show());
    }
    throw redirect({ to: '/home' });
  },
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/home' });
  },
});

const previewWindowRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/preview-window',
  component: () => (
    <React.Suspense fallback={<RouteFallback />}>
      <PreviewStandaloneRoute />
    </React.Suspense>
  ),
});

/** 96-7:模型对抗拖出窗口路径 — `/arena/detached?lane=<id>` */
const arenaDetachedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/arena/detached',
  component: () => (
    <React.Suspense fallback={<RouteFallback />}>
      <DetachedLaneRoute />
    </React.Suspense>
  ),
});

/**
 * 工作区根:仅 hydrate auth,不强制登录(游客可见首页)。
 * 受限子路由(如 admin)在自己的守卫里唤起 LoginModal。
 */
const workspaceLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_workspace',
  beforeLoad: async () => {
    const auth = useAuthStore.getState();
    if (!auth.hydrated) await auth.hydrate();
  },
  component: () => (
    <React.Suspense fallback={<RouteFallback />}>
      <Chrome />
    </React.Suspense>
  ),
});

/**
 * 一组"显式"路径 —— 让 router 类型 + prefetch + 浏览器历史友好。
 * 全部委托给 Chrome(TabHost 内部按 path 解析渲染)。
 */
const workspaceRoutes: ReadonlyArray<{ path: string; id: string }> = [
  { path: '/home', id: 'home' },
  { path: '/chat', id: 'chat' },
  { path: '/chat/$conversationId', id: 'chat-by-id' },
  { path: '/kb', id: 'kb' },
  { path: '/kb/$kuId', id: 'kb-by-id' },
  { path: '/marketplace', id: 'marketplace' },
  { path: '/skill/$slug', id: 'skill' },
  { path: '/history', id: 'history' },
  { path: '/annotation', id: 'annotation' },
  { path: '/settings', id: 'settings' },
  { path: '/tools', id: 'tools' },
  { path: '/mcp', id: 'mcp' },
  { path: '/help', id: 'help' },
  { path: '/notes/new', id: 'notes-new' },
  { path: '/image-to-text', id: 'image-to-text' },
  { path: '/audio-to-text', id: 'audio-to-text' },
  // 在线办公占位页 /online/<feature> — 必须在此登记,否则 navigate() 经
  // TanStack Router 找不到路由会 404「页面不存在」;真正渲染走 page-registry。
  { path: '/online/$feature', id: 'online-feature' },
];

const tabRoutes = workspaceRoutes.map((r) =>
  createRoute({
    getParentRoute: () => workspaceLayoutRoute,
    path: r.path,
    component: () => null, // Chrome 已渲染;此 component 不参与
  }),
);

/* Admin 路由 —— 未登录唤起 LoginModal 而非跳走;非 admin 角色回 /home */
const adminLayoutRoute = createRoute({
  getParentRoute: () => workspaceLayoutRoute,
  id: '_admin-guard',
  beforeLoad: async () => {
    const auth = useAuthStore.getState();
    if (!auth.hydrated) await auth.hydrate();
    if (!auth.isLoggedIn) {
      queueMicrotask(() => useLoginModalStore.getState().show());
      throw redirect({ to: '/home' });
    }
    if (auth.user?.role !== 'admin') throw redirect({ to: '/home' });
  },
  component: () => null,
});

const adminTabRoutes = (['traces', 'outbox', 'prompts', 'platforms'] as const).map((tab) =>
  createRoute({
    getParentRoute: () => adminLayoutRoute,
    path: `/admin/${tab}`,
    component: () => null,
  }),
);

const adminRedirectRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: '/admin',
  beforeLoad: () => {
    throw redirect({ to: '/admin/traces' });
  },
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  indexRoute,
  previewWindowRoute,
  arenaDetachedRoute,
  workspaceLayoutRoute.addChildren([
    ...(tabRoutes as unknown as AnyRoute[]),
    adminLayoutRoute.addChildren([
      adminRedirectRoute,
      ...(adminTabRoutes as unknown as AnyRoute[]),
    ] as unknown as AnyRoute[]),
  ] as unknown as AnyRoute[]),
]);

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    // 任何 loader / 子树渲染抛错都走这里,把错误推到全局 ErrorDialog 而不是裸页
    defaultErrorComponent: RouteErrorComponent,
    // 未匹配路径(如 /chat/<bad-id> 或拼错链接)不显示 router 默认 404,
    // 直接 redirect 回 /home,让用户从首页重新出发
    defaultNotFoundComponent: NotFoundRedirect,
  });
}

const NotFoundRedirect: React.FC = () => {
  const navigate = useNavigate();

  React.useEffect(() => {
    void import('../store/errorDialog').then(({ useErrorDialog }) => {
      useErrorDialog
        .getState()
        .show('页面不存在', '链接可能已失效,已为你回到首页', undefined, 3000);
    });
    void navigate({ to: '/home', replace: true });
  }, [navigate]);

  return (
    <div className="flex h-screen items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
      页面不存在,正在返回首页…
    </div>
  );
};

const RouteErrorComponent: React.FC<{ error: unknown; reset?: () => void }> = ({
  error,
  reset,
}) => {
  React.useEffect(() => {
    void import('../store/errorDialog').then(({ reportError }) =>
      reportError(error, '页面渲染失败'),
    );
  }, [error]);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-sm text-[var(--cy-text-secondary)]">页面渲染失败,详情见弹窗</p>
      {reset && (
        <button
          type="button"
          onClick={reset}
          className="rounded-full border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 py-1 text-xs hover:bg-[var(--cy-surface-2)]"
        >
          重试
        </button>
      )}
    </div>
  );
};

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}

/**
 * 路由懒加载兜底动画。三层:
 *   - 外圈:渐变 conic 环旋转(loader-ring)
 *   - 中圈:三粒轨道粒子,反向不同速,色相错峰
 *   - 内核:呼吸辉光 + 光斑扫扫
 * 全部纯 CSS keyframes,无依赖、无 Tailwind config 改动。
 */
export const RouteFallback: React.FC = () => (
  <div
    className="relative flex h-screen w-full items-center justify-center overflow-hidden"
    style={{
      background:
        'radial-gradient(ellipse at center, rgba(99,102,241,0.06), rgba(14,165,233,0.04) 45%, transparent 70%)',
    }}
  >
    <style>{`
      @keyframes cy-loader-spin { to { transform: rotate(360deg); } }
      @keyframes cy-loader-spin-rev { to { transform: rotate(-360deg); } }
      @keyframes cy-loader-pulse {
        0%,100% { transform: scale(1); opacity: 0.85; }
        50%     { transform: scale(1.18); opacity: 1; }
      }
      @keyframes cy-loader-orbit-1 {
        from { transform: rotate(0deg) translateX(28px) rotate(0deg); }
        to   { transform: rotate(360deg) translateX(28px) rotate(-360deg); }
      }
      @keyframes cy-loader-orbit-2 {
        from { transform: rotate(120deg) translateX(40px) rotate(-120deg); }
        to   { transform: rotate(480deg) translateX(40px) rotate(-480deg); }
      }
      @keyframes cy-loader-orbit-3 {
        from { transform: rotate(240deg) translateX(34px) rotate(-240deg); }
        to   { transform: rotate(600deg) translateX(34px) rotate(-600deg); }
      }
      @keyframes cy-loader-shimmer {
        0%   { transform: translateX(-120%); }
        100% { transform: translateX(120%); }
      }
    `}</style>

    {/* 外圈渐变环 */}
    <div
      className="absolute"
      style={{
        width: 132,
        height: 132,
        borderRadius: '50%',
        background: 'conic-gradient(from 0deg, #6366f1, #06b6d4, #ec4899, #f59e0b, #6366f1)',
        animation: 'cy-loader-spin 2.4s linear infinite',
        filter: 'blur(0.5px)',
        WebkitMask:
          'radial-gradient(circle, transparent 56px, black 58px, black 64px, transparent 66px)',
        mask: 'radial-gradient(circle, transparent 56px, black 58px, black 64px, transparent 66px)',
      }}
    />

    {/* 三粒轨道粒子(反向旋转 + 不同色) */}
    <div className="absolute" style={{ animation: 'cy-loader-spin-rev 3.8s linear infinite' }}>
      <span
        className="absolute block rounded-full"
        style={{
          width: 10,
          height: 10,
          background: '#6366f1',
          boxShadow: '0 0 12px #6366f1, 0 0 22px rgba(99,102,241,0.55)',
          animation: 'cy-loader-orbit-1 2.2s linear infinite',
        }}
      />
      <span
        className="absolute block rounded-full"
        style={{
          width: 8,
          height: 8,
          background: '#06b6d4',
          boxShadow: '0 0 10px #06b6d4, 0 0 20px rgba(6,182,212,0.55)',
          animation: 'cy-loader-orbit-2 2.8s linear infinite',
        }}
      />
      <span
        className="absolute block rounded-full"
        style={{
          width: 7,
          height: 7,
          background: '#ec4899',
          boxShadow: '0 0 10px #ec4899, 0 0 20px rgba(236,72,153,0.55)',
          animation: 'cy-loader-orbit-3 3.4s linear infinite',
        }}
      />
    </div>

    {/* 内核:呼吸辉光 + 光斑扫光 */}
    <div
      className="relative flex items-center justify-center overflow-hidden rounded-full"
      style={{
        width: 56,
        height: 56,
        background: 'radial-gradient(circle at 30% 30%, #a5b4fc, #6366f1 60%, #4338ca)',
        boxShadow: '0 0 24px rgba(99,102,241,0.55), inset 0 0 12px rgba(255,255,255,0.35)',
        animation: 'cy-loader-pulse 1.6s ease-in-out infinite',
      }}
    >
      <span
        className="absolute inset-y-0"
        style={{
          width: '60%',
          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.55), transparent)',
          animation: 'cy-loader-shimmer 1.8s ease-in-out infinite',
          filter: 'blur(2px)',
        }}
      />
    </div>
  </div>
);
