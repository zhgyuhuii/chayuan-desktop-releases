/**
 * 把 PreviewFile 解析为带 token 的 URL + (按需)blob 拉取器。
 *
 * 设计:
 *   - URL 拼接是同步的,但 token 是异步获取的(getAccessToken 走 secure store)。
 *   - blob 拉取经过 LRU 缓存;同 key 第二次调零开销。
 *   - 切换文件时,外层用 React.useEffect 的 cleanup 触发 abort。
 */

import { getAccessToken, getClientConfig } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import * as React from 'react';
import { getCachedBlob, putCachedBlob } from './blobCache';
import type { PreviewFile } from './types';
import { previewFileKey } from './types';

/** 同步拼 baseURL + path(后端 query token 白名单) */
function joinBase(path: string): string {
  const base = (getClientConfig().baseURL || '').replace(/\/+$/, '');
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

/** 把 PreviewFile 转成可直接 GET 的 URL(包含 token query) */
export async function buildPreviewUrl(file: PreviewFile): Promise<string> {
  if (file.source === 'direct') {
    // 直 URL 已由调用方负责 token 化(例如 ImageKbDetail 的 absImageUrl)
    return file.url;
  }
  const tok = await getAccessToken();
  const qs = new URLSearchParams({
    knowledge_base_name: file.kbName,
    file_name: file.fileName,
  });
  if (tok) qs.set('token', tok);
  return joinBase(`/knowledge_base/preview_doc?${qs.toString()}`);
}

/** 同上,但走 download_doc(强制 attachment) */
export async function buildDownloadUrl(file: PreviewFile): Promise<string> {
  if (file.source === 'direct') return file.url;
  const tok = await getAccessToken();
  const qs = new URLSearchParams({
    knowledge_base_name: file.kbName,
    file_name: file.fileName,
  });
  if (tok) qs.set('token', tok);
  return joinBase(`/knowledge_base/download_doc?${qs.toString()}`);
}

/**
 * Hook:订阅 file 变化,异步算出 URL,同时返回一个抓取 Blob 的 callback。
 *
 * 返回:
 *   - url:string | null;null 表示尚未就绪 / 没有 file
 *   - fetchBlob:稳定函数,内部走 LRU
 */
export function useFileUrl(file: PreviewFile | null): {
  url: string | null;
  fetchBlob: (signal?: AbortSignal) => Promise<Blob>;
} {
  const [url, setUrl] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    let live = true;
    void buildPreviewUrl(file)
      .then((u) => {
        if (live) setUrl(u);
      })
      .catch(() => {
        if (live) setUrl(null);
      });
    return () => {
      live = false;
    };
  }, [file]);

  // fetchBlob 用 ref 锁住 file,避免依赖跳变重建
  const fileRef = React.useRef(file);
  fileRef.current = file;

  const fetchBlob = React.useCallback(async (signal?: AbortSignal): Promise<Blob> => {
    const cur = fileRef.current;
    if (!cur) throw new Error('no file');
    const key = previewFileKey(cur);
    const cached = getCachedBlob(key);
    if (cached) return cached;
    const u = await buildPreviewUrl(cur);
    const res = await getPlatform().net.fetch(u, { method: 'GET', signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    putCachedBlob(key, blob);
    return blob;
  }, []);

  return { url, fetchBlob };
}
