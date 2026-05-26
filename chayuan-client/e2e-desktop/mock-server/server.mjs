#!/usr/bin/env node
/**
 * 内嵌 chayuan-server 行为的 mock：纯 Node http，零依赖。
 *
 * 用途：桌面 e2e 启 tauri-driver 时让 Tauri 应用连到这里，
 * 不需要真实后端、也不需要 webview 拦截。
 *
 * 运行：node e2e-desktop/mock-server/server.mjs --port 7891
 *
 * 协议：与 chayuan-server 保持一致；任何修改请同步 e2e-shared/fixtures.ts。
 */

import { createServer } from 'node:http';
import { parse } from 'node:url';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const fixturesPath = resolve(here, '../../e2e-shared/fixtures.ts');
// 用极简方式从 fixtures.ts 抽常量；不引 ts-node 以保持零依赖
const fxSrc = readFileSync(fixturesPath, 'utf8');

const TOKEN_PAIR = {
  access_token: 'access-token-xxx',
  refresh_token: 'refresh-token-xxx',
  token_type: 'bearer',
  user: {
    id: 1,
    username: 'tester',
    email: 'tester@chayuan.test',
    role: 'admin',
    active: true,
  },
};

const SIMPLE_STREAM = (content = 'Hello from mock') =>
  `data: ${JSON.stringify({ id: 'm1', choices: [{ delta: { content } }] })}\n\n` +
  `data: [DONE]\n\n`;

let chatBody = SIMPLE_STREAM();
const captured = { feedback: [], resume: [] };

void fxSrc; // 触发 readFileSync，便于运行时校验路径正确

function send(res, status, body, headers = {}) {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PATCH,PUT,DELETE,OPTIONS',
    ...headers,
  });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
}

function sendSSE(res, body) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PATCH,PUT,DELETE,OPTIONS',
  });
  res.end(body);
}

async function readJson(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      try {
        resolve(JSON.parse(data || '{}'));
      } catch {
        resolve({});
      }
    });
  });
}

const port = Number(process.env.PORT || process.argv[process.argv.indexOf('--port') + 1] || 7891);

const server = createServer(async (req, res) => {
  if (req.method === 'OPTIONS') return send(res, 204, '');
  const { pathname } = parse(req.url || '', true);

  // ── auth ─────────────────────────────────────────────────────
  if (pathname === '/auth/login' && req.method === 'POST') {
    const body = await readJson(req);
    if (body.username === 'tester' && body.password === 'test1234') return send(res, 200, TOKEN_PAIR);
    return send(res, 401, { detail: 'invalid credentials' });
  }
  if (pathname === '/auth/me' && req.method === 'GET') {
    const auth = req.headers.authorization || '';
    if (auth.startsWith('Bearer ')) return send(res, 200, TOKEN_PAIR.user);
    return send(res, 401, { detail: 'no auth' });
  }
  if (pathname === '/auth/refresh' && req.method === 'POST') {
    return send(res, 200, TOKEN_PAIR);
  }

  // ── catalog ──────────────────────────────────────────────────
  if (pathname === '/v1/models') return send(res, 200, { data: [{ id: 'claude-opus-4-7', object: 'model', platform_name: 'anthropic' }] });
  if (pathname === '/tools') return send(res, 200, { code: 0, data: { web_search: { name: 'web_search', title: '联网搜索', description: '' } } });
  if (pathname === '/api/v1/mcp_connections/') return send(res, 200, { connections: [], total: 0 });
  if (pathname === '/knowledge_base/list_knowledge_bases') return send(res, 200, { code: 0, data: [] });
  if (pathname === '/chat/conversations') return send(res, 200, []);

  // ── chat ─────────────────────────────────────────────────────
  if (pathname === '/chat/v2/chat' && req.method === 'POST') {
    await readJson(req);
    return sendSSE(res, chatBody);
  }
  if (pathname === '/chat/v2/chat/resume' && req.method === 'POST') {
    captured.resume.push(await readJson(req));
    chatBody = SIMPLE_STREAM('已批准，继续执行。');
    return send(res, 200, {});
  }
  if (pathname === '/chat/feedback' && req.method === 'POST') {
    captured.feedback.push(await readJson(req));
    return send(res, 200, { code: 0 });
  }

  // ── admin ────────────────────────────────────────────────────
  if (pathname === '/admin/health') return send(res, 200, { code: 0, msg: 'ok', data: { reachable: true, status: 200 } });
  if (pathname === '/admin/lf/traces') return send(res, 200, { code: 0, data: { data: [], meta: { totalItems: 0, totalPages: 0, page: 1, limit: 50 } } });
  if (pathname === '/admin/prompts') return send(res, 200, { code: 0, data: { data: [], meta: { totalItems: 0, totalPages: 0, page: 1, limit: 100 } } });

  // ── 测试控制 endpoints（仅 mock 用） ─────────────────────────
  if (pathname === '/__mock__/state' && req.method === 'GET') {
    return send(res, 200, { chatBody, captured });
  }
  if (pathname === '/__mock__/state' && req.method === 'POST') {
    const body = await readJson(req);
    if (typeof body.chatBody === 'string') chatBody = body.chatBody;
    if (body.reset) {
      captured.feedback.length = 0;
      captured.resume.length = 0;
    }
    return send(res, 200, { ok: true });
  }

  send(res, 404, { detail: `not found: ${req.method} ${pathname}` });
});

server.listen(port, '127.0.0.1', () => {
  console.log(`[mock-server] listening on http://127.0.0.1:${port}`);
});

process.on('SIGTERM', () => server.close());
process.on('SIGINT', () => server.close());
