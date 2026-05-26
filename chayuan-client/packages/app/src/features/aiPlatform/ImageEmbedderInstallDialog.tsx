/**
 * ImageEmbedderInstallDialog —— 图像向量模型自动安装引导。
 *
 * 触发:
 *   - 后端 ``POST /knowledge_source/{id}/image/upload`` 返回 422 +
 *     ``code=image_embedder_missing`` 时,``useKbUpload`` 会派发
 *     ``window.event('chayuan:image-embedder-missing', detail)``。
 *   - 本组件挂在 App 根部,监听该事件并弹对话框。
 *
 * 行为:
 *   - "立即下载并启用":调用 ``POST /model_registry/lifecycle/start`` 启动
 *     生命周期任务,SSE 订阅 ``/model_registry/lifecycle/{id}/stream`` 流式
 *     更新进度;完成后提示用户重试上传。
 *   - "稍后处理":关闭对话框,用户去 设置面板 → 镜像源 / 系统自检 自己处理。
 */

import * as React from 'react';
import { Dialog, DialogContent, DialogTitle, GuidanceCard } from '@chayuan/ui';

interface MissingDetail {
  suggestedModel?: string;
  suggestedCapability?: string;
  suggestedRuntime?: string;
}

type LifecycleEvent = {
  task_id: string;
  stage: 'resolve' | 'connect' | 'download' | 'verify' | 'wire' | 'ready' | 'error';
  progress: number;
  overall: number;
  detail: string;
};

const STAGE_LABELS: Record<LifecycleEvent['stage'], string> = {
  resolve: '解析',
  connect: '连接镜像源',
  download: '下载',
  verify: '校验',
  wire: '注册到运行时',
  ready: '就绪',
  error: '失败',
};

export const ImageEmbedderInstallDialog: React.FC = () => {
  const [open, setOpen] = React.useState(false);
  const [detail, setDetail] = React.useState<MissingDetail>({});
  const [running, setRunning] = React.useState(false);
  const [event, setEvent] = React.useState<LifecycleEvent | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);

  React.useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<MissingDetail>;
      setDetail(ce.detail || {});
      setError(null);
      setDone(false);
      setEvent(null);
      setOpen(true);
    };
    window.addEventListener('chayuan:image-embedder-missing', handler);
    return () => window.removeEventListener('chayuan:image-embedder-missing', handler);
  }, []);

  const startInstall = async () => {
    setRunning(true);
    setError(null);
    setEvent(null);
    setDone(false);
    try {
      const startResp = await fetch('/model_registry/lifecycle/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: detail.suggestedModel ?? 'google/siglip2-base-patch16-224',
          capability: detail.suggestedCapability ?? 'image-embedding',
          target_runtime: detail.suggestedRuntime ?? 'hf-cache',
        }),
      });
      if (!startResp.ok) {
        throw new Error(`HTTP ${startResp.status}`);
      }
      const startBody = (await startResp.json()) as { code: number; data?: { task_id: string } };
      const taskId = startBody.data?.task_id;
      if (!taskId) {
        throw new Error('后端未返回 task_id');
      }
      // SSE 订阅
      const stream = await fetch(`/model_registry/lifecycle/${taskId}/stream`);
      if (!stream.ok || !stream.body) {
        throw new Error(`SSE HTTP ${stream.status}`);
      }
      const reader = stream.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split('\n\n');
        buf = frames.pop() ?? '';
        for (const frame of frames) {
          for (const ln of frame.split('\n')) {
            if (!ln.startsWith('data:')) continue;
            const payload = ln.slice(5).trim();
            try {
              const ev = JSON.parse(payload) as LifecycleEvent;
              setEvent(ev);
              if (ev.stage === 'error') {
                setError(ev.detail);
                return;
              }
              if (ev.stage === 'ready') {
                setDone(true);
                return;
              }
            } catch {
              // ignore non-JSON keepalive lines
            }
          }
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const close = () => {
    if (running) return; // 安装中不允许关
    setOpen(false);
  };

  const repo = detail.suggestedModel ?? 'google/siglip2-base-patch16-224';

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? setOpen(true) : close())}>
      <DialogContent className="max-w-lg">
        <DialogTitle>需要图像向量模型才能继续</DialogTitle>
        <div className="space-y-3">
          <GuidanceCard
            tone="info"
            what="图像向量模型未就绪"
            why={
              <>
                上传图像需要 <code className="rounded bg-zinc-100 px-1 py-0.5 text-[11px] dark:bg-zinc-800">{repo}</code>
                 模型把图像编码成向量。我们可以为你一键自动下载并联动到运行时。
              </>
            }
            how={[
              {
                platform: 'all',
                steps: [
                  { text: '点击"立即安装"启动下载(走当前镜像源,预计 2-10 分钟,取决于网络)。' },
                  { text: '安装完成后会自动注册到 hf-cache,后续上传图像即可入索引,无需重启。' },
                  { text: '若网络慢,可去 设置 → AI 平台 → 镜像源 测速并切换。' },
                ],
              },
            ]}
          />

          {event && (
            <div className="rounded-md border border-zinc-200 p-3 text-xs dark:border-zinc-700">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-medium">{STAGE_LABELS[event.stage]}</span>
                <span className="text-zinc-500">{Math.round(event.overall)}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${Math.min(100, Math.max(0, event.overall))}%` }}
                />
              </div>
              {event.detail && (
                <div className="mt-2 truncate text-zinc-500">{event.detail}</div>
              )}
            </div>
          )}

          {error && (
            <div className="text-xs text-amber-600">安装失败:{error}</div>
          )}

          {done && (
            <div className="text-xs text-emerald-600">
              ✓ 安装完成。请重新尝试上传图像。
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={close}
              disabled={running}
              className="rounded border border-zinc-300 px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {done ? '完成' : '稍后处理'}
            </button>
            {!done && (
              <button
                type="button"
                onClick={() => void startInstall()}
                disabled={running}
                className="rounded bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {running ? '安装中…' : error ? '重试' : '立即安装'}
              </button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
