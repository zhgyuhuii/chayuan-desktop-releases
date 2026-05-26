/**
 * Token 存取桥（PAL.secure）+ 单飞刷新锁。
 *
 * 双栈共享：REST 客户端与 SSE transport 共用同一把 refreshPromise，
 * 保证并发请求只刷新一次。
 */

import { getPlatform } from '@chayuan/platform-shared';

const ACCESS_KEY = 'chayuan.access_token';
const REFRESH_KEY = 'chayuan.refresh_token';

let accessCache: string | null = null;
let refreshCache: string | null = null;
let inflightRefresh: Promise<string | null> | null = null;

export async function getAccessToken(): Promise<string | null> {
  if (accessCache !== null) return accessCache;
  accessCache = await getPlatform().secure.get(ACCESS_KEY);
  return accessCache;
}

export async function getRefreshToken(): Promise<string | null> {
  if (refreshCache !== null) return refreshCache;
  refreshCache = await getPlatform().secure.get(REFRESH_KEY);
  return refreshCache;
}

export async function setTokens(access: string, refresh?: string | null): Promise<void> {
  accessCache = access;
  await getPlatform().secure.set(ACCESS_KEY, access);
  if (refresh) {
    refreshCache = refresh;
    await getPlatform().secure.set(REFRESH_KEY, refresh);
  }
}

export async function clearTokens(): Promise<void> {
  accessCache = null;
  refreshCache = null;
  const p = getPlatform();
  await p.secure.del(ACCESS_KEY);
  await p.secure.del(REFRESH_KEY);
}

/**
 * 并发安全的刷新：所有调用共享同一个 inflight；成功写回 token，失败返回 null。
 *
 * @param doRefresh 发起 /auth/refresh 的函数（由 client.ts 注入，避免循环依赖）
 */
export async function refreshAccessToken(
  doRefresh: (refreshToken: string) => Promise<{ access_token: string; refresh_token?: string } | null>,
): Promise<string | null> {
  if (inflightRefresh) return inflightRefresh;
  inflightRefresh = (async () => {
    const refresh = await getRefreshToken();
    if (!refresh) return null;
    try {
      const out = await doRefresh(refresh);
      if (!out?.access_token) return null;
      await setTokens(out.access_token, out.refresh_token ?? null);
      return out.access_token;
    } catch {
      return null;
    } finally {
      inflightRefresh = null;
    }
  })();
  return inflightRefresh;
}
