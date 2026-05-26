/**
 * KB 详情顶栏 — 在 dialog 模式 / 独立 Tab 模式都用同一份。
 *
 * 职责:
 *   - 显示 KB 名 / 类型 chip / mine / private / 描述
 *   - 提供刷新按钮(loading 时旋转)
 *   - 可选 close 按钮(dialog 模式传 onClose;独立 Tab 模式由 TabBar 接管 close,留空)
 *
 * 设计:工作区顶部以中性色为主,只用小面积类型色表达 kind,避免大面积高饱和色干扰阅读。
 */

import * as React from 'react';
import {
  ArrowLeft,
  Boxes, Database, FileText, Images, Lock,
  PanelLeftClose, PanelLeftOpen, RefreshCw, X,
} from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import type { KuDetail, KuKind } from '@chayuan/api';
import { KbAdminMenu } from '../KbAdminMenu';

export const KB_KIND_META: Record<KuKind, {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  accent: string;
  softBg: string;
  chip: string;
}> = {
  document: {
    icon: FileText,
    label: '文档库',
    accent: 'text-sky-600',
    softBg: 'bg-sky-50 ring-sky-100',
    chip: 'bg-sky-50 text-sky-700 ring-sky-100',
  },
  image: {
    icon: Images,
    label: '图像库',
    accent: 'text-rose-600',
    softBg: 'bg-rose-50 ring-rose-100',
    chip: 'bg-rose-50 text-rose-700 ring-rose-100',
  },
  structured: {
    icon: Database,
    label: '数据库',
    accent: 'text-emerald-600',
    softBg: 'bg-emerald-50 ring-emerald-100',
    chip: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
  },
  vector: {
    icon: Boxes,
    label: '向量库',
    accent: 'text-amber-600',
    softBg: 'bg-amber-50 ring-amber-100',
    chip: 'bg-amber-50 text-amber-700 ring-amber-100',
  },
};

export interface KbDetailHeaderProps {
  detail: KuDetail;
  loading?: boolean;
  onRefresh(): void;
  onClose?(): void;
  /** 用 sr-only 还是普通 — dialog 内用 sr-only(Radix 用 DialogTitle 接管) */
  titleSrOnly?: boolean;
  /**
   * KB 主体(文件/表清单)是否折叠;非空时在标题左侧渲染折叠/展开按钮。
   * 折叠 = 文件清单收成右侧细边栏,把空间留给预览 + 底部 composer。
   */
  bodyCollapsed?: boolean;
  onToggleBody?(): void;
  /**
   * 删除成功后回调:让父级关 detail tab / 跳回 /kb;
   * 不传则只 invalidate query,UI 仍留在该 tab(显示空态)
   */
  onDeleted?(): void;
  /** 是否渲染 owner-only 操作菜单(默认 detail.meta.mine 决定) */
  adminMenu?: boolean;
  /** 左上角返回按钮 — 点击后回 KB 主页;不传则不渲染。Tab 模式 / 内嵌模式都用得上 */
  onBack?(): void;
}

export const KbDetailHeader: React.FC<KbDetailHeaderProps> = ({
  detail,
  loading,
  onRefresh,
  onClose,
  bodyCollapsed,
  onToggleBody,
  onDeleted,
  adminMenu,
  onBack,
}) => {
  const showAdmin = adminMenu ?? !!detail.meta.mine;
  const meta = KB_KIND_META[detail.kind] ?? KB_KIND_META.document;
  const Icon = meta.icon;
  return (
    <header
      className={cn(
        'relative flex flex-shrink-0 items-start gap-3 overflow-hidden border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-5 py-3 text-[var(--cy-text-primary)]',
      )}
    >
      <span
        aria-hidden
        className={cn('absolute inset-y-0 left-0 w-1', meta.softBg)}
      />
      {onBack && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onBack}
          aria-label="返回知识库"
          title="返回知识库"
          className="relative h-8 w-8 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
      )}
      {onToggleBody && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleBody}
          aria-label={bodyCollapsed ? '展开列表' : '折叠列表'}
          title={bodyCollapsed ? '展开列表' : '折叠列表'}
          className="relative h-8 w-8 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        >
          {bodyCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
      )}
      <div className={cn('relative flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl ring-1', meta.softBg, meta.accent)}>
        <Icon className="h-5 w-5" />
      </div>
      <div className="relative min-w-0 flex-1">
        <h2 className="truncate text-lg font-semibold">{detail.meta.display_name}</h2>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-[var(--cy-text-tertiary)]">
          <span className={cn('rounded-full px-2 py-0.5 font-medium ring-1', meta.chip)}>{meta.label}</span>
          {detail.meta.visibility === 'private' ? (
            <span className="inline-flex items-center gap-1">
              <Lock className="h-3 w-3" /> 私有
            </span>
          ) : (
            <span>公开</span>
          )}
          {detail.meta.mine && <span className="rounded-full bg-[var(--cy-surface-1)] px-2 py-0.5 text-[var(--cy-text-secondary)] ring-1 ring-[var(--cy-border-subtle)]">我的</span>}
          {detail.meta.description && (
            <span className="truncate">· {detail.meta.description}</span>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="icon"
        onClick={onRefresh}
        disabled={loading}
        aria-label="刷新"
        className="relative h-8 w-8 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
      >
        <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
      </Button>
      {showAdmin && <KbAdminMenu detail={detail} onDeleted={onDeleted} /> }
      {onClose && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="关闭"
          className="relative h-8 w-8 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        >
          <X className="h-4 w-4" />
        </Button>
      )}
    </header>
  );
};
