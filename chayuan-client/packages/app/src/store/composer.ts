/**
 * Composer / 客户端 UI 态。
 *
 * 仅放真正"客户端独占、不需要服务端缓存"的状态：
 *   - 当前 active conversation id
 *   - 当前选中模型
 *   - 当前能力多选（tools / mcp / kb）
 *   - 输入区草稿、附件
 *   - 流式中标志
 *
 * 服务端态（会话列表、工具列表、模型列表等）走 TanStack Query。
 */

import * as React from 'react';
import { create, type StoreApi, type UseBoundStore } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface AttachmentDraft {
  id: string;
  name: string;
  size: number;
  mime: string;
  type: 'file' | 'image' | 'audio';
  previewUrl?: string;
  fileChatId?: string;
  remoteUrl?: string;
  status: 'pending' | 'uploading' | 'ready' | 'error';
  error?: string;
  // ── OCR 双轨字段(2026-05-16 加):图片上传时并发跑 OCR,文本拼进 prompt ──
  /** 'pending' = OCR 进行中;'ok' = 已完成有文本;'empty' = 已完成无文本(图上没字);
   *  'failed' = OCR 服务失败(原图仍走 vision);'skipped' = 非图像类型,不做 OCR */
  ocrStatus?: 'pending' | 'ok' | 'empty' | 'failed' | 'skipped';
  ocrText?: string;
  ocrError?: string;
}

interface ComposerState {
  activeConversationId: string | null;
  draft: string;
  attachments: AttachmentDraft[];
  fileChatId?: string;
  modelId: string;
  /**
   * 77 题:模型所属平台(可选)。
   * - undefined:自动选(server `get_model_client` 走 dict 取一个,通常是 yaml 末尾的 platform)
   * - 给定:buildChatRequest 把 `model` 字段编码为 ``${platform}::${id}`` 让 server 精确路由,
   *   避免跨平台同名 model 被覆盖
   */
  platformName?: string;
  /**
   * 按请求覆盖的 embed 模型 id;空字符串 / undefined → "跟随 KB 默认"。
   * UI 在结果卡片上挂小徽章告诉用户实际用了哪个;后端按维度兼容性过滤。
   */
  embedModelId?: string;
  /**
   * Query 改写策略;默认 'auto'(短/含代词→hyde,复杂→multi_query,其它 passthrough)。
   * 'off' 完全跳过改写。
   */
  rewriteStrategy: 'auto' | 'passthrough' | 'rewrite' | 'multi_query' | 'hyde' | 'decompose' | 'off';
  deepThink: boolean;
  selectedTools: string[];
  selectedMcps: string[];
  selectedKbs: string[];
  /**
   * 统一的 KB 宇宙选中态:形如 ['doc:my-kb', 'src:42'](Phase 2 引入)。
   * Phase 2 行为:KbBoard 把 doc 类型的 ku_id 映射回 selectedKbs 维持旧 /chat/v2/chat 兼容;
   * Phase 3 起 ChatComposer 直接走 /knowledge_universe/ask 用 selectedKuIds dispatch。
   */
  selectedKuIds: string[];
  isStreaming: boolean;

  /** 高级参数；空表示用后端默认 */
  temperature?: number;
  maxTokens?: number;
  promptName?: string;
  topK?: number;
  scoreThreshold?: number;

  /**
   * 检索模式 chip(P2):前端打包 → 后端检索栈参数。
   *  - precise:精准 — passthrough,top_k 3,score>=0.5,无 hybrid/无 rerank
   *  - balanced:全面(默认) — auto rewrite,top_k 8,hybrid + rerank
   *  - fast:速度优先 — passthrough,top_k 5,score>=0.3,无 rerank
   * 切换时一次性 set 上面所有相关字段。
   */
  searchMode: 'precise' | 'balanced' | 'fast';

  /**
   * 模态路由参数(t2i 的 size/n/seed,t2v 的 size/duration/fps,tts 的
   * voice/speed 等)— 仅在非 chat capability 下生效。聊天模型 ignore 此字段。
   * 形态故意保持松散 ``Record<string, unknown>``,Connector 端自行 narrow,
   * 加新参数不需要改 store 类型。
   */
  modalityParams: Record<string, unknown>;

