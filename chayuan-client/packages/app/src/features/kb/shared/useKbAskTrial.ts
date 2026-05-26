/**
 * useKbAskTrial — 单 KB 的"试问" hook,被三个详情面板复用。
 *
 * 职责:
 *  - 管理 idle / pending / ok / error 四态
 *  - 通过 stage 字段提供多阶段进度文案("解析意图 / 生成 SQL / 执行查询" 等)
 *  - 内部用 AbortController:用户重发时立即取消上一次请求,避免乱序
 *  - 仅返回 AskBlock(对应 detail.kind 的那一块);整体路由仍走 universe.ask
 *
 * 不做的事:
 *  - UI 渲染(KbAskComposer / KbResultBlock 自己负责)
 *  - 跨 KB 聚合(那是 KbAskResultPanel 的活)
 */

import * as React from 'react';
import { annotationApi, knowledgeUniverse, type AskBlock, type KuKind } from '@chayuan/api';
import { useComposerStore } from '../../../store/composer';

export type AskStage = 'idle' | 'submitting' | 'thinking' | 'querying' | 'rendering' | 'done' | 'error';

interface AskState {
  stage: AskStage;
  query: string;
  block: AskBlock | null;
  error: string | null;
}

const INITIAL: AskState = { stage: 'idle', query: '', block: null, error: null };

/** 按 kind 给出更贴切的阶段文案,UI 显示用 */
const STAGE_LABELS: Record<KuKind, Partial<Record<AskStage, string>>> = {
  document: {
    submitting: '正在投递问题…',
    thinking: '检索向量库…',
    querying: '排序与拼装片段…',
    rendering: '整理答案…',
  },
  structured: {
    submitting: '解析查询意图…',
    thinking: '生成 SQL…',
    querying: '执行查询…',
    rendering: '整理结果…',
  },
  vector: {
    submitting: '向量化查询…',
    thinking: '检索集合…',
    querying: '计算相似度…',
    rendering: '排序结果…',
  },
  image: {
    submitting: '准备查询图…',
    thinking: '提取图像特征…',
    querying: '检索相似图…',
    rendering: '排序结果…',
  },
};

export interface UseKbAskTrialReturn {
  state: AskState;
  /** 是否处于 pending 中(submitting/thinking/querying/rendering) */
  isPending: boolean;
  /** 当前 stage 的中文文案(按 kind 匹配) */
  stageLabel: string;
  /** 提交问题。重发时会取消上一次。 */
  submit(query: string, topK?: number, structuredScopeNames?: string[]): Promise<void>;
  /** 取消当前请求(若在飞)+ 回到 idle */
  cancel(): void;
  /** 软重置:保留 query,清结果 */
  reset(): void;
}

/** kind:用于阶段文案匹配;kuId:目标 KB 的 universe id */
export function useKbAskTrial(kind: KuKind, kuId: string): UseKbAskTrialReturn {
  const [state, setState] = React.useState<AskState>(INITIAL);
  const ctrlRef = React.useRef<AbortController | null>(null);
  const modelId = useComposerStore((s) => s.modelId);
  const modelIdRef = React.useRef(modelId);
  modelIdRef.current = modelId;

  // unmount 时取消未结束的请求
  React.useEffect(() => {
    return () => {
      ctrlRef.current?.abort();
    };
  }, []);

  const submit = React.useCallback(
    async (query: string, topK = 5, structuredScopeNames?: string[]) => {
      const q = query.trim();
      if (!q) return;
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;

      setState({ stage: 'submitting', query: q, block: null, error: null });
      // 阶段动画:用计时器顺移到 thinking → querying;真实响应一回来直接跳到 done
      const t1 = window.setTimeout(() => {
        if (!ctrl.signal.aborted) setState((s) => (s.stage === 'submitting' ? { ...s, stage: 'thinking' } : s));
      }, 350);
      const t2 = window.setTimeout(() => {
        if (!ctrl.signal.aborted) setState((s) => (s.stage === 'thinking' ? { ...s, stage: 'querying' } : s));
      }, 1100);
      const t3 = window.setTimeout(() => {
        if (!ctrl.signal.aborted) setState((s) => (s.stage === 'querying' ? { ...s, stage: 'rendering' } : s));
      }, 2400);

      try {
        const structuredScopes = structuredScopeNames?.length ? { [kuId]: structuredScopeNames } : undefined;
        // 注意:client.ts 的 request 不直接转发 signal 到 fetch?其实转发了,但 universe.ask 当前没暴露 signal 形参。
        // 这里用 Promise.race + abort 兜底:abort 时拒绝。
        const data = await Promise.race([
          knowledgeUniverse.ask(q, [kuId], topK, { model: modelIdRef.current, structuredScopes }),
          new Promise<never>((_, rej) => {
            ctrl.signal.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')));
          }),
        ]);
        if (ctrl.signal.aborted) return;
        const block = (data.results || []).find((b) => b.ku_id === kuId) ?? null;
        setState({ stage: 'done', query: q, block, error: null });
        void annotationApi.sampleFromKbAsk({
          query: q,
          ku_ids: [kuId],
          top_k: topK,
          result: data as unknown as Record<string, unknown>,
          options: { source: 'useKbAskTrial', kind, model: modelIdRef.current, structured_scope: structuredScopeNames ?? [] },
        }).catch(() => {/* sampling is best effort */});
      } catch (e) {
        if (ctrl.signal.aborted || (e as Error)?.name === 'AbortError') {
          // 取消是用户行为,不进 error
          return;
        }
        setState({
          stage: 'error',
          query: q,
          block: null,
          error: (e as Error).message || String(e),
        });
      } finally {
        clearTimeout(t1);
        clearTimeout(t2);
        clearTimeout(t3);
      }
    },
    [kind, kuId],
  );

  const cancel = React.useCallback(() => {
    ctrlRef.current?.abort();
    setState(INITIAL);
  }, []);

  const reset = React.useCallback(() => {
    ctrlRef.current?.abort();
    setState((s) => ({ ...INITIAL, query: s.query }));
  }, []);

  const isPending =
    state.stage === 'submitting' ||
    state.stage === 'thinking' ||
    state.stage === 'querying' ||
    state.stage === 'rendering';
  const stageLabel = STAGE_LABELS[kind]?.[state.stage] ?? '';

  return { state, isPending, stageLabel, submit, cancel, reset };
}
