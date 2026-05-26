/**
 * 知识库详情容器 — 由 ku_id 路由到 4 个子面板。
 *
 * 使用方:
 *   <KbDetailDialog kuId={...} onClose={...} />
 *
 * 内部:
 *   - useQuery 拉 universe.detail
 *   - 按 detail.kind 渲染 DocumentKbDetail / ImageKbDetail / StructuredKbDetail / VectorKbDetail
 *   - 子面板调 onMutate() 后 invalidate(['ku.detail', kuId])
 *   - 错误 / 加载中各有占位
 *
 * 体积:容器本体 ~120 行,子面板各自 200~300 行,职责清晰
 */

import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Boxes, Database, FileText, Images, Library, Lock, RefreshCw, X } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  Button,
  cn,
} from '@chayuan/ui';
import { knowledgeUniverse, type KuDetail, type KuKind } from '@chayuan/api';
import { DocumentKbDetail } from './detail/DocumentKbDetail';
import { ImageKbDetail } from './detail/ImageKbDetail';
import { StructuredKbDetail } from './detail/StructuredKbDetail';
import { VectorKbDetail } from './detail/VectorKbDetail';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';

export interface KbDetailDialogProps {
  kuId: string | null;
  onClose(): void;
}

const KIND_META: Record<KuKind, { icon: React.ComponentType<{ className?: string }>; label: string; tint: string }> = {
  document: { icon: FileText, label: '文档库', tint: 'from-sky-500 to-indigo-600' },
  image: { icon: Images, label: '图像库', tint: 'from-pink-500 to-rose-600' },
  structured: { icon: Database, label: '数据库', tint: 'from-emerald-500 to-teal-600' },
  vector: { icon: Boxes, label: '向量库', tint: 'from-amber-500 to-orange-600' },
};

export const KbDetailDialog: React.FC<KbDetailDialogProps> = ({ kuId, onClose }) => {
  const qc = useQueryClient();
  const open = !!kuId;

  const detailQ = useQuery<KuDetail>({
    queryKey: ['ku.detail', kuId],
    queryFn: () => knowledgeUniverse.detail(kuId as string),
    enabled: open,
    staleTime: 30 * 1000,
  });

  const onMutate = () => {
    void qc.invalidateQueries({ queryKey: ['ku.detail', kuId] });
    // 同步把 list 也失效一次,文件数 / 图片数等会变
    void qc.invalidateQueries({ queryKey: ['ku.list'] });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <DialogContent
        hideClose
        className={cn(
          'flex h-[85vh] max-h-[900px] w-[92vw] max-w-5xl flex-col gap-0 overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-0 shadow-[var(--cy-shadow-lg)]',
          'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
          'data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95',
        )}
      >
        {detailQ.isLoading ? (
          <Loading onClose={onClose} />
        ) : detailQ.isError || !detailQ.data ? (
          <ErrorView error={detailQ.error as Error | null} onRetry={() => detailQ.refetch()} onClose={onClose} />
        ) : (
          <>
            <Header detail={detailQ.data} onClose={onClose} onRefresh={onMutate} loading={detailQ.isFetching} />
            <div className="min-h-0 flex-1 overflow-hidden">
              <RouteByKind detail={detailQ.data} mine={!!detailQ.data.meta.mine} onMutate={onMutate} />
            </div>
          </>
        )}
        <style>{`@keyframes kbHeaderShine{0%,100%{transform:translateX(0) skewX(-12deg);opacity:0}50%{transform:translateX(380%) skewX(-12deg);opacity:1}}`}</style>
      </DialogContent>
    </Dialog>
  );
};

// ──────────────────────────────────────────────────────────────
// Header
// ──────────────────────────────────────────────────────────────

