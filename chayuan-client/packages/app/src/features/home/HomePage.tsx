/**
 * HomePage — 主页(单机版重构 2026-05-09):
 *
 *   1. Greeting(早/午/晚 + 用户名 + Logo)
 *   2. [可选 banner] 还没配模型 → 跳模型广场
 *   3. 5 张炫酷功能入口卡(知识库 / 模型广场 / 图转文 / 音转文 / AI 笔记)
 *   4. 按模型类型开始对话(CapabilityCardGrid)
 *   5. 流式产品介绍气泡(进首页跑一次,流完即静止)
 *   6. 招商合作 banner
 *   7. 关注我们 + 收款码 三张二维码
 *
 * 历史:
 *  - 2026-05-09 移除旧版 Composer + 占位能力,首页改为产品入口 + 介绍。
 *  - 2026-05-12 取消"每 4s 自动换文案"循环 — LLM 不可用时它会在静态文案
 *    间反复切,视觉上就是"屏幕闪烁"。改为单次播放 + rAF 合批 chunk 渲染,
 *    气泡固定 min-h 避免内容增长推开下方卡片。
 *  - 2026-05-20 AI 介绍气泡从顶部下移到招商合作 banner 上方 — 让 5 个工具
 *    卡 + CapabilityCardGrid 上移,首屏直接看到全部功能入口。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  Library, Layers3, Pen, ScanText, Mic,
  AlertCircle,
} from 'lucide-react';
import { home, type ProviderCatalogEntry } from '@chayuan/api';
import { useAuthStore } from '../../store/auth';
import { useTabsStore } from '../../store/tabs';
import { useTranslation } from '../../i18n';
import { usePlatformCatalogQuery } from '../../store/modelPlatform';
import {
  ALIPAY_QR_URL, CHAYUAN_LOGO_URL, FOLLOW_QR_URL, WXPAY_QR_URL,
} from '../../lib/brandAssets';
import { CapabilityCardGrid } from './capabilityCards/CapabilityCardGrid';
import { HomeFxLayer } from './homeFx/HomeFxLayer';
import { CardAura } from './homeFx/CardAura';
import { PartnershipBanner } from './PartnershipBanner';
import { HomeFooterMarquee } from './HomeFooterMarquee';

// ── 5 张功能入口卡定义 ──────────────────────────────────────────────
interface CapItem {
  id: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  to: string;
  /** 卡片渐变 — 视觉系列从冷到暖排序,不同卡有"专属色" */
  gradient: string;
  iconBg: string;
}

const CAPS: ReadonlyArray<CapItem> = [
  {
    id: 'kb',
    icon: Library,
    title: '知识库',
    subtitle: '文档 / 结构化 / 向量统一查询',
    to: '/kb',
    gradient: 'from-emerald-50 to-teal-100 dark:from-emerald-900/30 dark:to-teal-800/40',
    iconBg: 'bg-gradient-to-br from-emerald-400 to-teal-500',
  },
  {
    id: 'marketplace',
    icon: Layers3,
    title: '模型广场',
    subtitle: '云端 / 本地 / 聚合厂商一键接入',
    to: '/marketplace',
    gradient: 'from-violet-50 to-fuchsia-100 dark:from-violet-900/30 dark:to-fuchsia-800/40',
    iconBg: 'bg-gradient-to-br from-violet-400 to-fuchsia-500',
  },
  {
    id: 'image-to-text',
    icon: ScanText,
    title: '图转文',
    subtitle: '上传图像 · OCR 自动提取文字 · 富文本编辑 · 导出 Word/MD/TXT',
    to: '/image-to-text',
    gradient: 'from-amber-50 to-orange-100 dark:from-amber-900/30 dark:to-orange-800/40',
    iconBg: 'bg-gradient-to-br from-amber-400 to-orange-500',
  },
  {
    id: 'audio-to-text',
    icon: Mic,
    title: '音转文',
    subtitle: '上传音频 · 自动转写并分话人 · 富文本编辑 · 导出 Word/MD/TXT',
    to: '/audio-to-text',
    gradient: 'from-rose-50 to-pink-100 dark:from-rose-900/30 dark:to-pink-800/40',
    iconBg: 'bg-gradient-to-br from-rose-400 to-pink-500',
  },
  {
    id: 'notes',
    icon: Pen,
    title: 'AI 笔记',
    subtitle: '随手记录 · 实时语音转文字 · 自动入库可检索',
    to: '/notes/new',
    gradient: 'from-sky-50 to-cyan-100 dark:from-sky-900/30 dark:to-cyan-800/40',
    iconBg: 'bg-gradient-to-br from-sky-400 to-cyan-500',
  },
];

