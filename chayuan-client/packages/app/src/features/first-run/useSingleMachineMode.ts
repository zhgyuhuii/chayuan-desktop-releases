/**
 * 单机模式判定(Phase 7)。
 *
 * 「单机模式」= **桌面端 + sidecar 能力存在 + 非瘦客户端**;此时:
 *   - 后端 ``apply_single_machine`` 已把 AUTH_REQUIRED 关掉
 *   - LocalUser 自动注入,所有路由游客可达
 *   - 应该隐藏登录入口(CLAUDE.md §3.4)、隐藏注册、不弹 LoginModal
 *
 * Web / 瘦客户端 / Tauri dev 走老路径(可登录到远端)。
 */

import { getPlatform } from '@chayuan/platform-shared';
import { useThinClientStore } from '../../store/thinClient';

export function isSingleMachineMode(): boolean {
  // 瘦客户端是另一个 desktop 形态:登录到远端,不是单机
  if (useThinClientStore.getState().enabled) return false;
  try {
    const p = getPlatform();
    return p.kind === 'desktop' && !!p.sidecar;
  } catch {
    return false;
  }
}

/** Hook 形态供 React 组件用;依赖 thinClient store 的 ``enabled`` 字段保持响应。 */
export function useSingleMachineMode(): boolean {
  const thinClient = useThinClientStore((s) => s.enabled);
  if (thinClient) return false;
  try {
    const p = getPlatform();
    return p.kind === 'desktop' && !!p.sidecar;
  } catch {
    return false;
  }
}
