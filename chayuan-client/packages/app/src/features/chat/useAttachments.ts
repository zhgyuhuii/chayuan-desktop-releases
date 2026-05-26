/**
 * 附件链路：拖拽 / 选择 / 粘贴 → 按类型分流上传：
 *
 *   - 图片 → POST /v1/files (purpose=vision) → remoteUrl（vision 用 image_urls）
 *   - 其他 → POST /knowledge_base/upload_temp_docs → fileChatId（temp_kb 文件对话）
 *
 * 双端共用 PAL.fs；单文件独立 promise（图片 Promise.all 并发；docs 顺序保 prev_id 链）。
 * remoteUrl 是相对路径（/v1/files/.../content），vision 节点会拼上 baseURL。
 */

import * as React from 'react';
import {
  aiPlatform,
  files as filesApi,
  kb,
  knowledgeUniverse,
  modality,
  type KuItem,
  type UploadTempDocsResult,
  getClientConfig,
} from '@chayuan/api';
import { uuid, getPlatform } from '@chayuan/platform-shared';
import { Events, logEvent } from '@chayuan/observability';
import { useQuery } from '@tanstack/react-query';
import {
  useComposerScopedStore, useComposerS, type AttachmentDraft,
} from '../../store/composer';
import { notifyInfo, reportError } from '../../store/errorDialog';

