/**
 * 路径 → 页面组件 注册表(M1 多 Tab 工作区核心)。
 *
 * 设计:
 *   - 每个一级路径对应一个 lazy 加载的页面组件;
 *   - render() 返回 ReactNode,以及 onActivate(可选)用于 Tab 激活时同步副作用
 *     (例:Chat 路径同步 composerStore.activeConversationId)。
 *
 * 添加新页型:
 *   1. 在 routes 文件夹写好 PageComponent;
 *   2. 在此追加 RouteEntry;
 *   3. Sidebar 里加 NavLink 调用 useTabsStore.open(path)。
 */

import * as React from 'react';
import { tabsStaticTitle } from './tab-titles';
import { useTabsStore } from '../../store/tabs';

export interface RouteParams {
  [key: string]: string;
}

export interface RouteEntry {
  /** 静态前缀(优先精确匹配)或正则 */
  pattern: RegExp;
  /** 返回 React 节点 */
  render(params: RouteParams, path: string): React.ReactNode;
  /** Tab 激活时执行的副作用(此处可读取 store 设置 active 资源 id 等) */
  onActivate?(params: RouteParams, path: string): void;
  /** Tab 默认标题(可被 open 时的 opts.title 覆盖)*/
  defaultTitle?: string;
  /** 默认图标(lucide 名);Sidebar / TabBar 都可读 */
  defaultIcon?: string;
}

const HomePage = React.lazy(() =>
  import('../home/HomePage').then((m) => ({ default: m.HomePage })),
);
const ChatPage = React.lazy(() =>
  import('../chat/ChatPage').then((m) => ({ default: m.ChatPage })),
);
const KbPage = React.lazy(() => import('../kb/KbBoard').then((m) => ({ default: m.KbBoard })));
const KbDetailPage = React.lazy(() =>
  import('../kb/detail/KbDetailPage').then((m) => ({ default: m.KbDetailPage })),
);
const MarketplacePage = React.lazy(() =>
  import('../marketplace/MarketplacePage').then((m) => ({ default: m.MarketplacePage })),
);
const HistoryPage = React.lazy(() =>
  import('../history/HistoryPage').then((m) => ({ default: m.HistoryPage })),
);
// 单机版移除训练数据中心:
// const AnnotationPage = React.lazy(() =>
//   import('../annotation/AnnotationPage').then((m) => ({ default: m.AnnotationPage })),
// );
const SettingsAsPage = React.lazy(() =>
  import('../placeholders/SettingsAsPage').then((m) => ({ default: m.SettingsAsPage })),
);
const McpPage = React.lazy(() => import('../mcp/McpBoard').then((m) => ({ default: m.McpBoard })));
const ToolsPage = React.lazy(() =>
  import('../tools/ToolsBoard').then((m) => ({ default: m.ToolsBoard })),
);
const AdminPage = React.lazy(() =>
  import('../admin/AdminPage').then((m) => ({ default: m.AdminPage })),
);
const SkillTemplate = React.lazy(() =>
  import('../skill/SkillTemplate').then((m) => ({ default: m.SkillTemplate })),
);
const HelpPage = React.lazy(() =>
  import('../help/HelpPage').then((m) => ({ default: m.HelpPage })),
);
const NoteEditorPage = React.lazy(() =>
  import('../notes/NoteEditorPage').then((m) => ({ default: m.NoteEditorPage })),
);
const ImageToTextPage = React.lazy(() =>
  import('../imageToText/ImageToTextPage').then((m) => ({ default: m.ImageToTextPage })),
);
const AudioToTextPage = React.lazy(() =>
  import('../audioToText/AudioToTextPage').then((m) => ({ default: m.AudioToTextPage })),
);
// 在线办公功能占位页 —— 单机版不支持的「察元办公 / 智能空间 / 应用中心 /
// 我的待办 / 训练数据中心」点击后进此页,按 feature key 参数化做流式介绍。
const OnlineFeaturePage = React.lazy(() =>
  import('../online/OnlineFeaturePage').then((m) => ({ default: m.OnlineFeaturePage })),
);

