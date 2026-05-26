/**
 * WebdriverIO + tauri-driver 配置。
 *
 * 启动顺序：
 *   1. mock-server (port 7891) — 由 onPrepare 起
 *   2. tauri-driver (port 4444) — 由 services 起
 *   3. 测试脚本通过 WebDriver session 控制 Tauri 应用
 *
 * 环境：
 *   CHAYUAN_DESKTOP_BIN  Tauri 打包后的二进制路径（如 src-tauri/target/release/chayuan-desktop）
 *   CHAYUAN_MOCK_PORT    mock 端口；默认 7891；前端 VITE_API_BASE 会被指到这个 host
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { resolve } from 'node:path';
import { existsSync } from 'node:fs';

const MOCK_PORT = Number(process.env.CHAYUAN_MOCK_PORT || 7891);
const TAURI_BIN = process.env.CHAYUAN_DESKTOP_BIN || resolve(__dirname, '../apps/desktop/src-tauri/target/release/chayuan-desktop');

let mockProc: ChildProcess | null = null;

export const config: WebdriverIO.Config = {
  runner: 'local',
  specs: ['./specs/**/*.spec.ts'],
  maxInstances: 1,
  capabilities: [
    {
      browserName: 'wry',
      'tauri:options': {
        application: TAURI_BIN,
      } as Record<string, unknown>,
    } as WebdriverIO.Capabilities,
  ],
  hostname: '127.0.0.1',
  port: 4444,
  logLevel: 'info',
  framework: 'mocha',
  mochaOpts: { ui: 'bdd', timeout: 60_000 },
  reporters: ['spec'],

  onPrepare() {
    if (!existsSync(TAURI_BIN)) {
      throw new Error(`Tauri 二进制不存在：${TAURI_BIN}\n请先 pnpm --filter @chayuan/desktop build`);
    }
    mockProc = spawn('node', [resolve(__dirname, 'mock-server/server.mjs'), '--port', String(MOCK_PORT)], {
      stdio: 'inherit',
      env: { ...process.env, PORT: String(MOCK_PORT) },
    });
    return new Promise((r) => setTimeout(r, 600));
  },

  onComplete() {
    mockProc?.kill('SIGTERM');
  },

  beforeSession() {
    // tauri-driver 应已由 systemd / 手动 / CI 启动；本地开发：
    //   $ tauri-driver
    // 我们不在 wdio 里 spawn 它，避免 PATH 不一致问题
  },
};
