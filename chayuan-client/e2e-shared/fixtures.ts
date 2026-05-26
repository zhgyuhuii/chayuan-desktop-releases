/**
 * 共享测试夹具：被 web e2e（playwright）和桌面 e2e（mock-server）同时使用。
 * 任何用例数据/SSE 流构造都应集中在这里，避免漂移。
 */

export const TEST_USER = {
  id: 1,
  username: 'tester',
  email: 'tester@chayuan.test',
  role: 'admin' as const,
  active: true,
};

export const PASSWORD = 'test1234';
export const ACCESS = 'access-token-xxx';
export const REFRESH = 'refresh-token-xxx';

export function tokenPair() {
  return {
    access_token: ACCESS,
    refresh_token: REFRESH,
    token_type: 'bearer',
    user: TEST_USER,
  };
}

export function buildSimpleStream(content = 'Hello from mock'): string {
  return (
    `data: ${JSON.stringify({ id: 'm1', choices: [{ delta: { content } }] })}\n\n` +
    `data: [DONE]\n\n`
  );
}

export function buildHilStream(): string {
  return (
    `event: tool_call_start\ndata: ${JSON.stringify({ id: 't1', name: 'web_search' })}\n\n` +
    `event: interrupt\ndata: ${JSON.stringify({ reason: '需要批准 web_search' })}\n\n`
  );
}

export const TOOLS_LIST = {
  code: 0,
  data: {
    web_search: { name: 'web_search', title: '联网搜索', description: '' },
  },
};