/**
 * 模型广场路由包装 —— 把 ``/marketplace?configure=<pid>`` 的 search param
 * 解析出来注入给 ``MarketplacePage``,并在弹窗消费后把 ``configure`` 参数
 * 从当前 Tab 路径里清掉(避免返回 / 刷新重复弹窗)。
 *
 * 之所以在这里(而非 MarketplacePage 内)读 search:本工作区不是标准 router
 * 渲染,页面由 KeepAliveOutlet 按 ``tab.path`` 驱动,query string 保留在
 * ``tab.path`` 上,page-registry 的 ``render(params, path)`` 能拿到完整 path。
 */
const MarketplaceRoute: React.FC<{ path: string }> = ({ path }) => {
  const queryIdx = path.indexOf('?');
  const configurePid =
    queryIdx >= 0
      ? new URLSearchParams(path.slice(queryIdx + 1)).get('configure') ?? undefined
      : undefined;

  // 清掉 configure 参数:找到当前这条 marketplace Tab,把 path 改回不带 query 的
  // ``/marketplace``。updatePath 改 tab.path 后,TabHost 会把 URL 同步拉齐。
  const onConfigureConsumed = React.useCallback(() => {
    const { tabs, updatePath } = useTabsStore.getState();
    const tab = tabs.find((t) => t.path === path);
    if (tab && tab.path !== '/marketplace') updatePath(tab.id, '/marketplace');
  }, [path]);

  return (
    <MarketplacePage
      {...(configurePid ? { configurePid } : {})}
      onConfigureConsumed={onConfigureConsumed}
    />
  );
};

