/**
 * 历史对话 — M1 已对接后端会话列表(useConversationList);
 * M4 升级为分组(今天/昨天/上周/更早)+ 大行气泡。
 */

import * as React from 'react';
import { MessageSquare } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { useConversationList } from '../conversations/useConversations';
import { useComposerStore } from '../../store/composer';
import { useTabsStore } from '../../store/tabs';
import { useTranslation } from '../../i18n';

export const HistoryPlaceholder: React.FC = () => {
  const { t } = useTranslation();
  const list = useConversationList();
  const setActive = useComposerStore((s) => s.setActive);
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();

  const onSelect = (id: string, title: string) => {
    setActive(id);
    const path = `/chat/${encodeURIComponent(id)}`;
    open(path, { title, icon: 'message-square' });
    void navigate({ to: path as never });
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold text-[var(--cy-text-primary)]">{t('history.title')}</h1>

      <div className="mt-6 flex flex-col gap-2">
        {list.isLoading && (
          <p className="text-sm text-[var(--cy-text-tertiary)]">{t('common.loading')}</p>
        )}
        {!list.isLoading && (list.data?.length ?? 0) === 0 && (
          <p className="text-sm text-[var(--cy-text-tertiary)]">{t('history.empty')}</p>
        )}
        {list.data?.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => onSelect(c.remote_id ?? c.id, c.title)}
            className="flex items-center gap-3 rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-3 text-left transition-colors hover:bg-[var(--cy-surface-2)]"
          >
            <MessageSquare className="h-4 w-4 flex-shrink-0 text-[var(--cy-text-tertiary)]" />
            <span className="truncate text-sm text-[var(--cy-text-primary)]">{c.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
};
