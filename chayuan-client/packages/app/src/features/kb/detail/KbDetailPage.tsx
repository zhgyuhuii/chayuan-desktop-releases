/**
 * KbDetailPage —— /kb/$kuId 独立 Tab 页(架构师视角:模块化 + 可复用 + 高并发)。
 *
 * 关键布局(D 修订:预览铺到底,与左列消息流并排):
 *   ┌──────────────── KbDetailHeader ────────────────────────┐
 *   │  ← 返回 / 折叠 / KB 名 / 类型 / 元 / 刷新                 │
 *   ├──────────────────────────┬──────────────────────────────┤
 *   │  ┌─ Left Column ──────┐  │  ┌─ Preview(embedded) ───┐  │
 *   │  │ Browse / Chat 视图  │  │  │  从 header 下沿一直   │  │
 *   │  │ (含自带 composer)   │  │  │  铺到面板底部         │  │
 *   │  │                     │  │  │  (左列结束的同一行)   │  │
 *   │  └─────────────────────┘  │  └────────────────────────┘  │
 *   └──────────────────────────┴──────────────────────────────┘
 *
 * 模式机(C 修订):
 *   - browse  :文件清单 + 草稿 ConversationView(用户首次发消息触发切换)
 *   - chat    :左列变成消息流 + composer;文件清单收成左侧 32px 折叠条
 *               click 折叠条 → 重新展开列表(chat 不丢,折叠条恢复 ChevronRight)
 *
 * 复用:
 *   - 消息状态机 / 落库 / 草稿 lazy 创建 全部委托 <ConversationView>
 *   - <PreviewPanel embedded /> 已经 claim inline 模式,全局抽屉自动退让
 *   - buildRequest 透传 composer 全局态(setKuIds 在 onActivate 已经把 ku_id
 *     映射回 selectedKbs,自然 mode='kb'),无需各路再实现一份 KB 锁定
 */

import * as React from 'react';
import { AlertCircle, ChevronRight, Columns3, MessageSquare, PanelRightClose, PanelRightOpen, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { Button, cn } from '@chayuan/ui';
import { useKuDetail, KU_LIST_KEY, KU_DETAIL_KEY } from './useKuDetail';
import { KB_KIND_META, KbDetailHeader } from './KbDetailHeader';
import { useTabsStore } from '../../../store/tabs';
import { DocumentKbDetail } from './DocumentKbDetail';
import { ImageKbDetail } from './ImageKbDetail';
import { StructuredKbDetail } from './StructuredKbDetail';
import { VectorKbDetail } from './VectorKbDetail';
import { ConversationView } from '../../chat/ConversationView';
import { useComposerStore } from '../../../store/composer';
import { PreviewPanel, usePreviewStore } from '../../preview';
import { KbDropZone } from '../upload/KbDropZone';
import { PendingMountsBanner } from './PendingMountsBanner';

const STORAGE_BODY_COLLAPSED_PREFIX = 'cy.kb.detail.bodyCollapsed.';
const STORAGE_CHAT_COLLAPSED_PREFIX = 'cy.kb.detail.chatCollapsed.';

function readBodyCollapsedLS(kuId: string): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_BODY_COLLAPSED_PREFIX + kuId) === '1';
  } catch {
    return false;
  }
}

function writeBodyCollapsedLS(kuId: string, v: boolean): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_BODY_COLLAPSED_PREFIX + kuId, v ? '1' : '0');
  } catch {
    /* ignore */
  }
}

function readChatCollapsedLS(kuId: string): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_CHAT_COLLAPSED_PREFIX + kuId) === '1';
  } catch {
    return false;
  }
}

function writeChatCollapsedLS(kuId: string, v: boolean): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_CHAT_COLLAPSED_PREFIX + kuId, v ? '1' : '0');
  } catch {
    /* ignore */
  }
}

export interface KbDetailPageProps {
  kuId: string;
}