// ── 静态介绍文案 fallback(LLM 不可用时随机一段)────────────────────
const STATIC_INTROS: ReadonlyArray<string> = [
  '察元 AI · 单机版,把多模型对话、本地知识库、离线推理装进同一个桌面应用。装完即用,数据全程留在本机;Ollama / Infinity / vLLM 等本地服务跟云端 LLM 一起摆在桌面上,随时切换、对比试用。',
  '一个面向办公场景的本地 AI 工作台。支持多模型并排对话、文档 / 结构化数据 / 向量库统一检索;不联网也能跑,跟 WPS 加载项「察元 AI 文档助手」共用同一份知识库,文档操作直接在 Office 内完成。',
  '为离线 / 内网场景设计:多 LLM 永道对比、按需切到本地 Ollama / Infinity / vLLM,知识库文件、向量索引、对话历史全部存本机 SQLite,不上传任何 SaaS。可在统信 UOS / 麒麟 / openKylin 等国产桌面顺畅运行。',
  '把"装完一个 AI 应用"做到极致:零配置启动,Web / Tauri 桌面 / WPS 加载项 三端共用同一份知识库与对话状态。云模型与本地推理可随时切换,不强制联网,不强制登录。',
  '面向办公的本地优先 AI 助手。多 LLM 同屏对比、知识库一站式检索、MCP 工具集中托管。WPS 加载项「察元 AI 文档助手」直接复用本端配置,文档处理无须跳转。',
];

function pickStaticIntro(): string {
  return STATIC_INTROS[Math.floor(Math.random() * STATIC_INTROS.length)] ?? STATIC_INTROS[0]!;
}

// ── 是否已配置任意可用 LLM 模型 ────────────────────────────────────
function hasUsableLLM(catalog: ProviderCatalogEntry[]): boolean {
  return catalog.some(
    (e) => e.configured && e.enabled && (e.llm_models ?? []).length > 0,
  );
}