  setDraft(s: string): void;
  setActive(id: string | null): void;
  /** 77 题:platformName 给定时,modelId 编码为 `${platformName}::${id}` 让 server 精确路由,避免跨平台同名覆盖 */
  setModel(id: string, platformName?: string): void;
  setEmbedModel(id?: string): void;
  setRewriteStrategy(s: ComposerState['rewriteStrategy']): void;
  setSearchMode(m: ComposerState['searchMode']): void;
  toggleDeepThink(): void;
  setTools(ids: string[]): void;
  setMcps(ids: string[]): void;
  setKbs(names: string[]): void;
  setKuIds(ids: string[]): void;
  upsertAttachment(a: AttachmentDraft): void;
  removeAttachment(id: string): void;
  resetAttachments(): void;
  setStreaming(b: boolean): void;
  setAdvanced(patch: Partial<Pick<ComposerState, 'temperature' | 'maxTokens' | 'promptName' | 'topK' | 'scoreThreshold'>>): void;
  resetAdvanced(): void;
  /** 模态参数局部 patch — 不动其它键 */
  setModalityParams(patch: Record<string, unknown>): void;
  resetModalityParams(): void;
}

/** 96 题:把 store 实现拆出来,允许工厂创建多个独立实例(每个聊天窗口一份)。
 *
 * - 默认全局实例(`useComposerStore`)持久化到 localStorage 'cy.composer'
 *   — 给老调用方用,行为零回归
 * - lane 实例(`createComposerStore('lane-x')`)用 'cy.composer.lane-x' key
 *   — 各自持久化,刷新后保留各 lane 选了什么模型/KB
 */
type ComposerStoreApi = UseBoundStore<StoreApi<ComposerState>>;

/** 可被 seed 的 per-window 字段(加新永道时从"最后一次操作"的 lane 拷贝过来) */
export interface ComposerSeed {
  modelId?: string;
  platformName?: string;
  selectedKbs?: string[];
  selectedKuIds?: string[];
  deepThink?: boolean;
  searchMode?: ComposerState['searchMode'];
  selectedTools?: string[];
  selectedMcps?: string[];
}

export function createComposerStore(
  scope = '__global__',
  seed?: ComposerSeed,
): ComposerStoreApi {
  // 96 题:如果给定 seed 且本 scope 还没在 localStorage 写过,先把 seed 写进去
  // 这样 zustand persist 在 hydrate 时拿到的就是 seed 后的值
  if (seed && scope !== '__global__' && typeof localStorage !== 'undefined') {
    const key = `cy.composer.${scope}`;
    if (!localStorage.getItem(key)) {
      try {
        localStorage.setItem(key, JSON.stringify({
          state: { ...seed }, version: 0,
        }));
      } catch {
        // 配额满 / 权限拒,忽略 — 加载时仍走默认值,体验降级但不报错
      }
    }
  }

  return create<ComposerState>()(
    persist(
      buildComposerStateImpl,
      {
        name: scope === '__global__' ? 'cy.composer' : `cy.composer.${scope}`,
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({
          modelId: s.modelId,
          platformName: s.platformName,
          embedModelId: s.embedModelId,
          rewriteStrategy: s.rewriteStrategy,
          deepThink: s.deepThink,
          selectedTools: s.selectedTools,
          selectedMcps: s.selectedMcps,
          selectedKbs: s.selectedKbs,
          selectedKuIds: s.selectedKuIds,
          temperature: s.temperature,
          maxTokens: s.maxTokens,
          promptName: s.promptName,
          topK: s.topK,
          scoreThreshold: s.scoreThreshold,
          searchMode: s.searchMode,
          draft: s.draft,
          modalityParams: s.modalityParams,
        }),
      },
    ),
  );
}

/** State 工厂闭包 — 拆出来让 createComposerStore 与默认实例共用一份逻辑 */
type SetFn = (
  partial: ComposerState | Partial<ComposerState>
    | ((state: ComposerState) => ComposerState | Partial<ComposerState>),
) => void;

function buildComposerStateImpl(set: SetFn): ComposerState {
  return _buildState(set);
}