export const KbDetailPage: React.FC<KbDetailPageProps> = ({ kuId }) => {
  const qc = useQueryClient();
  const detailQ = useKuDetail(kuId);

  // 预览状态(全局 store);本页只读
  const previewCurrent = usePreviewStore((s) => s.current);
  const claimInline = usePreviewStore((s) => s.claimInline);

  // 列表折叠(per-KB 持久化)
  const [bodyCollapsed, setBodyCollapsed] = React.useState(() => readBodyCollapsedLS(kuId));
  const [chatCollapsed, setChatCollapsed] = React.useState(() => readChatCollapsedLS(kuId));
  React.useEffect(() => {
    setBodyCollapsed(readBodyCollapsedLS(kuId));
    setChatCollapsed(readChatCollapsedLS(kuId));
  }, [kuId]);
  const toggleBody = React.useCallback(() => {
    setBodyCollapsed((v) => {
      const next = !v;
      if (next && chatCollapsed) {
        setChatCollapsed(false);
        writeChatCollapsedLS(kuId, false);
      }
      writeBodyCollapsedLS(kuId, next);
      return next;
    });
  }, [chatCollapsed, kuId]);
  const toggleChat = React.useCallback(() => {
    setChatCollapsed((v) => {
      const next = !v;
      if (next && bodyCollapsed) {
        setBodyCollapsed(false);
        writeBodyCollapsedLS(kuId, false);
      }
      writeChatCollapsedLS(kuId, next);
      return next;
    });
  }, [bodyCollapsed, kuId]);

  // 本页内的对话 id —— 进入 KB tab 时是 null(浏览模式),首发消息后转到 chat 模式
  // (per-tab 内存即可,KB 多个 tab 各管各的 conversation;路由 query 不暂存)
  const [convId, setConvId] = React.useState<string | null>(null);
  // 切换 KB(同一个组件实例切到别的 ku_id) → 重置 chat 状态
  React.useEffect(() => {
    setConvId(null);
  }, [kuId]);

  // 进入 KB tab 时 onActivate 已经 setKuIds([kuId]),保证 chat 走 kb 模式;
  // 这里再次校准一次,防止用户从其它 KB tab 切过来后状态错位。
  const setKuIds = useComposerStore((s) => s.setKuIds);
  React.useEffect(() => {
    setKuIds([kuId]);
  }, [kuId, setKuIds]);

  // claim inline 模式
  React.useEffect(() => claimInline(), [claimInline]);

  const onMutate = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: KU_DETAIL_KEY(kuId) });
    void qc.invalidateQueries({ queryKey: KU_LIST_KEY });
  }, [qc, kuId]);

  // 草稿首发回调 — 立即切到 chat 模式 + 自动折叠列表
  const handleConversationCreated = React.useCallback(
    (id: string, _title: string) => {
      setConvId(id);
      setChatCollapsed(false);
      writeChatCollapsedLS(kuId, false);
    },
    [kuId],
  );

  if (detailQ.isLoading) {
    return <KbDetailLoading />;
  }
  if (detailQ.isError || !detailQ.data) {
    return <KbDetailErrorView error={detailQ.error as Error | null} onRetry={() => detailQ.refetch()} />;
  }

  const detail = detailQ.data;
  const mine = !!detail.meta.mine;
  const canWrite = !!(detail.meta.can_write || detail.meta.mine);
  const hasPreview = !!previewCurrent;
  const inChatMode = convId !== null;

  // 文档 KB 才提供 chat 形态(图像 / 结构化 / 向量 库各有专用面板)
  const supportsChat = detail.kind === 'document';

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[var(--cy-surface-base)]">
      <KbDetailHeader
        detail={detail}
        loading={detailQ.isFetching}
        onRefresh={onMutate}
        bodyCollapsed={bodyCollapsed}
        onToggleBody={toggleBody}
        onBack={() => {
          const tabs = useTabsStore.getState();
          tabs.open('/kb');
          const cur = tabs.tabs.find(
            (t) => t.path === `/kb/${encodeURIComponent(kuId)}`,
          );
          if (cur) tabs.close(cur.id);
        }}
        onDeleted={() => {
          const tabs = useTabsStore.getState();
          const cur = tabs.tabs.find(
            (t) => t.path === `/kb/${encodeURIComponent(kuId)}`,
          );
          if (cur) tabs.close(cur.id);
        }}
      />

      {supportsChat && (
        <DetailLayoutSwitch
          filesCollapsed={bodyCollapsed}
          chatCollapsed={chatCollapsed}
          onToggleFiles={toggleBody}
          onToggleChat={toggleChat}
        />
      )}

      {/* 数据挂载 corpus_pending 待 ingest banner — 仅当目标是本 KB 且有待
          确认任务时才渲染;失败/为空时零侵入 */}
      {detail?.meta?.name && <PendingMountsBanner kbName={detail.meta.name} />}

      {/* 中间区域:左列(Browse / Chat)| 右列(Preview),两列都从 header 下沿
          一直填到面板底部。 */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左列容器 — 高度填满父容器,内部由 Browse / Chat 子布局自管 composer 位置;
            外层 KbDropZone 让整个详情区都接受拖拽(文件夹 / 多文件均支持),
            owner 才放行,non-owner / 不支持 kind 拖入会 toast 拒绝 */}
        <main
          className={cn(
            'flex min-h-0 min-w-0 flex-col overflow-hidden',
            hasPreview ? 'flex-[3]' : 'flex-1',
          )}
        >
         <KbDropZone
           kb={detail.meta}
           canUpload={canWrite}
           hint={supportsChat ? 'none' : 'none'}
           className="flex min-h-0 flex-1 flex-col overflow-hidden"
         >
          {supportsChat ? (
            // 文档 KB:**单一 ConversationView 实例**,通过 CSS 切布局而不是切组件;
            // 否则草稿→对话过渡时父级重渲会 unmount/remount,在飞的 stream 被 abort,
            // 第一条消息直接消失(本会话用户报"首发回车后消息为空"的根因)。
            //
            // 布局规则:
            //   - inChatMode + 文件列表展开 → 左侧 aside (w-72) + 右侧 ConversationView
            //   - inChatMode + 文件列表收 → 左侧 rail (w-12) + 右侧 ConversationView
            //   - !inChatMode → 上方文件列表(flex-1) + 下方 ConversationView(flex-shrink-0)
            <div className={cn(
              'flex min-h-0 flex-1 overflow-hidden',
              inChatMode ? 'flex-row' : 'flex-col',
            )}>
              {/* slot A:文件列表 / rail */}
              <div className={cn(
                'flex flex-col overflow-hidden',
                inChatMode
                  ? cn(
                      'flex-shrink-0 border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]',
                      bodyCollapsed ? 'w-12' : chatCollapsed ? 'min-w-0 flex-1' : 'w-72',
                    )
                  : cn(
                      'min-h-0',
                      bodyCollapsed ? 'flex-1 flex-row' : 'flex-1',
                    ),
              )}>
                {bodyCollapsed ? (
                  inChatMode ? (
                    <CollapsedRail
                      kind={detail.kind}
                      onExpand={() => {
                        setBodyCollapsed(false);
                        writeBodyCollapsedLS(kuId, false);
                      }}
                    />
                  ) : (
                    <div className="flex min-h-0 flex-1">
                      <CollapsedRail kind={detail.kind} onExpand={toggleBody} />
                      <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
                        点左侧条展开列表
                      </div>
                    </div>
                  )
                ) : (
                  <DocumentKbDetail
                    detail={detail}
                    mine={canWrite}
                    onMutate={onMutate}
                    onToggleCollapse={toggleBody}
                  />
                )}
              </div>
              {/* slot B:ConversationView(永不被 unmount) */}
              <div className={cn(
                'flex min-h-0 flex-col overflow-hidden',
                inChatMode
                  ? chatCollapsed
                    ? 'w-12 flex-shrink-0 border-l border-[var(--cy-border-subtle)]'
                    : 'min-w-0 flex-1'
                  : 'flex-shrink-0 border-t border-[var(--cy-border-subtle)]',
              )}>
                {chatCollapsed && <CollapsedChatRail onExpand={toggleChat} vertical={inChatMode} />}
                <ConversationView
                  conversationId={convId}
                  onConversationCreated={handleConversationCreated}
                  mode="kb"
                  emptySlot={inChatMode ? <KbChatWelcome name={detail.meta.display_name} /> : null}
                  placeholder={
                    inChatMode
                      ? `继续在「${detail.meta.display_name}」里聊…`
                      : `问问「${detail.meta.display_name}」里的内容…`
                  }
                  className={cn(chatCollapsed && 'hidden', inChatMode ? undefined : 'h-auto')}
                />
              </div>
            </div>
          ) : (
            // 非 document KB:无 chat 形态,只渲染对应详情面板
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {bodyCollapsed ? (
                <div className="flex min-h-0 flex-1">
                  <CollapsedRail kind={detail.kind} onExpand={toggleBody} />
                  <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
                    点左侧条展开列表
                  </div>
                </div>
              ) : (
                <div className="flex min-h-0 flex-1 overflow-hidden">
                  {detail.kind === 'image' && <ImageKbDetail detail={detail} mine={canWrite} onMutate={onMutate} />}
                  {detail.kind === 'structured' && <StructuredKbDetail detail={detail} mine={mine} onMutate={onMutate} />}
                  {detail.kind === 'vector' && <VectorKbDetail detail={detail} mine={mine} onMutate={onMutate} />}
                </div>
              )}
            </div>
          )}
         </KbDropZone>
        </main>

        {/* 右列:预览面板;只在有 current 时占空间,从 header 一直延展到底 */}
        {hasPreview && (
          <aside className="flex min-h-0 min-w-0 flex-[4] flex-col overflow-hidden">
            <PreviewPanel embedded />
          </aside>
        )}
      </div>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// Layout controls
