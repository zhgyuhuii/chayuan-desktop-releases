import { beforeEach, describe, expect, it, vi, afterEach } from 'vitest';
import { useLocalRuntimeStore } from '../localRuntime';

// 拦截 @chayuan/api 的 localRuntime,所有调用走 mock
vi.mock('@chayuan/api', () => ({
  localRuntime: {
    getStatus: vi.fn(),
    getConfig: vi.fn(),
    getInstallInfo: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    restart: vi.fn(),
    setConfig: vi.fn(),
    // Plan 3B
    getStatusFor: vi.fn(),
    startFor: vi.fn(),
    stopFor: vi.fn(),
    restartFor: vi.fn(),
    getRegistry: vi.fn(),
  },
}));

import { localRuntime as api } from '@chayuan/api';

beforeEach(() => {
  useLocalRuntimeStore.setState({
    status: null,
    config: null,
    installInfo: null,
    pending: null,
    lastError: null,
    reachable: true,
    statuses: { chat: null, embedding: null, rerank: null, asr: null, 'image-embedding': null },
    pendingFor: { chat: null, embedding: null, rerank: null, asr: null, 'image-embedding': null },
  });
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useLocalRuntimeStore', () => {
  it('refreshStatus 成功时 status 被缓存,reachable=true', async () => {
    (api.getStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      state: 'ready',
      endpoint: 'http://127.0.0.1:62582',
      pid: 99,
    });
    await useLocalRuntimeStore.getState().refreshStatus();
    expect(useLocalRuntimeStore.getState().status?.state).toBe('ready');
    expect(useLocalRuntimeStore.getState().reachable).toBe(true);
  });

  it('refreshStatus 失败时 reachable=false 但旧 status 保留', async () => {
    useLocalRuntimeStore.setState({
      status: { state: 'ready', endpoint: 'http://old', pid: 1 },
    });
    (api.getStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));
    await useLocalRuntimeStore.getState().refreshStatus();
    expect(useLocalRuntimeStore.getState().reachable).toBe(false);
    expect(useLocalRuntimeStore.getState().lastError).toContain('network');
    // 不清空旧状态,UI 仍展示
    expect(useLocalRuntimeStore.getState().status?.state).toBe('ready');
  });

  it('start 期间 pending=start,完成后归零并写入新 status', async () => {
    let resolveStart: (s: { state: string }) => void;
    (api.start as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolveStart = r;
      }),
    );
    const p = useLocalRuntimeStore.getState().start();
    expect(useLocalRuntimeStore.getState().pending).toBe('start');
    resolveStart!({ state: 'ready' });
    await p;
    expect(useLocalRuntimeStore.getState().pending).toBeNull();
    expect(useLocalRuntimeStore.getState().status?.state).toBe('ready');
  });

  it('pending 时再触发 start 立即返回,不会调第二次 api', async () => {
    let resolveStart: (s: { state: string }) => void = () => undefined;
    (api.start as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolveStart = r;
      }),
    );
    const p1 = useLocalRuntimeStore.getState().start();
    await useLocalRuntimeStore.getState().start(); // 立即 return,不发请求
    expect((api.start as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
    resolveStart({ state: 'ready' });
    await p1;
  });

  it('saveConfig 成功后 config 被刷新', async () => {
    (api.setConfig as ReturnType<typeof vi.fn>).mockResolvedValue({
      preload_on_startup: true,
      host: '127.0.0.1',
      port: 62590,
      api_key: '',
      expose_lan: true,
      default_chat_model: '',
    });
    await useLocalRuntimeStore.getState().saveConfig({ port: 62590, expose_lan: true });
    expect(useLocalRuntimeStore.getState().config?.port).toBe(62590);
    expect(useLocalRuntimeStore.getState().config?.expose_lan).toBe(true);
  });

  it('clearError 清掉 lastError', async () => {
    useLocalRuntimeStore.setState({ lastError: 'foo' });
    useLocalRuntimeStore.getState().clearError();
    expect(useLocalRuntimeStore.getState().lastError).toBeNull();
  });

  it('refreshRegistry 一次更新 3 个 capability 的 statuses', async () => {
    (api.getRegistry as ReturnType<typeof vi.fn>).mockResolvedValue({
      chat: { state: 'ready', endpoint: 'http://127.0.0.1:62582', pid: 1 },
      embedding: { state: 'stopped' },
      rerank: { state: 'failed', last_error: 'no model' },
    });
    await useLocalRuntimeStore.getState().refreshRegistry();
    const s = useLocalRuntimeStore.getState();
    expect(s.statuses.chat?.state).toBe('ready');
    expect(s.statuses.embedding?.state).toBe('stopped');
    expect(s.statuses.rerank?.state).toBe('failed');
  });

  it('startCapability(embedding) 只置 pendingFor.embedding,不影响 chat', async () => {
    let resolveStart: (s: { state: string }) => void = () => undefined;
    (api.startFor as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolveStart = r;
      }),
    );
    const p = useLocalRuntimeStore.getState().startCapability('embedding');
    expect(useLocalRuntimeStore.getState().pendingFor.embedding).toBe('start');
    expect(useLocalRuntimeStore.getState().pendingFor.chat).toBeNull();
    resolveStart({ state: 'ready' });
    await p;
    expect(useLocalRuntimeStore.getState().pendingFor.embedding).toBeNull();
    expect(useLocalRuntimeStore.getState().statuses.embedding?.state).toBe('ready');
  });

  it('stopCapability(rerank) 写 statuses.rerank 为 stopped', async () => {
    useLocalRuntimeStore.setState({
      statuses: {
        chat: { state: 'ready' },
        embedding: null,
        rerank: { state: 'ready', endpoint: 'http://127.0.0.1:62584', pid: 99 },
      } as never,
    });
    (api.stopFor as ReturnType<typeof vi.fn>).mockResolvedValue({ state: 'stopped' });
    await useLocalRuntimeStore.getState().stopCapability('rerank');
    expect(useLocalRuntimeStore.getState().statuses.rerank?.state).toBe('stopped');
  });

  it('startCapability("chat", { model_id }) 透传给 localRuntime.startFor', async () => {
    (api.startFor as ReturnType<typeof vi.fn>).mockResolvedValue({
      state: 'ready',
      endpoint: 'http://127.0.0.1:62582',
      pid: 1234,
      model_id: 'qwen3-4b',
    });
    await useLocalRuntimeStore.getState().startCapability('chat', { model_id: 'qwen3-4b' });
    expect((api.startFor as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('chat', {
      model_id: 'qwen3-4b',
    });
  });

  it('startCapability 不带 opts 时 startFor 收到 undefined', async () => {
    (api.startFor as ReturnType<typeof vi.fn>).mockResolvedValue({
      state: 'ready',
      endpoint: 'http://127.0.0.1:62582',
      pid: 1234,
    });
    await useLocalRuntimeStore.getState().startCapability('embedding');
    expect((api.startFor as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('embedding', undefined);
  });

  it('restartCapability("rerank", { model_id }) 透传给 localRuntime.restartFor', async () => {
    (api.restartFor as ReturnType<typeof vi.fn>).mockResolvedValue({
      state: 'ready',
      endpoint: 'http://127.0.0.1:62584',
      pid: 5678,
      model_id: 'bge-rerank-q8',
    });
    await useLocalRuntimeStore.getState().restartCapability('rerank', { model_id: 'bge-rerank-q8' });
    expect((api.restartFor as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('rerank', {
      model_id: 'bge-rerank-q8',
    });
  });
});
