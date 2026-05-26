import { create } from 'zustand';
import type { UserInfo } from '@chayuan/api';
import { auth as authApi, getAccessToken, clearTokens } from '@chayuan/api';
import { setUser as setLfUser } from '@chayuan/observability';

interface AuthState {
  user: UserInfo | null;
  hydrated: boolean;
  hydrating: boolean;
  isLoggedIn: boolean;

  hydrate(): Promise<void>;
  login(username: string, password: string): Promise<UserInfo>;
  logout(): Promise<void>;
  setUser(u: UserInfo | null): void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  hydrated: false,
  hydrating: false,
  isLoggedIn: false,

  async hydrate() {
    if (get().hydrated || get().hydrating) return;
    set({ hydrating: true });
    try {
      // 安全获取 token:Stronghold 在某些环境下首次 init 会抛
      // (e.g. argon2 "illegal non-contiguous size"),不应让 hydrate 整体失败
      // —— 拿不到 token 等价于游客态。
      let token: string | null = null;
      try {
        token = await getAccessToken();
      } catch (e) {
        console.warn('[chayuan] secure store unavailable, treat as guest:', e);
      }
      if (!token) return;
      try {
        const user = await authApi.me();
        get().setUser(user);
      } catch {
        await clearTokens().catch(() => undefined);
        set({ user: null, isLoggedIn: false });
      }
    } finally {
      set({ hydrated: true, hydrating: false });
    }
  },

  async login(username, password) {
    const tokens = await authApi.login(username, password);
    get().setUser(tokens.user);
    return tokens.user;
  },

  async logout() {
    try {
      await authApi.logout();
    } finally {
      get().setUser(null);
    }
  },

  setUser(u) {
    set({ user: u, isLoggedIn: !!u });
    if (u) setLfUser(`${u.id}`);
  },
}));
