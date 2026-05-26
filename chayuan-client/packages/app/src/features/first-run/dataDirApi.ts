/**
 * 数据目录管理(Phase 1 首启动向导)适配层。
 *
 * 业务侧调用此模块,具体实现由 ``getPlatform().dataDir`` 决定:
 *   - Tauri 桌面端:走 ``data_dir.rs`` 5 个命令
 *   - Web 端:无能力,``getPlatform().dataDir`` 为 undefined,业务直接放行
 */

import type { DataDirApi, DataDirState } from '@chayuan/platform-shared';
import { getPlatform } from '@chayuan/platform-shared';

export type { DataDirState } from '@chayuan/platform-shared';

/** 桌面端 + 平台已注入 dataDir 能力时返回 true。 */
export function isDataDirSupported(): boolean {
  try {
    return !!getPlatform().dataDir;
  } catch {
    return false;
  }
}

/** 取当前平台的 DataDirApi 实例;Web 端会抛 ── 业务在调用前应先用 ``isDataDirSupported`` 守卫。 */
export function getDataDirApi(): DataDirApi {
  const api = getPlatform().dataDir;
  if (!api) {
    throw new Error('current platform has no dataDir capability');
  }
  return api;
}