export function useAttachments(): {
  attachments: AttachmentDraft[];
  pickFiles(opts?: { accept?: string | string[]; multiple?: boolean }): Promise<void>;
  pickImages(): Promise<void>;
  /** ASR 模式专用 — 单个音频文件,本地暂存为 blob URL,送 modality 时 base64 出去。
   *  不走 /v1/files 上传(后端 ASR 链路 _resolve_local_audio 期望 artifacts URL,
   *  浏览器 blob URL 后端读不到 → 在 ConversationView 送之前 fetch 转 base64)。 */
  pickAudio(): Promise<void>;
  /** 编程入口:外部已经拿到 File[] 时(截图 / 粘贴 / 跨组件传递),
   *  直接走 useAttachments 内部 upload 管线 — 无需 picker。 */
  addImages(items: File[]): Promise<void>;
  addFiles(items: File[]): Promise<void>;
  addAudio(items: File[]): Promise<void>;
  acceptDrop(event: React.DragEvent): Promise<void>;
  remove(id: string): void;
  reset(): void;
  getOcrPromptAppendix(): string;
} {
  const composer = useComposerScopedStore();
  const attachments = useComposerS((s) => s.attachments);
  const upsert = useComposerS((s) => s.upsertAttachment);
  const removeRef = useComposerS((s) => s.removeAttachment);
  const resetRef = useComposerS((s) => s.resetAttachments);
  const selectedKuIds = useComposerS((s) => s.selectedKuIds);
  // KB catalog — 跟 KnowledgePickerPill 共享 react-query 缓存(同 queryKey),
  // 不增加额外网络。上传图片时用来判"用户当前选的 KB 里有没有 image-kind",
  // 没有就完全跳过 image-embedding preflight + notifyInfo,免得"我就要发图聊天
  // 而已"也被弹一条"图像向量化未就绪"。
  const { data: kuCatalog = [] } = useQuery<KuItem[]>({
    queryKey: ['ku.list', 'composer-picker'],
    queryFn: () => knowledgeUniverse.list(),
    staleTime: 60 * 1000,
  });

  const setFileChatId = (id?: string) =>
    composer.setState({ fileChatId: id });

  const upload = React.useCallback(
    async (kind: 'file' | 'image' | 'audio', items: File[]) => {
      const drafts: AttachmentDraft[] = items.map((f) => ({
        id: uuid(),
        name: f.name,
        size: f.size,
        mime: f.type,
        type: kind,
        previewUrl:
          kind === 'image' || kind === 'audio' ? URL.createObjectURL(f) : undefined,
        status: 'uploading',
      }));
      for (const d of drafts) upsert(d);
      logEvent(Events.ComposerAttachmentAdd, { metadata: { count: items.length, kind } });

      if (kind === 'image') {
        // image-embedding preflight 只在用户**确实选了 image-kind KB**时跑 —
        // 因为这个能力只服务"持久化向量化 → 以图搜图 / image KB 检索",跟
        // OCR(RapidOCR)和 Vision(multimodal LLM image_url)都**没关系**。
        //
        // 之前无条件 preflight 会弹"图像向量化能力未就绪"对所有用户狂刷,
        // 即使用户根本没选 image KB、就只是想发张图聊天。统一约束:只有"打
        // 算把这张图入库做以图搜图"的场景才提示安装。
        const hasImageKb = (() => {
          if (!selectedKuIds.length) return false;
          const byId = new Map(kuCatalog.map((it) => [it.ku_id, it]));
          return selectedKuIds.some((id) => byId.get(id)?.kind === 'image');
        })();
        if (hasImageKb) {
          try {
            const pre = await aiPlatform.embeddingsPreflight.check('image');
            if (!pre.ok) {
              const recName = pre.setup?.default?.id ?? 'jina-clip-v1';
              notifyInfo(
                '图像向量化能力未就绪(不影响本次对话)',
                `本次对话仍会做 OCR + 原图 vision;但若要把图入选中的「图像知识库」` +
                  `做以图搜图,建议在「设置 → AI 平台 → 图像嵌入」安装 ${recName}。`,
                5000,
              );
              // 不改 status,不 return — 继续走 OCR + vision
            }
          } catch (err: unknown) {
            // preflight 端点不可用 — 静默降级继续走;真实问题会在后续 OCR/vision 失败时再暴露
            console.debug('[image attach] embeddingsPreflight unavailable, skipping:', err);
          }
        }
        // 双轨 OCR:并行触发(不阻塞 vision 上传),识别结果异步回填 ocrText
        for (const d of drafts) {
          upsert({ ...d, ocrStatus: 'pending' });
        }
        const ocrPromises = drafts.map(async (d, idx) => {
          const f = items[idx]!;
          try {
            const r = await modality.ocr(f);
            const txt = (r.text || '').trim();
            // 注意:此时 d.status 可能已经被 vision upload 路径改成 ready/error;
            // 我们只覆盖 ocr* 字段,不动 status/remoteUrl。从 store 读最新版。
            const latest = composer.getState().attachments.find((a) => a.id === d.id);
            if (!latest) return; // 用户已删除
            upsert({
              ...latest,
              ocrStatus: txt ? 'ok' : 'empty',
              ocrText: txt,
            });
          } catch (err) {
            const latest = composer.getState().attachments.find((a) => a.id === d.id);
            if (!latest) return;
            upsert({
              ...latest,
              ocrStatus: 'failed',
              ocrError: err instanceof Error ? err.message : 'OCR 失败',
            });
          }
        });
        // 把 ocrPromises 挂到全局以便 ConversationView 发送前 await(MVP 不 await,fire-and-forget)
        void Promise.all(ocrPromises);

        // 图片走 /v1/files；并发 — vision 不依赖 prev_id 链
        const baseURL = getClientConfig().baseURL.replace(/\/+$/, '');
        await Promise.all(
          drafts.map(async (d, idx) => {
            const f = items[idx]!;
            try {
              const r = await filesApi.upload(f, 'vision');
              // 相对 url 拼成绝对（vision 节点要求公网/绝对）；空 base 时保持相对
              const remoteUrl = baseURL ? `${baseURL}${r.url}` : r.url;
              upsert({ ...d, status: 'ready', remoteUrl });
            } catch (err: unknown) {
              upsert({ ...d, status: 'error', error: err instanceof Error ? err.message : '上传失败' });
            }
          }),
        );
        return;
      }

      if (kind === 'audio') {
        // ASR 模式专用 — 不走 /v1/files(后端 ASR 链路 _resolve_local_audio
        // 只识别 /v1/artifacts/<sha>;浏览器 blob URL 后端读不到)。这里只本地
        // 暂存:UI 用 previewUrl(blob:) 渲染附件条;发送时 ConversationView
        // fetch(blob URL) → arrayBuffer → base64 走 modality_attachments 通道,
        // 后端解码落 artifacts 后再注入 GenerateReq.attachments。
        for (const d of drafts) upsert({ ...d, status: 'ready', ocrStatus: 'skipped' });
        return;
      }

      // 文档走 temp_kb；顺序累积 prev_id
      let prevId = composer.getState().fileChatId;
      for (let i = 0; i < drafts.length; i++) {
        const d = drafts[i]!;
        const f = items[i]!;
        try {
          const result: UploadTempDocsResult = await kb.uploadTempDocs([f], prevId);
          prevId = result.id || prevId;
          upsert({ ...d, status: 'ready', fileChatId: result.id, ocrStatus: 'skipped' });
        } catch (err: unknown) {
          upsert({ ...d, status: 'error', error: err instanceof Error ? err.message : '上传失败', ocrStatus: 'skipped' });
        }
      }
      if (prevId) setFileChatId(prevId);
    },
    [upsert],
  );

  const pickFiles = React.useCallback(
    async (opts?: { accept?: string | string[]; multiple?: boolean }) => {
      const files = await getPlatform().fs.pickFiles({ multiple: opts?.multiple ?? true, accept: opts?.accept });
      if (files.length) await upload('file', files);
    },
    [upload],
  );

  const pickImages = React.useCallback(async () => {
    const files = await getPlatform().fs.pickFiles({ multiple: true, accept: 'image/*' });
    if (files.length) await upload('image', files);
  }, [upload]);

  const pickAudio = React.useCallback(async () => {
    const files = await getPlatform().fs.pickFiles({ multiple: false, accept: 'audio/*' });
    // ASR 同时只用一个 attachment;若用户多选(平台允许)只取第一个
    if (files.length) await upload('audio', files.slice(0, 1));
  }, [upload]);

  const addImages = React.useCallback(async (items: File[]) => {
    const imgs = items.filter((f) => f.type.startsWith('image/'));
    if (!imgs.length) return;
    await upload('image', imgs);
  }, [upload]);

  const addFiles = React.useCallback(async (items: File[]) => {
    if (!items.length) return;
    await upload('file', items);
  }, [upload]);

  const addAudio = React.useCallback(async (items: File[]) => {
    const audios = items.filter((f) => f.type.startsWith('audio/'));
    if (!audios.length) return;
    await upload('audio', audios.slice(0, 1));
  }, [upload]);

  const acceptDrop = React.useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault();
      const files = await getPlatform().fs.readDropped(event.nativeEvent);
      if (!files.length) return;
      const images = files.filter((f) => f.type.startsWith('image/'));
      const docs = files.filter((f) => !f.type.startsWith('image/'));
      if (docs.length) await upload('file', docs);
      if (images.length) await upload('image', images);
    },
    [upload],
  );

  const remove = React.useCallback(
    (id: string) => {
      const att = composer.getState().attachments.find((a) => a.id === id);
      if (att?.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(att.previewUrl);
      removeRef(id);
      logEvent(Events.ComposerAttachmentRemove, { metadata: { id } });
    },
    [removeRef],
  );

  const reset = React.useCallback(() => {
    for (const a of composer.getState().attachments) {
      if (a.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(a.previewUrl);
    }
    resetRef();
  }, [resetRef]);

  /**
   * 发送前拼接 OCR 文本到 prompt 末尾。
   *
   * 产品决策(参考 DeepSeek / ChatGPT):用户消息气泡里**不要直接把整段 OCR
   * 文字铺开** — 几百字 OCR 把用户原话淹没,视觉极差,也让用户搞不清自己
   * 到底发了什么。改用 ``<details>`` 折叠 — summary 显示一行紧凑摘要(图标
   * + 文件名 + 字数);展开后看完整 OCR 内容(用 ``<pre>`` 等宽块包).
   *
   * 引导 LLM:summary 之后单独一行 "以下为附件 OCR 识别内容,作为对话补充
   * 资料;若用户没明确提问可暂忽略" — 避免模型把 OCR 全部当成用户的问题
   * 重复念出来。
   *
   * 只取 ``ocrStatus='ok'`` 的 image attachment 的 ocrText。
   * 返回空字符串表示"没有 OCR 内容,无需拼接"。
   */
  const getOcrPromptAppendix = React.useCallback((): string => {
    const items = composer.getState().attachments
      .filter((a) => a.type === 'image' && a.ocrStatus === 'ok' && a.ocrText);
    if (!items.length) return '';
    // 引导段在所有 details 之前,只出现一次;LLM 看到这段会理解 OCR 是参考资料
    const intro =
      '\n\n---\n_以下为附件 OCR 识别内容(参考资料,非用户问题正文)_\n';
    const blocks = items.map((a) => {
      const len = (a.ocrText || '').length;
      // <details> + <summary> 折叠;<pre> 包 OCR 文本(避免被 markdown 误解析)
      // marked + DOMPurify 链路允许这三个标签(见 lib/markdown.tsx)
      return [
        `<details>`,
        `<summary>📎 ${a.name} · OCR 识别(${len} 字)</summary>`,
        '',
        '<pre>',
        a.ocrText,
        '</pre>',
        `</details>`,
      ].join('\n');
    });
    return intro + '\n' + blocks.join('\n\n');
  }, []);

  return {
    attachments, pickFiles, pickImages, pickAudio,
    addImages, addFiles, addAudio,
    acceptDrop, remove, reset, getOcrPromptAppendix,
  };
}
