/**
 * 瘦客户端模式 store —— 控制 :class:`ServerLoginModal` 的显隐。
 *
 * 工作流：
 *
 * 1. ``Shell`` 在 mount 时根据 ``thinClient`` env 标志判断是不是瘦壳模式；
 *    瘦壳模式下，如果还没有有效 ``apiBaseOverride`` + 已登录 token，调
 *    ``requireServerLoginGate()`` 把 modal 锁开。
 * 2. ``ServerLoginModal`` 提交成功后调 ``finishServerLogin()`` 收掉 modal。
 * 3. 用户登出时（auth store ``logout``）会通过 hook 把 modal 重新打开（如果
 *    仍在瘦壳模式），让用户能换服务器或者重新登录。
 *
 * 这是一个 "门禁 store"，决定 Shell 渲染 ``<ServerLoginModal>`` 还是
 * ``<RouterProvider>`` 主路由。
 */
import { create } from 'zustand';

interface ThinClientState {
  /** 是否处于瘦客户端模式（由 Shell 在启动时根据 env 设定）。 */
  enabled: boolean;
  /** 是否还需要"输入服务器地址 + 登录"。``true`` 时显示 ServerLoginModal。 */
  requireServerLogin: boolean;
  setEnabled(v: boolean): void;
  requireServerLoginGate(): void;
  finishServerLogin(): void;
}

export const useThinClientStore = create<ThinClientState>((set) => ({
  enabled: false,
  requireServerLogin: false,
  setEnabled: (v) => set({ enabled: v }),
  requireServerLoginGate: () => set({ requireServerLogin: true }),
  finishServerLogin: () => set({ requireServerLogin: false }),
}));