// ──────────────────────────────────────────────────────────────

const DetailLayoutSwitch: React.FC<{
  filesCollapsed: boolean;
  chatCollapsed: boolean;
  onToggleFiles(): void;
  onToggleChat(): void;
}> = ({ filesCollapsed, chatCollapsed, onToggleFiles, onToggleChat }) => (
  <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-1.5">
    <div className="inline-flex items-center gap-1 text-[11px] text-[var(--cy-text-tertiary)]">
      <Columns3 className="h-3.5 w-3.5" />
      <span>
        {filesCollapsed
          ? '仅展示对话'
          : chatCollapsed
            ? '仅展示文件列表'
            : '文件列表和对话同时展示'}
      </span>
    </div>
    <div className="inline-flex rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-0.5">
      <button
        type="button"
        onClick={onToggleFiles}
        className={cn(
          'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]',
          filesCollapsed ? 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]' : 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]',
        )}
      >
        {filesCollapsed ? <PanelRightOpen className="h-3 w-3" /> : <PanelRightClose className="h-3 w-3" />}
        文件
      </button>
      <button
        type="button"
        onClick={onToggleChat}
        className={cn(
          'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]',
          chatCollapsed ? 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]' : 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]',
        )}
      >
        {chatCollapsed ? <PanelRightOpen className="h-3 w-3" /> : <PanelRightClose className="h-3 w-3" />}
        对话
      </button>
    </div>
  </div>
);