const Header: React.FC<{
  detail: KuDetail;
  loading: boolean;
  onClose(): void;
  onRefresh(): void;
}> = ({ detail, loading, onClose, onRefresh }) => {
  const meta = KIND_META[detail.kind] ?? KIND_META.document;
  const Icon = meta.icon;
  return (
    <header className={cn('relative flex items-start gap-3 overflow-hidden px-5 py-3 text-white', `bg-gradient-to-r ${meta.tint}`)}>
      {/* 微动效:对角扫光 */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 -skew-x-12 bg-gradient-to-r from-transparent via-white/12 to-transparent"
        style={{ animation: 'kbHeaderShine 3.5s ease-in-out infinite' }}
      />
      <div className="relative flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
        <Icon className="h-5 w-5" />
      </div>
      <div className="relative min-w-0 flex-1">
        <DialogTitle asChild>
          <h2 className="truncate text-lg font-semibold">{detail.meta.display_name}</h2>
        </DialogTitle>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-white/85">
          <span className="rounded-full bg-white/20 px-2 py-0.5 font-medium">{meta.label}</span>
          {detail.meta.visibility === 'private' ? (
            <span className="inline-flex items-center gap-1">
              <Lock className="h-3 w-3" /> 私有
            </span>
          ) : (
            <span>公开</span>
          )}
          {detail.meta.mine && <span className="rounded-full bg-white/20 px-2 py-0.5">我的</span>}
          {detail.meta.description && (
            <span className="truncate opacity-90">· {detail.meta.description}</span>
          )}
        </div>
        <DialogDescription className="sr-only">{detail.meta.description}</DialogDescription>
      </div>
      <Button
        variant="ghost"
        size="icon"
        onClick={onRefresh}
        disabled={loading}
        aria-label="刷新"
        className="relative h-8 w-8 text-white hover:bg-white/20"
      >
        {loading ? <AssistantBrandLogo running className="h-4 w-4 rounded-full" /> : <RefreshCw className="h-4 w-4" />}
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onClose}
        aria-label="关闭"
        className="relative h-8 w-8 text-white hover:bg-white/20"
      >
        <X className="h-4 w-4" />
      </Button>
    </header>
  );
};

// ──────────────────────────────────────────────────────────────
// 按 kind 路由
// ──────────────────────────────────────────────────────────────

const RouteByKind: React.FC<{ detail: KuDetail; mine: boolean; onMutate(): void }> = ({
  detail,
  mine,
  onMutate,
}) => {
  switch (detail.kind) {
    case 'document':
      return <DocumentKbDetail detail={detail} mine={mine} onMutate={onMutate} />;
    case 'image':
      return <ImageKbDetail detail={detail} mine={mine} onMutate={onMutate} />;
    case 'structured':
      return <StructuredKbDetail detail={detail} mine={mine} onMutate={onMutate} />;
    case 'vector':
      return <VectorKbDetail detail={detail} mine={mine} onMutate={onMutate} />;
    default:
      return (
        <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
          未知 kind: {(detail as { kind: string }).kind}
        </div>
      );
  }
};

// ──────────────────────────────────────────────────────────────
// Loading / Error
// ──────────────────────────────────────────────────────────────

/**
 * Radix Dialog 强制要求 DialogContent 内必须有 DialogTitle + Description,
 * 否则会发"DialogContent requires a DialogTitle for accessibility..."警告。
 * Loading / Error 这两态没有真实标题可显示,用 sr-only 让屏幕阅读器拿到一段
 * 占位文案,视觉上仍然按设计稿走骨架/错误屏。
 */

const Loading: React.FC<{ onClose(): void }> = ({ onClose }) => (
  <div className="flex h-full flex-col">
    <DialogTitle className="sr-only">加载知识库详情</DialogTitle>
    <DialogDescription className="sr-only">正在拉取知识库元信息,请稍候</DialogDescription>
    <div className="flex items-center justify-between bg-gradient-to-r from-slate-400 to-slate-500 px-5 py-3 text-white">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 animate-pulse rounded-xl bg-white/20" />
        <div className="space-y-1.5">
          <div className="h-4 w-40 animate-pulse rounded bg-white/30" />
          <div className="h-3 w-24 animate-pulse rounded bg-white/20" />
        </div>
      </div>
      <Button size="icon" variant="ghost" onClick={onClose} className="h-8 w-8 text-white hover:bg-white/20" aria-label="关闭">
        <X className="h-4 w-4" />
      </Button>
    </div>
    <div className="flex flex-1 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
      加载中…
    </div>
  </div>
);

const ErrorView: React.FC<{ error: Error | null; onRetry(): void; onClose(): void }> = ({
  error,
  onRetry,
  onClose,
}) => (
  <div className="flex h-full flex-col">
    <DialogTitle className="sr-only">加载知识库详情失败</DialogTitle>
    <DialogDescription className="sr-only">{error?.message || '未知错误'}</DialogDescription>
    <div className="flex items-center justify-end bg-red-500/90 px-5 py-3 text-white">
      <Button size="icon" variant="ghost" onClick={onClose} className="h-8 w-8 text-white hover:bg-white/20" aria-label="关闭">
        <X className="h-4 w-4" />
      </Button>
    </div>
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-600">
        <AlertCircle className="h-6 w-6" />
      </div>
      <p className="text-sm font-medium">加载知识库详情失败</p>
      <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">
        {error?.message || '未知错误'}
      </p>
      <Button size="sm" onClick={onRetry} className="mt-2">
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> 重试
      </Button>
    </div>
  </div>
);
