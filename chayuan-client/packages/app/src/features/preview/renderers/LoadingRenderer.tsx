import { Loader2 } from 'lucide-react';
import type * as React from 'react';

export const LoadingRenderer: React.FC<{ label?: string }> = ({ label = '加载中…' }) => (
  <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-[var(--cy-text-tertiary)]">
    <Loader2 className="h-5 w-5 animate-spin text-[var(--cy-brand-500)]" />
    <span>{label}</span>
  </div>
);