const _buildState = (set: SetFn): ComposerState => ({
      activeConversationId: null,
      draft: '',
      attachments: [],
      modelId: '',  // 96 题:空字符串 → ChatComposer 显示"未选模型",由 ChatComposer 内部拿真实 /v1/models 列表第一个 fallback
      embedModelId: undefined,
      rewriteStrategy: 'auto',
      searchMode: 'balanced',
      deepThink: false,
      selectedTools: [],
      selectedMcps: [],
      selectedKbs: [],
      selectedKuIds: [],
      isStreaming: false,
      modalityParams: {},

      setDraft: (draft) => set({ draft }),
      setActive: (id) => set({ activeConversationId: id }),
      // 77 题:同名跨平台模型(如 baidu-qianfan 和 deepseek 都有 deepseek-v4-flash)
      // 必须记录 platformName,buildChatRequest 时拼接 `${platform}::${id}` 发给
      // server,server 解析后精确路由。store 里 modelId 仍然是裸 model_id,
      // 方便 UI find(m.id===modelId)。
      setModel: (modelId, platformName) =>
        set({ modelId, platformName: platformName || undefined }),
      setEmbedModel: (embedModelId) => set({ embedModelId: embedModelId || undefined }),
      setRewriteStrategy: (rewriteStrategy) => set({ rewriteStrategy }),
      setSearchMode: (m) => {
        if (m === 'precise') {
          set({
            searchMode: m, rewriteStrategy: 'passthrough',
            topK: 3, scoreThreshold: 0.5,
          });
        } else if (m === 'fast') {
          set({
            searchMode: m, rewriteStrategy: 'passthrough',
            topK: 5, scoreThreshold: 0.3,
          });
        } else {
          set({
            searchMode: m, rewriteStrategy: 'auto',
            topK: 8, scoreThreshold: 0.3,
          });
        }
      },
      toggleDeepThink: () => set((s) => ({ deepThink: !s.deepThink })),
      setTools: (selectedTools) => set({ selectedTools }),
      setMcps: (selectedMcps) => set({ selectedMcps }),
      setKbs: (selectedKbs) => set({ selectedKbs }),
      setKuIds: (selectedKuIds) => {
        // selectedKuIds 现在是"对话路由的真源",含 doc/structured/image 全部 kind。
        // 仍同步 selectedKbs 仅为前端旧路径(KbBoard hasNonDocSelected 判断 / 兼容
        // 老 mode='kb' 推导)还在用;后端 ChatRequest.ku_ids 为非空时 KBHandler 直接
        // 走多源聚合,不依赖 kb_names。
        const kbNames = selectedKuIds
          .filter((id) => id.startsWith('doc:'))
          .map((id) => id.slice(4));
        set({ selectedKuIds, selectedKbs: kbNames });
      },
      upsertAttachment: (a) =>
        set((s) => {
          const idx = s.attachments.findIndex((x) => x.id === a.id);
          if (idx === -1) return { attachments: [...s.attachments, a] };
          const next = [...s.attachments];
          next[idx] = a;
          return { attachments: next };
        }),
      removeAttachment: (id) =>
        set((s) => ({ attachments: s.attachments.filter((x) => x.id !== id) })),
      resetAttachments: () => set({ attachments: [], fileChatId: undefined, draft: '' }),
      setStreaming: (b) => set({ isStreaming: b }),
      setAdvanced: (patch) => set(patch),
      resetAdvanced: () =>
        set({ temperature: undefined, maxTokens: undefined, promptName: undefined, topK: undefined, scoreThreshold: undefined }),
      setModalityParams: (patch) =>
        set((s) => ({ modalityParams: { ...s.modalityParams, ...patch } })),
      resetModalityParams: () => set({ modalityParams: {} }),
});

/** 默认全局 store 实例(老调用方继续用,零回归) */
export const useComposerStore = createComposerStore('__global__');

/**
 * Context — 让 ChatComposer / ConversationView 等内部组件感知"当前应该用哪个 store 实例"。
 * 默认 undefined → useComposerScopedStore() 返回全局 useComposerStore。
 * 在 ChatWindow 内部 setProvider value 后,所有子组件自动用 lane 实例。
 */
export const ComposerStoreContext = React.createContext<ComposerStoreApi | null>(null);

/**
 * 96 题:取当前生效的 composer store。
 *
 * 优先 Context(在 ChatWindow Provider 内部) → 全局 useComposerStore(老路径)
 *
 * 用法(组件无 scope 感知,自动正确路由):
 *
 *     // 老:
 *     const draft = useComposerStore((s) => s.draft);
 *
 *     // 新:
 *     const useStore = useComposerScopedStore();
 *     const draft = useStore((s) => s.draft);
 *
 * 由于直接使用全局 hook 的代码量大,我们额外导出 useComposerStoreScoped 作为
 * "selector 形式"的代理 hook,签名跟原 useComposerStore 一模一样,内部
 * 自动从 Context 取真正的 store。
 */
export function useComposerScopedStore(): ComposerStoreApi {
  const ctx = React.useContext(ComposerStoreContext);
  return ctx ?? useComposerStore;
}

/**
 * 与原 useComposerStore selector 调用语法**一模一样**,但自动感知 ChatWindow scope。
 *
 *     const draft = useComposerS((s) => s.draft);
 *     const setDraft = useComposerS((s) => s.setDraft);
 *
 * 这样所有现有调用点改成 useComposerS 即可平滑切换;不在 Provider 内时与 useComposerStore 等价。
 */
export function useComposerS<T>(selector: (s: ComposerState) => T): T {
  const store = useComposerScopedStore();
  return store(selector);
}
