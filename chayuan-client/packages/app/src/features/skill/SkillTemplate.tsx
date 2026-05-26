/**
 * SkillTemplate —— 通用能力页(写作 / 翻译 / 妙记 / 同传 / 修图 / 操控)。
 *
 * 路由:/skill/$slug(由 page-registry 解析)
 *
 * 数据:packages/api 的 SKILLS fixture(前端常量)
 *
 * 行为:
 *   - 选模板 → 拼好 systemPrompt 进 useComposerStore.draft
 *   - "发送" → 跳转 /chat/{newId} 由 ChatPage 处理流式
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { uuid } from '@chayuan/platform-shared';
import { useQueryClient } from '@tanstack/react-query';
import { findSkill, type SkillSpec, type SkillTemplateItem } from '@chayuan/api';
import { SegmentedTabs, type SegmentedItem, cn } from '@chayuan/ui';
import { ChatComposer } from '../composer/ChatComposer';
import { useComposerStore } from '../../store/composer';
import { useTabsStore } from '../../store/tabs';
import { upsertConversation } from '../conversations/persistence';
import { CONVERSATION_KEYS } from '../conversations/useConversations';
import { useTranslation } from '../../i18n';

export interface SkillTemplateProps {
  slug: string;
}

export const SkillTemplate: React.FC<SkillTemplateProps> = ({ slug }) => {
  const skill = findSkill(slug);
  if (!skill) return <NotFound slug={slug} />;
  return <SkillView skill={skill} />;
};

const SkillView: React.FC<{ skill: SkillSpec }> = ({ skill }) => {
  const { t } = useTranslation();
  const [active, setActive] = React.useState(skill.categories[0]?.id ?? 'recommended');
  const setDraft = useComposerStore((s) => s.setDraft);
  const setActiveConv = useComposerStore((s) => s.setActive);
  const open = useTabsStore((s) => s.open);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const items: SegmentedItem[] = skill.categories.map((c) => ({
    value: c.id,
    label: c.label,
  }));

  const visible = skill.templates.filter(
    (tpl) => active === 'recommended' || tpl.category === active,
  );

  const groups = React.useMemo(() => {
    if (active !== 'recommended') return null;
    const byCat: Record<string, SkillTemplateItem[]> = {};
    for (const tpl of skill.templates) {
      const c = tpl.category;
      (byCat[c] ??= []).push(tpl);
    }
    return Object.entries(byCat);
  }, [skill.templates, active]);

  const onSelectTemplate = (tpl: SkillTemplateItem) => {
    setDraft(`${tpl.systemPrompt}\n\n`);
  };

  const onSend = async (content: string) => {
    const id = uuid();
    const now = Date.now();
    await upsertConversation({
      id,
      remote_id: null,
      title: `${skill.title} · ${content.slice(0, 30)}`,
      model: useComposerStore.getState().modelId,
      mode: 'agent',
      created_at: now,
      updated_at: now,
    });
    setActiveConv(id);
    void qc.invalidateQueries({ queryKey: CONVERSATION_KEYS.list });
    const path = `/chat/${encodeURIComponent(id)}`;
    open(path, { title: skill.title, icon: 'message-square', forceNew: true });
    void navigate({ to: path as never });
    setDraft(content);
  };

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col px-6 py-8">
      {/* Header */}
      <div className="text-center">
        <div className="mx-auto inline-flex items-center gap-2">
          <span className="text-2xl">{skill.icon}</span>
          <h1 className="text-2xl font-semibold text-[var(--cy-text-primary)]">{skill.title}</h1>
        </div>
        <p className="mt-1 text-sm text-[var(--cy-text-secondary)]">{skill.subtitle}</p>
      </div>

      {/* Categories */}
      <div className="mt-6">
        <SegmentedTabs items={items} value={active} onChange={setActive} />
      </div>

      {/* Templates(推荐 Tab 时按分组渲染;否则单一列表) */}
      <div className="mt-4 flex-1 overflow-y-auto">
        {groups
          ? groups.map(([cat, list]) => (
              <CategorySection
                key={cat}
                title={skill.categories.find((c) => c.id === cat)?.label ?? cat}
                templates={list}
                onSelect={onSelectTemplate}
              />
            ))
          : (
              <CategorySection
                title=""
                templates={visible}
                onSelect={onSelectTemplate}
              />
            )}
      </div>

      {/* Composer */}
      <div className="pt-4">
        <ChatComposer
          isStreaming={false}
          onSend={onSend}
          placeholder={skill.placeholder ?? t('chat.inputPlaceholder', { model: '' })}
        />
      </div>
    </div>
  );
};

const CategorySection: React.FC<{
  title: string;
  templates: SkillTemplateItem[];
  onSelect: (tpl: SkillTemplateItem) => void;
}> = ({ title, templates, onSelect }) => (
  <section className={cn('mt-4', !title && 'mt-0')}>
    {title && (
      <h2 className="mb-2 text-sm font-medium text-[var(--cy-text-secondary)]">{title}</h2>
    )}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {templates.map((tpl) => (
        <button
          key={tpl.id}
          type="button"
          onClick={() => onSelect(tpl)}
          className="flex flex-col rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-4 text-left transition-colors hover:border-[var(--cy-brand-300)]"
        >
          <div className="flex items-center gap-2">
            <span
              className="flex h-7 w-7 items-center justify-center rounded-md text-base"
              style={{ backgroundColor: tpl.iconBg + '22' }}
            >
              {tpl.icon}
            </span>
            <p className="truncate text-sm font-medium text-[var(--cy-text-primary)]">
              {tpl.title}
            </p>
          </div>
          <p className="mt-2 line-clamp-2 text-xs text-[var(--cy-text-tertiary)]">
            {tpl.description}
          </p>
        </button>
      ))}
    </div>
  </section>
);

const NotFound: React.FC<{ slug: string }> = ({ slug }) => (
  <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
    未识别的能力:/skill/{slug}
  </div>
);