export const HomePage: React.FC = () => {
  const { t } = useTranslation();
  const user = useAuthStore((s) => s.user);
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();

  const catalogQ = usePlatformCatalogQuery();
  const catalog = catalogQ.data ?? [];
  const ready = hasUsableLLM(catalog);

  const greeting = React.useMemo(() => {
    const h = new Date().getHours();
    if (h < 12) return t('home.greeting.morning');
    if (h < 18) return t('home.greeting.afternoon');
    return t('home.greeting.evening');
  }, [t]);

  const onCapClick = (cap: CapItem) => {
    // 「AI 笔记」每次进来都是空白:已有 `/notes/new` tab 先关掉,让 NoteEditorPage
    // 重新 mount → useMemo 生成新 draftKey → 不会被上次未保存草稿污染。
    if (cap.id === 'notes') {
      const store = useTabsStore.getState();
      const existing = store.tabs.find((t) => t.path === cap.to);
      if (existing) store.close(existing.id);
    }
    open(cap.to, { title: cap.title, icon: cap.id });
    void navigate({ to: cap.to as never });
  };

  const onConfigureModel = () => {
    open('/marketplace', { title: '模型广场', icon: 'box' });
    void navigate({ to: '/marketplace' as never });
  };

  // ── 流式产品介绍 ────────────────────────────────────────────────
  // 行为(2026-05-12 改):进首页拉一次 LLM 流式介绍;流完即停,**不再自动循环**。
  // 旧版每 4s 清空 + 重新流,LLM 不可用时(后端 emit error)会变成每 4s 在静态
  // 文案间反复切换,视觉上"屏幕一直闪烁"。改为单次播放后静止。
  // 离开 tab(unmount / KeepAlive 切到背景)→ AbortController 中止流。
  // LLM 不可用时 catch 一次 → 选一段静态文案落定,不再重试。
  //
  // chunk → setIntro 用 rAF 合并:LLM 高速吐 token 时不再每个 token 都触发
  // HomePage 全树重渲染,显著降低四张能力卡 + 二维码的 reflow 抖动。
  const [intro, setIntro] = React.useState('');
  const [introDone, setIntroDone] = React.useState(false);
  const [introError, setIntroError] = React.useState<string | null>(null);
  const introAbortRef = React.useRef<AbortController | null>(null);
  // rAF 合批:onChunk 把增量塞到 ref,下一帧统一 flush 到 state
  const pendingRef = React.useRef('');
  const rafRef = React.useRef<number | null>(null);

  const flushPending = React.useCallback(() => {
    rafRef.current = null;
    const delta = pendingRef.current;
    if (!delta) return;
    pendingRef.current = '';
    setIntro((prev) => prev + delta);
  }, []);

  const queueChunk = React.useCallback((text: string) => {
    pendingRef.current += text;
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(flushPending);
  }, [flushPending]);

  React.useEffect(() => {
    const ctl = new AbortController();
    introAbortRef.current = ctl;
    setIntro('');
    setIntroDone(false);
    setIntroError(null);

    void home.introStream(
      {
        onChunk: queueChunk,
        onDone: () => {
          // 最后一帧 pending 还可能没 flush,补刷一次,再标记 done
          if (rafRef.current != null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushPending();
          setIntroDone(true);
        },
      },
      { signal: ctl.signal },
    ).catch((e: unknown) => {
      if ((e as { name?: string })?.name === 'AbortError' || ctl.signal.aborted) return;
      // LLM 不可用 / 调用失败 → 选一段静态文案落定;**不再循环**
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      pendingRef.current = '';
      setIntro(pickStaticIntro());
      setIntroDone(true);
      setIntroError(e instanceof Error ? e.message : String(e));
    });

    return () => {
      ctl.abort();
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      pendingRef.current = '';
    };
  }, [flushPending, queueChunk]);

  return (
    <div className="relative isolate mx-auto flex h-full max-w-5xl flex-col gap-8 px-6 py-10 overflow-y-auto">
      {/* 特效层 — 金龙/凤凰/星空在背景层,内容卡片用 z-10 浮在上面不被遮。
          isolate 强制 HomePage 创建 stacking context,让 fx 的 z:0 严格沉到
          本页内容下方,不会跑到 sidebar/header 之上。 */}
      <HomeFxLayer />

      {/* ── 1. Greeting(Logo + 早/午/晚 + 用户名)───────────────── */}
      <div className="relative z-10 flex items-center gap-3">
        <img
          src={CHAYUAN_LOGO_URL}
          alt="察元 AI"
          className="h-12 w-12 bg-transparent object-contain rounded-full"
          style={{ animation: 'cy-badge-halo 3.6s ease-in-out infinite' }}
        />
        <div>
          <h1 className="text-2xl font-semibold text-[var(--cy-text-primary)]">
            {greeting}
            {user?.username && (
              <span className="ml-2 text-base font-normal text-[var(--cy-text-secondary)]">
                {user.username}
              </span>
            )}
          </h1>
          <p className="text-sm text-[var(--cy-text-tertiary)]">察元 AI · 本地优先的办公 AI 工作台</p>
        </div>
      </div>

      {/* ── 2. 未配模型时的引导 banner ────────────────────────── */}
      {!ready && catalogQ.isFetched && (
        <div className="relative z-10 flex items-center gap-3 rounded-2xl border border-amber-200/60 bg-amber-50/80 px-4 py-3 dark:border-amber-800/40 dark:bg-amber-900/20">
          <AlertCircle className="h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
              还没配置任何可用模型
            </p>
            <p className="text-xs text-amber-700/90 dark:text-amber-200/80">
              先去模型广场接入云厂商或本地推理服务(Ollama / Infinity / vLLM 等),才能开始对话。
            </p>
          </div>
          <button
            type="button"
            onClick={onConfigureModel}
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-amber-600"
          >
            去配置模型
          </button>
        </div>
      )}

      {/* ── 3. 四张炫酷功能入口卡 ────────────────────────────────
          设计:窄屏 2 列、桌面 4 列等宽并列,卡片高度统一 h-40,
          gap 略增到 5,卡内 padding p-5,左上图标 + 左下文案的固定网格,
          上下左右留白对称 — 视觉平衡感比之前 5 列(残余空隙)好。 */}
      <div className="relative z-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-5">
        {CAPS.map((c, i) => {
          const Icon = c.icon;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => onCapClick(c)}
              className={`group relative flex h-40 flex-col justify-between overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-gradient-to-br ${c.gradient} p-5 text-left transition-all hover:-translate-y-0.5 hover:shadow-[var(--cy-shadow-md)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cy-brand-500)]`}
            >
              {/* 图标"动起来" — 4s 周期轻浮 + 微旋 + 金光晕,5 张卡 0.55s 错相 */}
              <div
                className={`flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-sm ${c.iconBg}`}
                style={{ animation: `cy-icon-float 4s ease-in-out ${i * 0.55}s infinite` }}
              >
                <Icon className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <p className="text-base font-semibold text-[var(--cy-text-primary)]">{c.title}</p>
                <p className="mt-1 line-clamp-2 text-xs text-[var(--cy-text-secondary)]">{c.subtitle}</p>
              </div>
              {/* 右上角光斑装饰,hover 微移 */}
              <div
                className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full bg-white/30 blur-2xl transition-transform group-hover:translate-x-1"
                aria-hidden
              />
              {/* 金线缠绕 — 沿卡片矩形边流动 */}
              <CardAura hue="gold" intensity="subtle" radius={16} />
            </button>
          );
        })}
      </div>

      {/* ── 4. 按模型类型开始对话 ────────────────────────────
       *  7 张 capability 卡(对话 / 视觉 / 文生图 / 图像编辑 / 文生视频
       *  / 语音合成 / 语音识别)。已配模型的卡点击直接新对话并预选最优模型;
       *  未配卡点击弹支持目录,锚跳到对应 cap 段帮用户找申请地址。
       *  数据源跟 ChatComposer 共享 ['v1.models',{type:'all'}] react-query 缓存,
       *  零额外网络。 */}
      <div className="relative z-10">
        <CapabilityCardGrid />
      </div>

      {/* ── 5. 流式产品介绍气泡 ────────────────────────────────
          一次播放,流完即静止;不再循环换文案,避免视觉闪烁。
          固定 min-h-[5rem] 让气泡高度不随文字增长抖动。
          2026-05-20 从顶部下移到此 — 让上方 5 个工具卡 + CapabilityCardGrid
          首屏即可见,产品介绍作为补充信息靠后展示。 */}
      <div className="relative z-10 flex max-w-3xl items-start gap-3">
        <div
          className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-[var(--cy-brand-500)] to-[var(--cy-brand-700)] flex items-center justify-center text-[11px] font-semibold text-white shadow-sm"
          style={{ animation: 'cy-badge-halo 3.6s 1.2s ease-in-out infinite' }}
        >
          AI
        </div>
        <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-[var(--cy-surface-base)] px-4 py-3 ring-1 ring-[var(--cy-border-subtle)] shadow-sm">
          <p className="min-h-[5rem] text-sm leading-relaxed text-[var(--cy-text-secondary)]">
            {intro || (introDone ? '' : <TypingDots />)}
            {!introDone && intro && <Caret />}
          </p>
          {introError && (
            <p
              className="mt-1 text-[10px] text-[var(--cy-text-tertiary)]"
              title={introError}
            >
              (LLM 不可用,已用本地文案兜底 — 可前往模型广场配置)
            </p>
          )}
        </div>
      </div>

      {/* ── 6. 招商合作 banner ─────────────────────────────────
       *  寻找城市合伙人 / ISV 集成商 / 私有化部署 / 行业定制。
       *  深金渐变背景 + 金线缠绕,显得"郑重",2 个 CTA 弹联系方式
       *  Dialog(微信/电话/邮箱,可复制)。
       *  文案/联系方式在 PartnershipBanner.tsx 顶部常量,后续替换简单。 */}
      <PartnershipBanner />

      {/* ── 7. 关注我们 / 收款码 ───────────────────────────────── */}
      <section className="relative z-10 rounded-3xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-[var(--cy-text-primary)]">支持我们</h2>
          <p className="text-xs text-[var(--cy-text-tertiary)]">扫码关注 / 赞助开发者</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <QrCard label="关注我们" src={FOLLOW_QR_URL} />
          <QrCard label="支付宝赞助" src={ALIPAY_QR_URL} />
          <QrCard label="微信赞助" src={WXPAY_QR_URL} />
        </div>
      </section>

      {/* ── 8. 底部走马灯 ── 版权 + 源码授权 + slogan,点击弹"关注我们" */}
      <div className="relative z-10 mt-2 flex justify-center">
        <HomeFooterMarquee />
      </div>

    </div>
  );
};

