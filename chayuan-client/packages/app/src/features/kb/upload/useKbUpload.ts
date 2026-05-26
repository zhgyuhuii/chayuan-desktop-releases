/**
 * KB 上传 hook(并发调度 + 进度桥接 + 失效查询)。
 *
 * 调用:
 *   const { submit } = useKbUpload();
 *   await submit({ kuId, kind, kbName, sourceId, files });
 *
 * 行为:
 *   - 立即把 files 入队列(zustand),UI 即时显进度条
 *   - FIFO 调度,单 KB 默认并发 3
 *   - 上传走 uploadWithProgress(XHR onprogress)
 *   - 完成后失效 ku.list / ku.detail,卡片文件数刷新
 *   - 单文件失败不连坐;UI 显错并提供重试入口(由 KbProgressBar 触发 retryJob)
 */

import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { uploadWithProgress } from '@chayuan/api';
import { uuid } from '@chayuan/platform-shared';
import { confirmDuplicateUpload } from './duplicateCheck';
import { useKbUploadStore } from './kbUploadStore';

export type KbUploadKind = 'document' | 'image';

export interface KbUploadSubmitParams {
  kuId: string;
  kind: KbUploadKind;
  /** 文档型:kb_name;图像型:source_id */
  kbName?: string;
  sourceId?: number;
  files: File[];
  /** 单 KB 并发数;默认 3 */
  concurrency?: number;
  /** 文档型:override / chunk_size 等额外字段 */
  extra?: Record<string, string>;
}

const DEFAULT_CONCURRENCY = 3;

/** 单个 job 的上传逻辑(被调度器调起) */
async function runJob(
  job: { jobId: string; kuId: string; file: File; kind: KbUploadKind; kbName?: string; sourceId?: number; extra?: Record<string, string> },
  store: typeof useKbUploadStore,
  qc: ReturnType<typeof useQueryClient>,
  abort: AbortController,
): Promise<void> {
  const { jobId, kind, file, kbName, sourceId, extra } = job;
  const s = store.getState();
  s.start(jobId);

  const form = new FormData();
  // 文件夹上传场景:File 上挂了 webkitRelativePath,此时 multipart filename
  // 应当用相对路径(如 'folder/sub/a.pdf'),后端 KnowledgeFile 会按 Path 落到
  // KB/content/folder/sub/a.pdf。否则就用 file.name。
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  const partName = rel && rel.length ? rel : file.name;
  let path: string;
  if (kind === 'document') {
    if (!kbName) throw new Error('文档上传缺 kb_name');
    form.append('files', file, partName);
    form.append('knowledge_base_name', kbName);
    form.append('override', extra?.override ?? 'false');
    form.append('to_vector_store', extra?.to_vector_store ?? 'true');
    if (extra?.chunk_size) form.append('chunk_size', extra.chunk_size);
    if (extra?.chunk_overlap) form.append('chunk_overlap', extra.chunk_overlap);
    path = '/knowledge_base/upload_docs';
  } else {
    if (sourceId == null) throw new Error('图像上传缺 source_id');
    form.append('files', file, partName);
    if (extra?.tags) form.append('tags', extra.tags);
    path = `/knowledge_source/${sourceId}/image/upload`;
  }

  try {
    await uploadWithProgress(path, form, {
      signal: abort.signal,
      onProgress: (e) => {
        store.getState().progress(jobId, e.loaded, e.total);
      },
    });
    store.getState().ok(jobId);
    // 失效列表 / 详情让卡片数 + 文件 / 图像列表刷新
    void qc.invalidateQueries({ queryKey: ['ku.list'] });
    void qc.invalidateQueries({ queryKey: ['ku.detail', job.kuId] });
  } catch (e) {
    const err = e as Error & {
      name?: string;
      status?: number;
      payload?: { detail?: { code?: string; suggested_model?: string; suggested_capability?: string; suggested_runtime?: string } };
    };
    if (err?.name === 'AbortError') {
      // cancel 已由 store.cancel 处理
      return;
    }
    // 后端识别到"图像 embedder 缺失"时返回 422 + code=image_embedder_missing
    // 这里转成全局事件,让 ImageEmbedderInstallDialog 接管引导用户安装
    if (
      err?.status === 422 &&
      err?.payload?.detail?.code === 'image_embedder_missing' &&
      typeof window !== 'undefined'
    ) {
      const detail = err.payload.detail;
      window.dispatchEvent(
        new CustomEvent('chayuan:image-embedder-missing', {
          detail: {
            suggestedModel: detail.suggested_model,
            suggestedCapability: detail.suggested_capability,
            suggestedRuntime: detail.suggested_runtime,
          },
        }),
      );
    }
    store.getState().error(jobId, err?.message || String(e));
  }
}

