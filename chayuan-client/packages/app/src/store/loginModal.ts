/**
 * 登录弹框开关 —— 跨组件共享。
 *
 * 任何"需要登录但用户未登录"的入口(顶栏 登录 按钮 / 用户菜单 / CommandPalette /
 * /login URL 兜底)都通过 useLoginModalStore.open() 唤起,登录页不再是独立全屏页面。
 *
 * 强制模式 `locked=true`:
 *   - Shell 启动拉 /auth/config,auth_required=true 时未登录就把弹框 show + lock
 *   - LoginModal 在 lock 下:隐藏关闭 X、隐藏「以游客身份继续」、onOpenChange 拒绝关闭
 *   - 登录成功后由 LoginModal 自己 hide() — 此时 isLoggedIn 切 true,后续 lock 不再生效
 */
import { create } from 'zustand';

interface LoginModalState {
  open: boolean;
  /** 强制登录:不允许关闭弹框,不展示游客入口 */
  locked: boolean;
  /** 后端是否要求登录后才能进入系统；null 表示尚未探测完成 */
  authRequired: boolean | null;
  /** 后端 auth_allow_registration 镜像;为 false 时 LoginModal 隐藏注册入口 */
  allowRegistration: boolean;
  show(): void;
  hide(): void;
  setLocked(locked: boolean): void;
  setAuthRequired(required: boolean | null): void;
  setAllowRegistration(allow: boolean): void;
  /** 同时开 + 锁;Shell 在检测到 auth_required && !isLoggedIn 时调 */
  lock(): void;
}

export const useLoginModalStore = create<LoginModalState>((set) => ({
  open: false,
  locked: false,
  authRequired: null,
  allowRegistration: true,
  show: () => set({ open: true }),
  hide: () => set({ open: false }),
  setLocked: (locked) => set({ locked }),
  setAuthRequired: (authRequired) => set({ authRequired }),
  setAllowRegistration: (allow) => set({ allowRegistration: allow }),
  lock: () => set({ open: true, locked: true }),
}));