// ── 三粒跳动 dots — 流式介绍空白等待期的纯动画占位 ────────────────
// 取代之前的「正在生成介绍…」文字,纯视觉提示 LLM 在思考,文案干净不打断阅读。
const TypingDots: React.FC = () => (
  <span className="inline-flex items-center gap-1 align-middle motion-safe:contents motion-reduce:hidden">
    <span
      className="h-1.5 w-1.5 rounded-full bg-[var(--cy-text-tertiary)] animate-bounce"
      style={{ animationDelay: '-0.3s' }}
    />
    <span
      className="h-1.5 w-1.5 rounded-full bg-[var(--cy-text-tertiary)] animate-bounce"
      style={{ animationDelay: '-0.15s' }}
    />
    <span className="h-1.5 w-1.5 rounded-full bg-[var(--cy-text-tertiary)] animate-bounce" />
  </span>
);

// ── 打字机光标 — 仅流式过程中跟在文末,动画用 motion-safe 包,
//    用户系统勾了"减少动画"时变成静态竖线,不会一直闪。 ──────────────
const Caret: React.FC = () => (
  <span
    aria-hidden
    className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 bg-[var(--cy-text-tertiary)] align-text-bottom motion-safe:animate-pulse"
  />
);

// ── 二维码小卡 ─────────────────────────────────────────────────────
const QrCard: React.FC<{ label: string; src: string }> = ({ label, src }) => (
  <div className="flex flex-col items-center gap-2 rounded-2xl bg-[var(--cy-surface-base)] p-3 ring-1 ring-[var(--cy-border-subtle)]">
    <img
      src={src}
      alt={label}
      className="h-32 w-32 rounded-lg object-contain"
      draggable={false}
    />
    <p className="text-xs font-medium text-[var(--cy-text-secondary)]">{label}</p>
  </div>
);
