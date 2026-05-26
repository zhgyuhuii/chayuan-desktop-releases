/**
 * 模型选择器搜索栏旁边那个 ``?`` 按钮弹出来的支持说明面板。
 *
 * 内容来源:``modelHelpCatalog.ts``;每个 capability 一段,下面列我们 Connector
 * 已经接入的厂商 + 它们的官网 / API key 申请地址 / 推荐起步模型。
 *
 * 用户场景:
 *   - "我想画图但下拉空空" → 看这里有哪些 t2i 厂商,挑一个去申请 key,在
 *     「设置 → AI 平台」里粘贴 → 回来下拉就出现该模型
 *   - "qwen-image-max 跟 dall-e-3 哪个好" → 看 note 选合适的
 *   - "API key 在哪申请" → 直接点链接打开控制台
 *
 * 实现要点:
 *   - 纯展示,不发任何请求;链接走 ``getPlatform().shell.open``(桌面端直接调
 *     系统浏览器,Web 端 fallback window.open),避免 Tauri WebView 把链接吞进
 *     app 窗口
 *   - 默认按 ``CapabilityChip`` 排好的顺序铺,左边一个目录(锚跳),右边内容
 */

import * as React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  cn,
} from '@chayuan/ui';
import { useNavigate } from '@tanstack/react-router';
import { getPlatform } from '@chayuan/platform-shared';
import { ExternalLink, HelpCircle, Settings2 } from 'lucide-react';
import type { ModalityCapability } from '@chayuan/transport';
import { CapabilityChip, getCapabilityMeta } from './modelCapabilityChip';
import { listCapabilityHelp, type CapabilityHelp, type VendorEntry } from './modelHelpCatalog';

async function openExternal(url: string): Promise<void> {
  // platform-shared.shell.openExternal:Tauri 走 plugin-shell.open,Web 走 window.open
  try {
    await getPlatform().shell.openExternal(url);
  } catch {
    if (typeof window !== 'undefined') {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }
}

// ──────────────────────────────────────────────────────────────
// 受控 Dialog 组件(给主页能力卡 / 设置面板等外部入口用)
// ──────────────────────────────────────────────────────────────


export interface ModelSupportHelpDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 打开时锚跳到指定 cap 段(给"未配置卡→自动滚到该 cap"用) */
  initialCap?: ModalityCapability;
}

