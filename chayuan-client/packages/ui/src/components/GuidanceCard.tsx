/**
 * GuidanceCard —— 三段式 What / Why / How 引导卡片。
 *
 * 用例:
 *   - macOS Gatekeeper 拦截 → 教用户右键打开
 *   - Windows SmartScreen / 杀软拦截 → 教用户白名单
 *   - OnlyOffice 未启 → 一键 docker pull 引导
 *   - 镜像源失败 → 切换 fallback
 *
 * 设计原则:
 *   - 三段语义固定: 上(What 出了什么) → 中(Why 为什么) → 下(How 平台 tab)
 *   - tone 决定颜色 + 图标; 不要传 className 改 tone, 用 tone prop
 *   - 平台 tab 自动检测当前平台并默认选中
 *   - 命令行步骤自带"复制"按钮; 复制成功 toast 由调用方处理(本组件只触发 onCopy)
 *
 * 不做的事:
 *   - 不内置 toast / 不内置网络请求
 *   - 不在 dark 模式下切换图标(图标颜色由 tone token 决定,跨主题自适应)
 */

import * as React from 'react';
import { AlertTriangle, Info, CheckCircle2, XCircle, Copy, ExternalLink } from 'lucide-react';
import { cn } from '../lib/cn';

export type GuidanceTone = 'warning' | 'info' | 'success' | 'danger';
export type GuidancePlatform = 'macos' | 'windows' | 'linux' | 'all';

export interface GuidanceStep {
  /** 文本说明(可含粗体/链接,支持 ReactNode) */
  text: React.ReactNode;
  /** 可选的 shell 命令,会渲染成 <code> 块 + 复制按钮 */
  command?: string;
}

export interface GuidancePlatformBlock {
  platform: GuidancePlatform;
  /** Tab 显示名;默认按 platform 取 'macOS' / 'Windows' / 'Linux' / '通用' */
  label?: string;
  steps: GuidanceStep[];
  /** 可选的"了解更多"外链 */
  docHref?: string;
}

export interface GuidanceCardProps {
  tone?: GuidanceTone;
  /** 第一段:出了什么(标题 + 一句话) */
  what: React.ReactNode;
  /** 第二段:为什么会这样(可选的解释) */
  why?: React.ReactNode;
  /** 第三段:平台分 tab 的解决步骤 */
  how: GuidancePlatformBlock[];
  /** 可选的右上角主操作按钮 (e.g. "一键安装") */
  actionLabel?: React.ReactNode;
  onAction?: () => void;
  /** 复制命令时回调,调用方负责 toast */
  onCopy?: (command: string) => void;
  className?: string;
}

const TONE_STYLES: Record<GuidanceTone, { bg: string; border: string; iconColor: string; titleColor: string }> = {
  warning: {
    bg: 'bg-[var(--cy-warning-50)]',
    border: 'border-l-[var(--cy-warning-500)]',
    iconColor: 'text-[var(--cy-warning-600)]',
    titleColor: 'text-[var(--cy-warning-700)]',
  },
  info: {
    bg: 'bg-[var(--cy-info-50)]',
    border: 'border-l-[var(--cy-info-500)]',
    iconColor: 'text-[var(--cy-info-600)]',
    titleColor: 'text-[var(--cy-info-700)]',
  },
  success: {
    bg: 'bg-[var(--cy-success-50)]',
    border: 'border-l-[var(--cy-success-500)]',
    iconColor: 'text-[var(--cy-success-600)]',
    titleColor: 'text-[var(--cy-success-700)]',
  },
  danger: {
    bg: 'bg-[var(--cy-danger-50)]',
    border: 'border-l-[var(--cy-danger-500)]',
    iconColor: 'text-[var(--cy-danger-600)]',
    titleColor: 'text-[var(--cy-danger-700)]',
  },
};

const TONE_ICONS: Record<GuidanceTone, React.ComponentType<{ className?: string }>> = {
  warning: AlertTriangle,
  info: Info,
  success: CheckCircle2,
  danger: XCircle,
};

const PLATFORM_LABELS: Record<GuidancePlatform, string> = {
  macos: 'macOS',
  windows: 'Windows',
  linux: 'Linux',
  all: '通用',
};

