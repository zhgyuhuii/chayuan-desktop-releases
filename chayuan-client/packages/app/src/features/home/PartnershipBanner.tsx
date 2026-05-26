/**
 * 主页底部招商 banner — 寻找城市合伙人 / ISV 集成商 / 私有化部署 / 行业定制。
 *
 * 设计
 * ----
 * 一行宽幅:
 *   - 左侧:Sparkles 图标 + 标题 + 副标题 + 3 个服务标签 chip
 *   - 右侧:2 个 CTA 按钮(主要金色填充 + 次要透明边框)
 *   - 背景:深金 → 黑 → 深金 渐变,叠 CardAura 金线缠绕,夜空背景下显得"郑重"
 *
 * 点击 CTA 弹 Dialog 显示联系方式(微信、电话、邮箱)。
 * 文案/联系方式集中在文件顶部常量,后续替换真实信息直接改这里就够。
 *
 * 跟其它主页 section 一样加 ``relative z-10``,防止被 fx 层(z:0 dragon
 * etc.)盖到。
 */

import * as React from 'react';
import { Sparkles, Mail, MessageSquare, Copy, Check } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@chayuan/ui';
import { notifySuccess } from '../../store/errorDialog';
import { FOLLOW_QR_URL } from '../../lib/brandAssets';

// ── 招商文案 / 联系方式(集中维护)─────────────────────────────────

type Contact =
  | {
      kind: 'qr';
      icon: React.ComponentType<{ className?: string }>;
      label: string;
      value: string;
      hint: string;
      qrUrl: string;
    }
  | {
      kind: 'copy';
      icon: React.ComponentType<{ className?: string }>;
      label: string;
      value: string;
      hint: string;
    };

const PARTNERSHIP: {
  title: string;
  subtitle: string;
  badges: ReadonlyArray<string>;
  contacts: ReadonlyArray<Contact>;
} = {
  title: '寻找城市合伙人 · ISV 集成商',
  subtitle:
    '渠道分销、私有化部署、行业垂直定制 — 一起把察元 AI 带到更多企业',
  badges: ['渠道加盟', '私有化', '行业定制'],
  contacts: [
    {
      kind: 'qr',
      icon: MessageSquare,
      label: '微信公众号',
      value: '智灵鸟科技',
      hint: '扫码关注公众号,私信"合作"接洽',
      qrUrl: FOLLOW_QR_URL,
    },
    {
      kind: 'copy',
      icon: Mail,
      label: '邮箱',
      value: 'cmdbird@163.com',
      hint: '商务对接 / 招投标',
    },
  ],
};

export const PartnershipBanner: React.FC = () => {
  const [open, setOpen] = React.useState(false);

  return (
    <>
      <section className="relative z-10 rounded-3xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-6">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          {/* —— 左:标题 + 副标题 + 服务 chip —— */}
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex items-center gap-2">
              <span className="inline-flex items-center justify-center rounded-lg bg-gradient-to-br from-[var(--cy-brand-500)] to-[var(--cy-brand-700)] p-1.5 shadow-sm">
                <Sparkles className="h-3.5 w-3.5 text-white" />
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
                合作 · 招商
              </span>
            </div>
            <h3 className="text-lg font-semibold text-[var(--cy-text-primary)] sm:text-xl">
              {PARTNERSHIP.title}
            </h3>
            <p className="mt-1 text-sm text-[var(--cy-text-secondary)]">
              {PARTNERSHIP.subtitle}
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {PARTNERSHIP.badges.map((b) => (
                <span
                  key={b}
                  className="inline-flex items-center rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--cy-text-secondary)]"
                >
                  {b}
                </span>
              ))}
            </div>
          </div>

          {/* —— 右:CTA 按钮 —— */}
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
            <Button size="default" onClick={() => setOpen(true)}>
              <MessageSquare className="h-4 w-4" />
              联系合作
            </Button>
            <Button
              size="default"
              variant="outline"
              onClick={() => setOpen(true)}
            >
              申请商务资料
            </Button>
          </div>
        </div>
      </section>

      <ContactDialog open={open} onOpenChange={setOpen} />
    </>
  );
};

// ──────────────────────────────────────────────────────────────
// 联系方式 Dialog — 3 张卡(微信 / 电话 / 邮箱),每张带"复制"按钮
// ──────────────────────────────────────────────────────────────

const ContactDialog: React.FC<{
  open: boolean;
  onOpenChange(o: boolean): void;
}> = ({ open, onOpenChange }) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-amber-500" />
            合作联系方式
          </DialogTitle>
          <DialogDescription>
            欢迎洽谈渠道加盟、私有化部署、行业定制 — 任选一种方式联系我们
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {PARTNERSHIP.contacts.map((c) => (
            <ContactRow key={c.label} contact={c} />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
};

const ContactRow: React.FC<{ contact: Contact }> = ({ contact }) => {
  const Icon = contact.icon;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--cy-brand-500)] to-[var(--cy-brand-700)] text-white shadow-sm">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-[var(--cy-text-tertiary)]">
          {contact.label}
        </p>
        <p className="font-mono text-sm font-semibold text-[var(--cy-text-primary)]">
          {contact.value}
        </p>
        <p className="text-[11px] text-[var(--cy-text-tertiary)]">{contact.hint}</p>
      </div>
      {contact.kind === 'qr' ? (
        <img
          src={contact.qrUrl}
          alt={`${contact.label} 二维码`}
          className="h-20 w-20 shrink-0 rounded-md border border-[var(--cy-border-subtle)] bg-white object-contain p-1"
        />
      ) : (
        <CopyButton value={contact.value} label={contact.label} />
      )}
    </div>
  );
};

const CopyButton: React.FC<{ value: string; label: string }> = ({ value, label }) => {
  const [copied, setCopied] = React.useState(false);
  const onCopy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      notifySuccess(`已复制 ${label}:${value}`);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 老 webview 不支持 clipboard API — fallback execCommand
      const ta = document.createElement('textarea');
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        notifySuccess(`已复制 ${label}`);
        setTimeout(() => setCopied(false), 1600);
      } catch {
        /* noop */
      }
      document.body.removeChild(ta);
    }
  }, [value, label]);

  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => void onCopy()}
      className="shrink-0 gap-1"
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" />
          已复制
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          复制
        </>
      )}
    </Button>
  );
};