const CollapsedChatRail: React.FC<{ onExpand(): void; vertical: boolean }> = ({ onExpand, vertical }) => (
  <button
    type="button"
    onClick={onExpand}
    title="展开对话"
    aria-label="展开对话"
    className={cn(
      'group/chat flex flex-shrink-0 items-center justify-center gap-2 bg-[var(--cy-surface-1)] text-[var(--cy-text-tertiary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
      vertical
        ? 'h-full w-12 flex-col border-l border-[var(--cy-border-subtle)]'
        : 'h-10 w-full border-t border-[var(--cy-border-subtle)]',
    )}
  >
    <MessageSquare className="h-4 w-4" />
    <span className={cn('text-[10px] font-medium', vertical && '[writing-mode:vertical-rl]')}>展开对话</span>
  </button>
);

// ──────────────────────────────────────────────────────────────
// CollapsedRail
// ──────────────────────────────────────────────────────────────

const CollapsedRail: React.FC<{
  kind: 'document' | 'image' | 'structured' | 'vector';
  onExpand(): void;
}> = ({ kind, onExpand }) => {
  const meta = KB_KIND_META[kind] ?? KB_KIND_META.document;
  const Icon = meta.icon;
  return (
    <button
      type="button"
      onClick={onExpand}
      title="展开列表"
      aria-label="展开列表"
      className="group/rail flex h-full w-9 flex-shrink-0 flex-col items-center justify-start gap-3 border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] py-3 text-[var(--cy-text-tertiary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
    >
      <ChevronRight className="h-4 w-4 transition-transform group-hover/rail:translate-x-0.5" />
      <span className={cn('flex h-7 w-7 items-center justify-center rounded-md ring-1', meta.softBg, meta.accent)}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="vertical-rl text-[10px] font-medium [writing-mode:vertical-rl]">{meta.label}</span>
    </button>
  );
};

const KbChatWelcome: React.FC<{ name: string }> = ({ name }) => (
  <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-indigo-600 text-white">
      <MessageSquare className="h-6 w-6" />
    </div>
    <h2 className="text-lg font-semibold">在「{name}」里聊</h2>
    <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">
      问点什么开始对话;回答会自动引用本知识库的相关文件,点引用 chip 可重开预览面板。
    </p>
  </div>
);

// ──────────────────────────────────────────────────────────────
// Loading / Error
// ──────────────────────────────────────────────────────────────

const KbDetailLoading: React.FC = () => (
  <div className="flex h-full flex-col">
    <div className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-5 py-3">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 animate-pulse rounded-xl bg-[var(--cy-surface-2)]" />
        <div className="space-y-1.5">
          <div className="h-4 w-40 animate-pulse rounded bg-[var(--cy-surface-2)]" />
          <div className="h-3 w-24 animate-pulse rounded bg-[var(--cy-surface-1)]" />
        </div>
      </div>
    </div>
    <div className="flex flex-1 items-center justify-center text-sm text-[var(--cy-text-tertiary)]">加载中…</div>
  </div>
);

const KbDetailErrorView: React.FC<{ error: Error | null; onRetry(): void }> = ({ error, onRetry }) => (
  <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 text-amber-600">
      <AlertCircle className="h-6 w-6" />
    </div>
    <p className="text-sm font-medium">加载知识库详情失败</p>
    <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">{error?.message || '未知错误'}</p>
    <Button size="sm" onClick={onRetry} className="mt-2">
      <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> 重试
    </Button>
  </div>
);
