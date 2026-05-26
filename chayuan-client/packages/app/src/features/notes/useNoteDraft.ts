/**
 * 笔记草稿持久化 — localStorage,debounce 500ms。
 *
 * 用法:
 *   const draft = useNoteDraft('chayuan:note-draft:new');
 *   useEffect(() => draft.save(title, content), [title, content]);
 *   const initial = draft.load();
 *   draft.clear() 保存成功后调用
 */
import * as React from 'react';

export interface NoteDraftPayload {
  title: string;
  content: any; // Tiptap JSON
  savedAt: string;
  /** AI 生成的摘要 markdown(可选)。老草稿无此字段,load 时为 undefined。 */
  summary?: string;
}

export interface UseNoteDraftResult {
  load: () => NoteDraftPayload | null;
  save: (title: string, content: any, summary?: string) => void;
  clear: () => void;
}

export function useNoteDraft(key = 'chayuan:note-draft:new'): UseNoteDraftResult {
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = React.useCallback((): NoteDraftPayload | null => {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw) as NoteDraftPayload;
    } catch {
      return null;
    }
  }, [key]);

  const save = React.useCallback(
    (title: string, content: any, summary?: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        try {
          const payload: NoteDraftPayload = {
            title, content, savedAt: new Date().toISOString(),
          };
          if (summary !== undefined) payload.summary = summary;
          localStorage.setItem(key, JSON.stringify(payload));
        } catch {
          /* quota / privacy 模式;静默忽略 */
        }
      }, 500);
    },
    [key],
  );

  const clear = React.useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    try {
      localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  }, [key]);

  React.useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  return { load, save, clear };
}
