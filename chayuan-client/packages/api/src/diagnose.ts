/**
 * 本地 runtime 诊断 API 客户端。
 *
 * 对齐 chayuan-server GET /runtime/diagnose:跑 10 项 check 返
 * DiagnoseReport,前端做 markdown 渲染 (在 DiagnoseModal)。
 */

import { request } from './client';

export type DiagnoseSeverity = 'ok' | 'warn' | 'fail';

export interface DiagnoseCheck {
  name: string;
  ok: boolean;
  severity: DiagnoseSeverity;
  detail: string;
  context?: Record<string, unknown> | null;
}

export interface DiagnoseReport {
  timestamp: string;
  platform: string;
  python_version: string;
  chayuan_root: string;
  chayuan_server_version: string;
  checks: DiagnoseCheck[];
  summary: { ok: number; warn: number; fail: number };
}

async function run(): Promise<DiagnoseReport> {
  return (await request<DiagnoseReport>('/runtime/diagnose', { timeoutMs: 15_000 })).data;
}

export const diagnose = { run };
