/**
 * 本地推理服务安装指南 Dialog。
 *
 * 离线友好:全部内容是 ``installGuides/data.ts`` 里的纯文字 / 命令 / yaml,
 * 不发请求、不依赖图床,断网也完全可读。
 *
 * 布局(从上到下):
 *   - 标题:emoji + 厂商名 + 一句话简介
 *   - 能力 chip(对话 / 嵌入 / 重排 / 图像 / 图文向量化 / 语音)
 *   - 默认 API Base URL(察元里要填的)+ 复制按钮
 *   - 平台 Tab(Windows / macOS / Linux / Docker)
 *   - 当前 Tab 下的步骤列表(标题 + 说明 + 可复制代码块)
 *   - 推荐模型(按 chat / embed / rerank 等分组)
 *   - 在察元里如何配置(数字步骤)
 *   - 底部:查看完整官方文档(openExternal 跳官网)
 */
import * as React from 'react';
import {
  Dialog, DialogContent, DialogTitle, cn,
} from '@chayuan/ui';
import { Check, Copy, ExternalLink } from 'lucide-react';
import { getPlatform } from '@chayuan/platform-shared';
import {
  type InstallGuide, type ModelKind, type PlatformKey,
  MODEL_KIND_LABEL, PLATFORM_LABEL,
} from '../installGuides/data';

export interface InstallGuideDialogProps {
  guide: InstallGuide | null;
  onOpenChange(open: boolean): void;
}

/** 各能力对应的颜色 chip(跟 ProviderCard 模型类型徽风格一致) */
const KIND_TONES: Record<ModelKind, string> = {
  chat:   'bg-sky-50 text-sky-700 ring-sky-200',
  embed:  'bg-emerald-50 text-emerald-700 ring-emerald-200',
  rerank: 'bg-amber-50 text-amber-700 ring-amber-200',
  image:  'bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200',
  vision: 'bg-pink-50 text-pink-700 ring-pink-200',
  speech: 'bg-violet-50 text-violet-700 ring-violet-200',
};

/** Tab 顺序:挑出该 guide 实际有的平台,按这个顺序展示 */
const PLATFORM_ORDER: PlatformKey[] = ['windows', 'macos', 'linux', 'docker'];

