/**
 * 首页 — M1 占位。
 *
 * M2 替换为真正的:WelcomeOrb + Greeting + CapabilityRail + ChatComposer + SuggestionCards。
 */

import * as React from 'react';
import { useTranslation } from '../../i18n';

export const HomePlaceholder: React.FC = () => {
  const { t } = useTranslation();
  const hour = new Date().getHours();
  const greetingKey =
    hour < 12 ? 'home.greeting.morning' : hour < 18 ? 'home.greeting.afternoon' : 'home.greeting.evening';

  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center px-6 py-24">
      <div
        className="mb-6 h-16 w-16 rounded-full"
        style={{ background: 'var(--cy-iris-gradient)' }}
        aria-hidden
      />
      <h1 className="text-2xl font-semibold text-[var(--cy-text-primary)]">{t(greetingKey)}</h1>
      <p className="mt-2 text-sm text-[var(--cy-text-secondary)]">
        {t('home.inputPlaceholder')}
      </p>
      <p className="mt-8 text-xs text-[var(--cy-text-tertiary)]">M2 即将上线霓虹光晕 Composer</p>
    </div>
  );
};