function detectPlatform(): GuidancePlatform {
  if (typeof navigator === 'undefined') return 'all';
  const ua = navigator.userAgent.toLowerCase();
  if (ua.includes('mac')) return 'macos';
  if (ua.includes('win')) return 'windows';
  if (ua.includes('linux')) return 'linux';
  return 'all';
}

export const GuidanceCard: React.FC<GuidanceCardProps> = ({
  tone = 'warning',
  what,
  why,
  how,
  actionLabel,
  onAction,
  onCopy,
  className,
}) => {
  const styles = TONE_STYLES[tone];
  const Icon = TONE_ICONS[tone];
  const detected = React.useMemo(detectPlatform, []);
  const initialPlatform = React.useMemo<GuidancePlatform>(() => {
    const match = how.find((b) => b.platform === detected);
    return match?.platform ?? how[0]?.platform ?? 'all';
  }, [how, detected]);
  const [activePlatform, setActivePlatform] = React.useState<GuidancePlatform>(initialPlatform);
  const activeBlock = how.find((b) => b.platform === activePlatform) ?? how[0];

  const handleCopy = (command: string) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      void navigator.clipboard.writeText(command).catch(() => undefined);
    }
    onCopy?.(command);
  };

  return (
    <div
      role="region"
      aria-label="操作引导"
      className={cn(
        'rounded-[var(--cy-radius-lg)] border border-l-4 border-[var(--cy-border-subtle)] p-4 shadow-[var(--cy-shadow-sm)]',
        styles.bg,
        styles.border,
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn('mt-0.5 h-5 w-5 flex-shrink-0', styles.iconColor)} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          {/* What */}
          <div className={cn('text-sm font-semibold leading-snug', styles.titleColor)}>{what}</div>
          {/* Why */}
          {why ? (
            <div className="mt-1 text-xs leading-relaxed text-[var(--cy-text-secondary)]">{why}</div>
          ) : null}
        </div>
        {actionLabel ? (
          <button
            type="button"
            onClick={onAction}
            className={cn(
              'flex-shrink-0 rounded-[var(--cy-radius-md)] px-3 py-1.5 text-xs font-medium transition-colors',
              'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)] hover:opacity-90',
            )}
          >
            {actionLabel}
          </button>
        ) : null}
      </div>

      {/* How — platform tabs */}
      {how.length > 0 ? (
        <div className="mt-4">
          {how.length > 1 ? (
            <div
              role="tablist"
              className="mb-3 inline-flex rounded-[var(--cy-radius-full)] bg-[var(--cy-surface-2)] p-0.5"
            >
              {how.map((b) => {
                const label = b.label ?? PLATFORM_LABELS[b.platform];
                const active = b.platform === activePlatform;
                return (
                  <button
                    key={b.platform}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => setActivePlatform(b.platform)}
                    className={cn(
                      'rounded-[var(--cy-radius-full)] px-3 py-1 text-xs font-medium transition-colors',
                      active
                        ? 'bg-[var(--cy-surface-base)] text-[var(--cy-text-primary)] shadow-[var(--cy-shadow-sm)]'
                        : 'text-[var(--cy-text-secondary)] hover:text-[var(--cy-text-primary)]',
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          ) : null}
          {activeBlock ? (
            <div className="space-y-2">
              <ol className="space-y-2 text-xs leading-relaxed text-[var(--cy-text-primary)]">
                {activeBlock.steps.map((step, i) => (
                  <li key={i} className="flex gap-2">
                    <span
                      className={cn(
                        'flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-semibold',
                        'bg-[var(--cy-surface-base)] text-[var(--cy-text-secondary)]',
                      )}
                    >
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div>{step.text}</div>
                      {step.command ? (
                        <div className="mt-1.5 flex items-center gap-1.5 rounded-[var(--cy-radius-sm)] bg-[var(--cy-surface-base)] px-2 py-1.5 font-mono text-[11px]">
                          <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre text-[var(--cy-text-primary)]">
                            {step.command}
                          </code>
                          <button
                            type="button"
                            onClick={() => step.command && handleCopy(step.command)}
                            className="flex-shrink-0 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
                            aria-label="复制命令"
                          >
                            <Copy className="h-3 w-3" />
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
              {activeBlock.docHref ? (
                <a
                  href={activeBlock.docHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-[var(--cy-brand-600)] hover:underline"
                >
                  了解更多 <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