export const ModelSupportHelpDialog: React.FC<ModelSupportHelpDialogProps> = ({
  open, onOpenChange, initialCap,
}) => {
  const help = React.useMemo(() => listCapabilityHelp(), []);
  const navigate = useNavigate();

  // 「去配置」:关掉帮助面板,跳到模型广场并通过 ?configure=<pid> 自动弹出该
  // 厂商的 PlatformSettingsDialog(表单已预填,用户只需填 API key)。
  // 路径里直接带 query string —— 跟随本仓 ``navigate({ to: path as never })``
  // 的既有写法(/marketplace 路由未声明 typed search,用 as never 绕过)。
  const goConfigure = React.useCallback(
    (platformId: string) => {
      onOpenChange(false);
      void navigate({
        to: `/marketplace?configure=${encodeURIComponent(platformId)}` as never,
      });
    },
    [navigate, onOpenChange],
  );
  // open=true 切换瞬间执行锚跳;Dialog 没有 onAnimationEnd 但 setTimeout(0)
  // 足够等内容 mount。锚 id 固定 ``help-cap-<cap>``。
  React.useEffect(() => {
    if (!open || !initialCap) return;
    const id = setTimeout(() => {
      document.getElementById(`help-cap-${initialCap}`)?.scrollIntoView({
        behavior: 'smooth', block: 'start',
      });
    }, 30);
    return () => clearTimeout(id);
  }, [open, initialCap]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="z-[200] w-[min(900px,92vw)] max-w-[900px] max-h-[80vh] overflow-hidden p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader className="border-b border-[var(--cy-border-subtle)] px-5 py-4">
          <DialogTitle>模型支持说明</DialogTitle>
          <DialogDescription>
            察元支持下列各类模型;选好厂商点「去配置」一键打开预填好的配置表单,只需填上 API key,模型就会出现在选择器里。
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[calc(80vh-72px)] overflow-hidden">
          {/* 左侧目录:capability 锚跳 */}
          <nav className="w-44 flex-shrink-0 overflow-y-auto border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2 py-3">
            <ul className="space-y-0.5">
              {help.map((h) => (
                <li key={h.cap}>
                  <a
                    href={`#help-cap-${h.cap}`}
                    onClick={(e) => {
                      e.preventDefault();
                      document
                        .getElementById(`help-cap-${h.cap}`)
                        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
                  >
                    <CapabilityChip cap={h.cap} compact />
                    <span className="truncate">{getCapabilityMeta(h.cap).longLabel}</span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          {/* 右侧内容 */}
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {help.map((section) => (
              <CapabilitySection
                key={section.cap}
                section={section}
                onOpen={openExternal}
                onConfigure={goConfigure}
              />
            ))}
            <p className="mt-6 text-[10px] text-[var(--cy-text-tertiary)]">
              链接以官方一手地址为准。海外厂商(OpenAI / Anthropic / Runway / Stability)国内需自行解决网络访问;阿里 / 字节 / 智谱 / 月之暗面 / 深度求索国内直连。
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};


// ──────────────────────────────────────────────────────────────
// 非受控按钮 — ModelMenuList 里嵌套这个最方便,自己管 open state
// ──────────────────────────────────────────────────────────────


export interface ModelSupportHelpButtonProps {
  /** dialog 自定义对齐(主要用于嵌入 dropdown 内部时关掉 portal,避免被 menu 关闭) */
  className?: string;
}

export const ModelSupportHelpButton: React.FC<ModelSupportHelpButtonProps> = ({ className }) => {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <button
        type="button"
        aria-label="支持哪些模型 / 如何申请 API key"
        title="点击查看支持的模型 / 厂商 / API key 申请地址"
        onClick={(e) => {
          // dropdown / menu 父级会监听 click 收 menu — stopPropagation 让 dialog 安心打开
          e.stopPropagation();
          setOpen(true);
        }}
        onKeyDown={(e) => e.stopPropagation()}
        className={cn(
          'flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] transition-colors',
          'hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
          className,
        )}
      >
        <HelpCircle className="h-4 w-4" />
      </button>
      <ModelSupportHelpDialog open={open} onOpenChange={setOpen} />
    </>
  );
};


const CapabilitySection: React.FC<{
  section: CapabilityHelp;
  onOpen(url: string): void;
  onConfigure(platformId: string): void;
}> = ({ section, onOpen, onConfigure }) => {
  const meta = getCapabilityMeta(section.cap);
  return (
    <section id={`help-cap-${section.cap}`} className="mb-6 scroll-mt-4">
      <header className="mb-2 flex items-center gap-2">
        <CapabilityChip cap={section.cap} />
        <h3 className="text-sm font-semibold text-[var(--cy-text-primary)]">{section.title}</h3>
        <span className="text-[10px] text-[var(--cy-text-tertiary)]">{meta.label}</span>
      </header>
      <p className="mb-3 text-xs leading-relaxed text-[var(--cy-text-secondary)]">{section.blurb}</p>
      <ul className="space-y-2">
        {section.vendors.map((v) => (
          <VendorRow key={v.name} v={v} onOpen={onOpen} onConfigure={onConfigure} />
        ))}
      </ul>
    </section>
  );
};


const VendorRow: React.FC<{
  v: VendorEntry;
  onOpen(url: string): void;
  onConfigure(platformId: string): void;
}> = ({ v, onOpen, onConfigure }) => (
  <li className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm font-medium text-[var(--cy-text-primary)]">{v.name}</span>
      <div className="flex flex-shrink-0 items-center gap-1">
        {/* 「去配置」只在能对应上模型广场 catalog 的厂商显示 —— 点击直接打开
            预填好的配置表单(base URL / 模型清单已填),用户只需补 API key。 */}
        {v.platformId && (
          <button
            type="button"
            onClick={() => onConfigure(v.platformId!)}
            title={`在「AI 平台」里打开 ${v.name} 的配置表单(已预填,只需填 API key)`}
            className="inline-flex items-center gap-1 rounded-full bg-[var(--cy-brand-500)] px-2 py-0.5 text-[10px] font-medium text-white transition-colors hover:bg-[var(--cy-brand-600)]"
          >
            <Settings2 className="h-3 w-3" />
            去配置
          </button>
        )}
        <ExternalLinkPill label="官网" url={v.home} onOpen={onOpen} />
        {v.apiKey && v.apiKey !== v.home && (
          <ExternalLinkPill label="申请 API key" url={v.apiKey} onOpen={onOpen} highlight />
        )}
      </div>
    </div>
    {v.note && (
      <p className="mt-1 text-[11px] leading-snug text-[var(--cy-text-secondary)]">{v.note}</p>
    )}
    {v.models.length > 0 && (
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <span className="text-[10px] text-[var(--cy-text-tertiary)]">示例模型:</span>
        {v.models.map((m) => (
          <code
            key={m}
            className="rounded bg-[var(--cy-surface-2)] px-1 py-0.5 font-mono text-[10px] text-[var(--cy-text-secondary)]"
            title="复制到「设置 → AI 平台」里填这个 id"
          >
            {m}
          </code>
        ))}
      </div>
    )}
  </li>
);


const ExternalLinkPill: React.FC<{
  label: string;
  url: string;
  onOpen(url: string): void;
  highlight?: boolean;
}> = ({ label, url, onOpen, highlight = false }) => {
  // url 非 http/https 时(如 "无需 — 桌面端已 bundle")只展示文字不渲染链接
  const isExternal = /^https?:/i.test(url);
  if (!isExternal) {
    return (
      <span className="rounded-full bg-[var(--cy-surface-2)] px-2 py-0.5 text-[10px] text-[var(--cy-text-tertiary)]">
        {url}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(url)}
      title={url}
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] transition-colors',
        highlight
          ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)] hover:bg-[var(--cy-brand-100)] dark:bg-[var(--cy-brand-950)]/40 dark:text-[var(--cy-brand-300)]'
          : 'bg-[var(--cy-surface-2)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-3)]',
      )}
    >
      {label}
      <ExternalLink className="h-3 w-3" />
    </button>
  );
};