export function useKbUpload() {
  const qc = useQueryClient();
  // 用 ref 持调度器队列,避免组件重渲打断
  const pendingRef = React.useRef<Map<string, Array<{ jobId: string; run: () => Promise<void> }>>>(new Map());
  const inflightRef = React.useRef<Map<string, number>>(new Map());

  const tick = React.useCallback((kuId: string, concurrency: number) => {
    const queue = pendingRef.current.get(kuId);
    if (!queue || queue.length === 0) return;
    const inflight = inflightRef.current.get(kuId) ?? 0;
    while ((inflightRef.current.get(kuId) ?? 0) < concurrency && queue.length > 0) {
      const next = queue.shift()!;
      inflightRef.current.set(kuId, (inflightRef.current.get(kuId) ?? 0) + 1);
      void next.run().finally(() => {
        inflightRef.current.set(kuId, Math.max(0, (inflightRef.current.get(kuId) ?? 1) - 1));
        // 自动派遣下一个
        tick(kuId, concurrency);
      });
    }
    void inflight;
  }, []);

  const submit = React.useCallback(
    async (params: KbUploadSubmitParams) => {
      const { kuId, kind, kbName, sourceId, files, concurrency = DEFAULT_CONCURRENCY, extra } = params;
      if (files.length === 0) return;
      if (kind === 'document' && kbName) {
        const duplicateOk = await confirmDuplicateUpload(kbName, files);
        if (!duplicateOk) return;
      }

      const enqueueArr = files.map((file) => {
        const jobId = uuid();
        const abort = new AbortController();
        return { jobId, kuId, fileName: file.name, fileSize: file.size, abort, file };
      });

      // 入 store(状态 = queued)
      useKbUploadStore.getState().enqueue(
        enqueueArr.map(({ jobId, kuId, fileName, fileSize, abort }) => ({
          jobId, kuId, fileName, fileSize, abort,
        })),
      );

      // 入本地调度队列
      const q = pendingRef.current.get(kuId) ?? [];
      for (const item of enqueueArr) {
        q.push({
          jobId: item.jobId,
          run: () =>
            runJob(
              { jobId: item.jobId, kuId, file: item.file, kind, kbName, sourceId, extra },
              useKbUploadStore,
              qc,
              item.abort,
            ),
        });
      }
      pendingRef.current.set(kuId, q);
      tick(kuId, concurrency);
    },
    [qc, tick],
  );

  const retryJob = React.useCallback(
    (jobId: string, kind: KbUploadKind, kbName?: string, sourceId?: number, file?: File) => {
      const job = useKbUploadStore.getState().jobs[jobId];
      if (!job || !file) return;
      // 清掉旧错误状态
      useKbUploadStore.getState().enqueue([{
        jobId: job.jobId,
        kuId: job.kuId,
        fileName: job.fileName,
        fileSize: job.fileSize,
        abort: new AbortController(),
      }]);
      const q = pendingRef.current.get(job.kuId) ?? [];
      q.push({
        jobId: job.jobId,
        run: () =>
          runJob(
            { jobId: job.jobId, kuId: job.kuId, file, kind, kbName, sourceId },
            useKbUploadStore,
            qc,
            new AbortController(),
          ),
      });
      pendingRef.current.set(job.kuId, q);
      tick(job.kuId, DEFAULT_CONCURRENCY);
    },
    [qc, tick],
  );

  return { submit, retryJob };
}
