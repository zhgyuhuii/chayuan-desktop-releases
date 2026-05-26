/**
 * Tab 拖出独立窗口 —— 桌面 / Web 通用入口。
 *
 * 设计:
 *   - 桌面(Tauri):走 platform.preview.openWindow(WebviewWindow);label = `tab-<tabId>`,
 *     URL = `<path>?detached=1`。同源,共享 cookie / localStorage / Stronghold。
 *   - Web:同样走 platform.preview.openWindow,内部用 window.open 弹窗;同 label
 *     复用同一个弹窗。
 *   - 失败(浏览器拦截弹窗 / Tauri webview 创建失败):返回 false,调用方应给 toast
 *     "弹窗被拦截 / 创建失败,请允许后再试"。
 *
 * 跨窗状态同步:
 *   - useCrossWindowSync 在主窗 + 独立窗都挂,基于 BroadcastChannel('chayuan');
 *   - 各 mutation 在 onSuccess 里 channel.postMessage({type, payload}),其他窗收
 *     到后 invalidateQueries 关键 key,无须显式刷新。
 *
 * 记忆尺寸:
 *   - localStorage 'cy.detached.size' = { w, h };上次关窗时由独立窗写入,下次开窗读。
 *   - 没记录时默认 1280×860(参考图选定)。
 */
import { getPlatform } from '@chayuan/platform-shared';
import type { PreviewWindowHandle } from '@chayuan/platform-shared';

const SIZE_KEY = 'cy.detached.size';
const DEFAULT_W = 1280;
const DEFAULT_H = 860;

interface DetachedSize {
  w: number;
  h: number;
}

function readSize(): DetachedSize {
  try {
    const raw = localStorage.getItem(SIZE_KEY);
    if (!raw) return { w: DEFAULT_W, h: DEFAULT_H };
    const v = JSON.parse(raw) as DetachedSize;
    if (typeof v.w === 'number' && typeof v.h === 'number' && v.w > 320 && v.h > 240) {
      return v;
    }
  } catch {
    /* ignore */
  }
  return { w: DEFAULT_W, h: DEFAULT_H };
}

export function writeDetachedSize(w: number, h: number): void {
  try {
    localStorage.setItem(SIZE_KEY, JSON.stringify({ w, h }));
  } catch {
    /* ignore */
  }
}

export interface OpenDetachedSpec {
  /** Tab id;独立窗 label = 'tab-' + tabId */
  tabId: string;
  /** 页面路径,会自动追加 ?detached=1 */
  path: string;
  /** OS 窗口标题 */
  title?: string;
}

/**
 * 把 Tab 拖出为独立窗口。
 *
 * 返回 handle 给调用方挂 onClosed 回调(独立窗关时主窗回收 Tab)。
 * 不可用时返回 null(平台无 preview 能力 — 罕见,几乎所有平台都该有)。
 */
export async function openDetachedTab(
  spec: OpenDetachedSpec,
): Promise<PreviewWindowHandle | null> {
  const platform = getPlatform();
  if (!platform.preview) return null;
  const { w, h } = readSize();
  const sep = spec.path.includes('?') ? '&' : '?';
  const url = `${spec.path}${sep}detached=1&label=${encodeURIComponent('tab-' + spec.tabId)}`;
  return platform.preview.openWindow({
    label: `tab-${spec.tabId}`,
    path: url,
    title: spec.title ?? 'Chayuan',
    width: w,
    height: h,
  });
}

export function isDetachedWindow(): boolean {
  if (typeof window === 'undefined') return false;
  const sp = new URLSearchParams(window.location.search);
  return sp.get('detached') === '1';
}
