/**
 * 后端 mock 工具：在每个 spec 里 install(page) 一次，
 * 即可拦截 chayuan-server 与 Langfuse 的 HTTP 流量并返回可控响应。
 *
 * 路由列表与真实后端 prefix 一致；改后端时同步改这里。
 */

import type { Page, Route } from '@playwright/test';
// 复用桌面 e2e 共享夹具，避免漂移
import {
  TEST_USER as DEFAULT_USER,
  ACCESS as ACCESS_TOKEN,
  REFRESH as REFRESH_TOKEN,
  buildSimpleStream,
  buildHilStream,
  TOOLS_LIST,
  tokenPair,
} from '../../e2e-shared/fixtures';

export { DEFAULT_USER, buildSimpleStream, buildHilStream };
export const ACCESS = ACCESS_TOKEN;
export const REFRESH = REFRESH_TOKEN;

export interface MockState {
  loginSucceedsFor?: { username: string; password: string };
  /** 一次 chat stream 的完整 SSE body；可被某个 spec 临时覆盖 */
  chatStreamBody?: string;
  /** 当 fail=true 时 /chat/v2/chat 直接返回 503 */
  chatFail?: boolean;
  /** /chat/v2/chat/resume 之后下一次发流的 body 覆盖 */
  resumeStreamBody?: string;
  capturedFeedback: Array<Record<string, unknown>>;
  capturedResume: Array<Record<string, unknown>>;
}

export async function installMockBackend(page: Page, opts: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = {
    loginSucceedsFor: { username: 'tester', password: 'test1234' },
    chatStreamBody: buildSimpleStream(),
    capturedFeedback: [],
    capturedResume: [],
    ...opts,
  };

  // 登录
  await page.route('**/auth/login', async (route) => {
    const data = postJson(route);
    if (data.username === state.loginSucceedsFor?.username && data.password === state.loginSucceedsFor?.password) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(tokenPair()),
      });
    } else {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'invalid credentials' }) });
    }
  });

  // /auth/config：Shell 启动 + ServerLoginModal "探活" 都依赖这条
  await page.route('**/auth/config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth_required: true,
        auth_allow_registration: true,
      }),
    });
  });

  // /auth/me：只要带 Bearer 即返回 user
  await page.route('**/auth/me', async (route) => {
    const auth = route.request().headers()['authorization'] || '';
    if (auth.startsWith('Bearer ')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DEFAULT_USER) });
    } else {
      await route.fulfill({ status: 401, body: 'no auth' });
    }
  });

  // /v1/models
  await page.route('**/v1/models', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [{ id: 'claude-opus-4-7', object: 'model', platform_name: 'anthropic' }] }),
    });
  });

  // catalog
  await page.route('**/tools?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(TOOLS_LIST),
    });
  });
  await page.route('**/api/v1/mcp_connections/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ connections: [], total: 0 }) });
  });
  await page.route('**/knowledge_base/list_knowledge_bases', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: [] }) });
  });
  await page.route('**/chat/conversations**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });

  // SSE：chat
  await page.route('**/chat/v2/chat', async (route) => {
    if (state.chatFail) {
      await route.fulfill({ status: 503, body: 'unavailable' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
      body: state.chatStreamBody!,
    });
  });

  // /chat/v2/chat/resume
  await page.route('**/chat/v2/chat/resume', async (route) => {
    state.capturedResume.push(postJson(route));
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    // 第二轮：让 chat endpoint 返回 resumeStreamBody
    if (state.resumeStreamBody) state.chatStreamBody = state.resumeStreamBody;
    else state.chatStreamBody = buildSimpleStream('已批准，继续执行。');
  });

  // /chat/feedback
  await page.route('**/chat/feedback', async (route) => {
    state.capturedFeedback.push(postJson(route));
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"code":0}' });
  });

  // langfuse ingest（避免出错；也方便用例校验上传发生）
  await page.route('**/api/public/ingestion**', async (route) => {
    await route.fulfill({ status: 207, contentType: 'application/json', body: '{"successes":[],"errors":[]}' });
  });

  return state;
}

function postJson(route: Route): Record<string, unknown> {
  try {
    return JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
  } catch {
    return {};
  }
}