export const ROUTE_ENTRIES: ReadonlyArray<RouteEntry> = [
  {
    pattern: /^\/home$/,
    render: () => <HomePage />,
    defaultTitle: 'nav.home',
    defaultIcon: 'home',
  },
  {
    pattern: /^\/chat$/,
    // 草稿模式:conversationId={null} 让 ChatPage 渲染空态/欢迎屏。
    // 不依赖全局 useComposerStore.activeConversationId 渲染 — 因为多 chat tab 共享
    // 同一全局 store,后台 tab 会被前台 tab 的 setActive 污染,出现"两个 tab 显示同
    // 一会话"的 bug(2026-05-09 报修)。conversationId 必须由各自 tab.path 决定。
    render: () => <ChatPage conversationId={null} />,
    onActivate: () => {
      // onActivate 仍然同步全局 store —— sidebar 高亮当前会话等 UI 还在读它。
      // ChatPage 自身不再依赖该值。
      void import('../../store/composer').then(({ useComposerStore }) => {
        useComposerStore.getState().setActive(null);
      });
    },
    defaultTitle: 'nav.newChat',
    defaultIcon: 'message-square',
  },
  {
    pattern: /^\/chat\/(?<id>[^/]+)$/,
    render: (params) => (
      <ChatPage conversationId={params.id ? decodeURIComponent(params.id) : null} />
    ),
    onActivate: (params) => {
      // 同步 composerStore(给 sidebar / 模型挑选器等读);
      // ChatPage 渲染由 prop 决定,不依赖此处的 setActive 完成时机。
      const id = params.id ? decodeURIComponent(params.id) : '';
      if (!id) return;
      void import('../../store/composer').then(({ useComposerStore }) => {
        useComposerStore.getState().setActive(id);
      });
    },
    defaultIcon: 'message-square',
  },
  {
    pattern: /^\/kb$/,
    render: () => <KbPage />,
    defaultTitle: 'nav.knowledge',
    defaultIcon: 'library',
  },
  {
    // 单个 KB 独立 Tab(由 KbBoard 卡片点击触发)
    // ku_id 形如 'doc:foo' / 'src:42';URL-encoded 后做 path param,所以用 [^/]+
    pattern: /^\/kb\/(?<kuId>[^/]+)$/,
    render: (params) => <KbDetailPage kuId={decodeURIComponent(params.kuId ?? '')} />,
    onActivate: (params) => {
      // 进入 KB tab 时把 composer 选中态锁到当前 KB,顶部 picker / 底部 ask 都跟着走
      const id = decodeURIComponent(params.kuId ?? '');
      if (!id) return;
      void import('../../store/composer').then(({ useComposerStore }) => {
        useComposerStore.getState().setKuIds([id]);
      });
    },
    defaultIcon: 'library',
  },
  {
    // 注意:resolveRoute 会先剥掉 ``?query``/``#hash`` 再做 pattern 匹配,
    // 所以 ``/marketplace?configure=<pid>`` 仍命中本条;render 拿到的是完整 path。
    pattern: /^\/marketplace$/,
    render: (_params, path) => <MarketplaceRoute path={path} />,
    defaultTitle: 'nav.modelMarket',
    defaultIcon: 'box',
  },
  {
    pattern: /^\/history$/,
    render: () => <HistoryPage />,
    defaultTitle: 'nav.history',
    defaultIcon: 'clock',
  },
  // 单机版移除训练数据中心(/annotation):
  // {
  //   pattern: /^\/annotation$/,
  //   render: () => <AnnotationPage />,
  //   defaultTitle: '训练数据中心',
  //   defaultIcon: 'tag',
  // },
  {
    pattern: /^\/settings$/,
    render: () => <SettingsAsPage />,
    defaultTitle: 'nav.settings',
    defaultIcon: 'settings',
  },
  {
    pattern: /^\/mcp$/,
    render: () => <McpPage />,
    defaultTitle: 'mcp.title',
    defaultIcon: 'plug',
  },
  {
    pattern: /^\/tools$/,
    render: () => <ToolsPage />,
    defaultTitle: 'tools.title',
    defaultIcon: 'wrench',
  },
  {
    pattern: /^\/skill\/(?<slug>[^/]+)$/,
    render: (params) => <SkillTemplate slug={params.slug ?? ''} />,
    defaultIcon: 'message-square',
  },
  {
    pattern: /^\/help$/,
    render: () => <HelpPage />,
    defaultTitle: 'settings.helpCenter',
    defaultIcon: 'help-circle',
  },
  {
    pattern: /^\/notes\/new$/,
    render: () => <NoteEditorPage />,
    defaultTitle: 'AI 笔记',
    defaultIcon: 'pen',
  },
  {
    pattern: /^\/image-to-text$/,
    render: () => <ImageToTextPage />,
    defaultTitle: '图转文',
    defaultIcon: 'scan-text',
  },
  {
    pattern: /^\/audio-to-text$/,
    render: () => <AudioToTextPage />,
    defaultTitle: '音转文',
    defaultIcon: 'mic',
  },
  {
    // 在线办公功能占位页:/online/<feature>(office / space / market / tasks / annotation)
    pattern: /^\/online\/(?<feature>[^/]+)$/,
    render: (params) => <OnlineFeaturePage featureKey={params.feature ?? ''} />,
    defaultIcon: 'lock',
  },
  {
    pattern: /^\/admin\/(?<tab>traces|outbox|prompts|platforms)$/,
    render: (params) => (
      <AdminPage
        tab={(params.tab ?? 'traces') as 'traces' | 'outbox' | 'prompts' | 'scores' | 'platforms'}
        onClose={() => history.back()}
      />
    ),
    defaultIcon: 'shield',
  },
];

/** 解析 path,返回 entry + params;命中不到时返回 null */
export function resolveRoute(path: string): { entry: RouteEntry; params: RouteParams } | null {
  const pathname = path.split(/[?#]/, 1)[0] || path;
  for (const entry of ROUTE_ENTRIES) {
    const m = entry.pattern.exec(pathname);
    if (m) {
      const params: RouteParams = { ...(m.groups ?? {}) };
      return { entry, params };
    }
  }
  return null;
}

/** 由 path 推导默认 title;未命中返回原 path */
export function deriveTitle(path: string): string {
  const r = resolveRoute(path);
  if (!r) return path;
  return r.entry.defaultTitle ?? tabsStaticTitle(path) ?? path;
}

/** 由 path 推导默认图标 key */
export function deriveIcon(path: string): string | undefined {
  return resolveRoute(path)?.entry.defaultIcon;
}