export const InstallGuideDialog: React.FC<InstallGuideDialogProps> = ({ guide, onOpenChange }) => {
  const open = !!guide;

  const availablePlatforms = React.useMemo<PlatformKey[]>(
    () => (guide ? PLATFORM_ORDER.filter((p) => p in guide.platforms) : []),
    [guide],
  );
  const [activePlatform, setActivePlatform] = React.useState<PlatformKey>('linux');

  // 切换 guide 时把 tab 重置为第一个有效平台(避免切到一个该厂商不支持的 tab)
  React.useEffect(() => {
    if (availablePlatforms.length > 0 && availablePlatforms[0]) {
      setActivePlatform(availablePlatforms[0]);
    }
  }, [availablePlatforms]);

  if (!guide) return null;

  const platformGuide = guide.platforms[activePlatform];
  const groupedModels = groupRecommendedModels(guide.recommendedModels);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[90vh] w-[92vw] max-w-3xl flex-col gap-0 overflow-hidden p-0"
      >
        {/* Header */}
        <header className="border-b border-[var(--cy-border-subtle)] px-6 py-4">
          <DialogTitle asChild>
            <h2 className="flex items-center gap-2 text-lg font-semibold text-[var(--cy-text-primary)]">
              <span className="text-xl" aria-hidden>{guide.emoji}</span>
              <span>{guide.title} 安装指南</span>
            </h2>
          </DialogTitle>
          <p className="mt-1 text-xs leading-relaxed text-[var(--cy-text-secondary)]">
            {guide.intro}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {guide.capabilities.map((k) => (
              <span
                key={k}
                className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ring-1', KIND_TONES[k])}
              >
                {MODEL_KIND_LABEL[k]}
              </span>
            ))}
          </div>
        </header>

        {/* Body */}
        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {/* 默认 base URL */}
          <section>
            <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--cy-text-tertiary)]">
              在察元里填的 API Base URL
            </h3>
            <CopyableInline value={guide.defaultBaseUrl} />
          </section>

          {/* 平台 Tab */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--cy-text-tertiary)]">
              安装步骤
            </h3>
            <div className="mb-3 flex flex-wrap gap-1">
              {availablePlatforms.map((p) => {
                const supported = guide.platforms[p]?.supported !== false;
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setActivePlatform(p)}
                    className={cn(
                      'rounded-full px-3 py-1 text-xs transition-colors',
                      activePlatform === p
                        ? 'bg-[var(--cy-brand-500)] text-white shadow-sm'
                        : 'bg-[var(--cy-surface-1)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
                      !supported && activePlatform !== p && 'opacity-60',
                    )}
                  >
                    {PLATFORM_LABEL[p]}
                    {!supported && activePlatform !== p && ' ⚠'}
                  </button>
                );
              })}
            </div>

            {platformGuide && (
              <>
                {platformGuide.note && (
                  <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/40 dark:bg-amber-900/20 dark:text-amber-200">
                    {platformGuide.note}
                  </div>
                )}
                {platformGuide.steps.length === 0 && (
                  <p className="text-xs text-[var(--cy-text-tertiary)]">
                    该平台不直接支持,请参考其它 tab 或下方"完整文档"。
                  </p>
                )}
                <ol className="space-y-3">
                  {platformGuide.steps.map((step, i) => (
                    <li key={i} className="rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-3">
                      <p className="text-xs font-semibold text-[var(--cy-text-primary)]">{step.title}</p>
                      {step.description && (
                        <p className="mt-1 text-xs leading-relaxed text-[var(--cy-text-secondary)]">
                          {step.description}
                        </p>
                      )}
                      {step.code && (
                        <CodeBlock code={step.code} language={step.language} className="mt-2" />
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </section>

          {/* 推荐模型 */}
          {guide.recommendedModels.length > 0 && (
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--cy-text-tertiary)]">
                推荐模型
              </h3>
              <div className="space-y-3">
                {(['chat', 'embed', 'rerank', 'image', 'vision', 'speech'] as ModelKind[]).map((kind) => {
                  const items = groupedModels[kind];
                  if (!items?.length) return null;
                  return (
                    <div key={kind}>
                      <p className={cn('mb-1.5 inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ring-1', KIND_TONES[kind])}>
                        {MODEL_KIND_LABEL[kind]}
                      </p>
                      <ul className="space-y-1.5">
                        {items.map((m, i) => (
                          <li key={i} className="rounded-lg bg-[var(--cy-surface-1)] px-3 py-2">
                            <div className="flex flex-wrap items-baseline gap-2">
                              <code className="font-mono text-xs font-medium text-[var(--cy-text-primary)]">{m.name}</code>
                              {m.description && (
                                <span className="text-[11px] text-[var(--cy-text-tertiary)]">— {m.description}</span>
                              )}
                            </div>
                            {m.command && (
                              <CodeBlock code={m.command} language="bash" className="mt-1.5" compact />
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* 在察元里配置 */}
          <section className="rounded-2xl border border-[var(--cy-brand-200)] bg-[var(--cy-brand-50)]/40 p-4 dark:border-[var(--cy-brand-700)]/30 dark:bg-[var(--cy-brand-900)]/20">
            <h3 className="text-sm font-semibold text-[var(--cy-text-primary)]">
              ✓ 在察元里如何配置
            </h3>
            <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs leading-relaxed text-[var(--cy-text-secondary)]">
              {guide.chayuanConfigSteps.map((s, i) => (
                <li key={i}>{renderInline(s)}</li>
              ))}
            </ol>
          </section>
        </div>

        {/* Footer */}
        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-6 py-3">
          <button
            type="button"
            onClick={() => void getPlatform().shell.openExternal(guide.officialDocs)}
            className="inline-flex items-center gap-1.5 text-xs text-[var(--cy-text-tertiary)] transition-colors hover:text-[var(--cy-brand-600)]"
            title={guide.officialDocs}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看完整官方文档
          </button>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-full bg-[var(--cy-brand-500)] px-4 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-[var(--cy-brand-600)]"
          >
            完成
          </button>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

// ── 辅助 ────────────────────────────────────────────────────────

function groupRecommendedModels(items: InstallGuide['recommendedModels']) {
  const out: Partial<Record<ModelKind, typeof items>> = {};
  for (const m of items) {
    if (!out[m.kind]) out[m.kind] = [];
    out[m.kind]!.push(m);
  }
  return out;
}

/**
 * 极简 inline markdown 渲染:把 `` `code` `` 渲染成 ``<code>``,其余原样。
 * "在察元里配置" 步骤里包含一些命令 / 路径需要等宽,但又不想引入完整 markdown。
 */
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(``[^`]+``|`[^`]+`)/);
  return parts.map((p, i) => {
    if (p.startsWith('``') && p.endsWith('``')) {
      return <code key={i} className="rounded bg-[var(--cy-surface-1)] px-1 py-0.5 font-mono text-[11px] text-[var(--cy-text-primary)]">{p.slice(2, -2)}</code>;
    }
    if (p.startsWith('`') && p.endsWith('`')) {
      return <code key={i} className="rounded bg-[var(--cy-surface-1)] px-1 py-0.5 font-mono text-[11px] text-[var(--cy-text-primary)]">{p.slice(1, -1)}</code>;
    }
    return <span key={i}>{p}</span>;
  });
}

// ── 代码块 + 复制 ────────────────────────────────────────────────

interface CodeBlockProps {
  code: string;
  language?: string;
  className?: string;
  /** 紧凑模式:小一号,行高更紧,用于"推荐模型"行内嵌 */
  compact?: boolean;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ code, language, className, compact }) => {
  const [copied, setCopied] = React.useState(false);
  const onCopy = async () => {
    try {
      // 优先走 platform.clipboard(Tauri 走 plugin-clipboard-manager,Web 走 navigator.clipboard)
      await getPlatform().clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // navigator.clipboard 也可能在某些 webview 里被禁;最后兜底用 textarea + execCommand
      try {
        const ta = document.createElement('textarea');
        ta.value = code;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } catch {
        /* 全失败就静默,用户可以手动选中复制 */
      }
    }
  };

  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-lg border border-[var(--cy-border-subtle)] bg-[#0f172a] dark:bg-[#020617]',
        className,
      )}
    >
      {language && !compact && (
        <div className="flex items-center justify-between border-b border-white/10 bg-white/5 px-3 py-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-white/50">
            {language}
          </span>
        </div>
      )}
      <pre
        className={cn(
          'overflow-x-auto whitespace-pre p-3 font-mono leading-relaxed text-slate-100',
          compact ? 'text-[11px]' : 'text-xs',
        )}
      >
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={onCopy}
        title={copied ? '已复制' : '复制'}
        aria-label={copied ? '已复制' : '复制代码'}
        className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center rounded-md bg-white/10 text-white/70 opacity-0 transition-all hover:bg-white/20 hover:text-white group-hover:opacity-100 focus-visible:opacity-100"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
};

// ── 单行可复制(给 base URL 用) ──────────────────────────────────

const CopyableInline: React.FC<{ value: string }> = ({ value }) => {
  const [copied, setCopied] = React.useState(false);
  const onCopy = async () => {
    try {
      await getPlatform().clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* 静默 */ }
  };
  return (
    <div className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-[var(--cy-surface-1)] px-3 py-1.5 ring-1 ring-[var(--cy-border-subtle)]">
      <code className="truncate font-mono text-xs text-[var(--cy-text-primary)]">{value}</code>
      <button
        type="button"
        onClick={onCopy}
        title={copied ? '已复制' : '复制'}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
      >
        {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
      </button>
    </div>
  );
};
