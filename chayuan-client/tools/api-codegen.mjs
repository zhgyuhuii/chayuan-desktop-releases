#!/usr/bin/env node
/**
 * 从运行中的 FastAPI（本仓 `server/` 或任意 chayuan-server）拉 /openapi.json，用 openapi-typescript 生成
 * packages/api/generated/types.ts。
 *
 * 用法：
 *   API_BASE=http://127.0.0.1:62581 node tools/api-codegen.mjs           # 写盘
 *   API_BASE=http://127.0.0.1:62581 node tools/api-codegen.mjs --check   # CI: 只 diff 不写
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const API_BASE = process.env.API_BASE || 'http://127.0.0.1:62581';
const OUT = resolve(ROOT, 'packages/api/generated/types.ts');
const CHECK_MODE = process.argv.includes('--check');

async function main() {
  const url = `${API_BASE.replace(/\/+$/, '')}/openapi.json`;
  console.log(`[gen:api] fetching ${url}`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  const spec = await res.json();

  const { default: openapiTS, astToString } = await import('openapi-typescript');
  const ast = await openapiTS(spec, {
    arrayLength: true,
    enum: true,
    transform(schemaObject) {
      if (
        typeof schemaObject === 'object' &&
        schemaObject !== null &&
        'format' in schemaObject &&
        schemaObject.format === 'binary'
      ) {
        return 'Blob';
      }
      return undefined;
    },
  });
  const code = astToString(ast);
  const next = `/* eslint-disable */\n/* AUTO-GENERATED — 不要手改 */\n\n${code}`;

  if (CHECK_MODE) {
    if (!existsSync(OUT)) {
      console.error('[gen:api:check] generated/types.ts 不存在；先跑 pnpm gen:api');
      process.exit(2);
    }
    const cur = await readFile(OUT, 'utf8');
    if (cur !== next) {
      console.error('[gen:api:check] generated types 与后端 OpenAPI 不一致。请跑 pnpm gen:api 并提交结果。');
      process.exit(3);
    }
    console.log('[gen:api:check] up-to-date ✓');
    return;
  }

  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, next, 'utf8');
  console.log(`[gen:api] wrote ${OUT}`);
}

main().catch((err) => {
  console.error('[gen:api] failed', err);
  process.exit(1);
});
