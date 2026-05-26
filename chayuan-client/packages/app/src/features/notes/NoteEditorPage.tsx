/**
 * 笔记编辑器全屏页 — 路由 /notes/new 的承载组件。
 *
 * 每次从主页点 AI 笔记进来:**生成唯一 draftKey**,确保新会话从空白开始,
 * 不会被上一次未保存的草稿污染。本次会话内的 autosave 仍写到该唯一 key 下,
 * tab 内刷新可恢复;关闭 tab 或切到别处再回来就是新空白笔记。
 *
 * 同时清理 24h 前的 `chayuan:note-draft:new-*` 残留 key,避免 localStorage
 * 长期累积。
 */
import * as React from 'react';
import { uuid } from '@chayuan/platform-shared';
import { NoteEditor } from './NoteEditor';

const DRAFT_KEY_PREFIX = 'chayuan:note-draft:new-';
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000; // 24h

/** 清掉 24h 前的临时草稿,防 localStorage 累积。新页一进就跑一次,失败静默。 */
function gcStaleDrafts(): void {
  try {
    const now = Date.now();
    const toDel: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(DRAFT_KEY_PREFIX)) continue;
      try {
        const raw = localStorage.getItem(k);
        if (!raw) { toDel.push(k); continue; }
        const obj = JSON.parse(raw) as { savedAt?: string };
        const t = obj.savedAt ? Date.parse(obj.savedAt) : NaN;
        if (!Number.isFinite(t) || now - t > DRAFT_TTL_MS) toDel.push(k);
      } catch {
        toDel.push(k);
      }
    }
    for (const k of toDel) localStorage.removeItem(k);
  } catch {
    /* localStorage 不可用 / quota / privacy 模式;静默 */
  }
}

export const NoteEditorPage: React.FC = () => {
  // useMemo([])  → 整个组件生命期内一个稳定 key;router 切换离开再回来,组件
  // 重新 mount → 重新 useMemo → 新 key → 空白笔记。
  const draftKey = React.useMemo(() => {
    gcStaleDrafts();
    return DRAFT_KEY_PREFIX + uuid();
  }, []);

  const onSaved = (kbName: string) => {
    // 保存成功后跳到对应 doc KB 详情。kuId 形如 'doc:<kbName>'
    try {
      window.location.hash = `#/kb/${encodeURIComponent('doc:' + kbName)}`;
    } catch {
      /* ignore */
    }
  };
  return (
    <div className="h-full">
      <NoteEditor onSaved={onSaved} draftKey={draftKey} />
    </div>
  );
};
