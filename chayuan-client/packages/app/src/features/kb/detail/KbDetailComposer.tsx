/**
 * KB 详情页底部 composer —— 锁定单个 kuId 的"问一问"入口。
 *
 * - 走 knowledgeUniverse.askStream(query, [kuId], 5),命中即时显示
 * - 用既有 KbAskResultPanel 渲染结果块(传单元素 itemsByKuId Map)
 * - 与 KbBoard 跨 KB ask 区别:本组件**只问当前 KB**
 *
 * 性能:askStream SSE 流式拿块,onBlock 增量推 results;卸载即 abort。
 */

import * as React from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { annotationApi, knowledgeUniverse, type AskBlock, type AskResponse, type KuItem } from '@chayuan/api';
import { ChatComposer } from '../../composer/ChatComposer';
import { KbAskResultPanel } from '../KbAskResultPanel';
import { reportError } from '../../../store/errorDialog';
import { useComposerStore } from '../../../store/composer';

export interface KbDetailComposerProps {
  /** 锁定的 ku_id(形如 doc:xxx / src:42) */
  kuId: string;
  /** 显示用 KB 元(用来给 KbAskResultPanel 拼标题) */
  kuItem: KuItem;
  placeholder?: string;
}

interface AskState {
  query: string;
  status: 'idle' | 'pending' | 'running' | 'ok' | 'error';
  response: AskResponse | null;
  error?: string | null;
  abortController?: AbortController;
}

export const KbDetailComposer: React.FC<KbDetailComposerProps> = ({ kuId, kuItem, placeholder }) => {
  const [ask, setAsk] = React.useState<AskState>({ query: '', status: 'idle', response: null });
  const askRef = React.useRef(ask);
  askRef.current = ask;
  const composerModelId = useComposerStore((s) => s.modelId);
  const rewriteStrategy = useComposerStore((s) => s.rewriteStrategy);
  const searchMode = useComposerStore((s) => s.searchMode);
  const topK = useComposerStore((s) => s.topK);
  const selectedKuIds = useComposerStore((s) => s.selectedKuIds);
  const composerModelIdRef = React.useRef(composerModelId);
  composerModelIdRef.current = composerModelId;
  const rewriteStrategyRef = React.useRef(rewriteStrategy);
  rewriteStrategyRef.current = rewriteStrategy;
  const searchModeRef = React.useRef(searchMode);
  searchModeRef.current = searchMode;
  const topKRef = React.useRef(topK);
  topKRef.current = topK;

  const kuList = useQuery({
    queryKey: ['ku.list', 'detail-composer'],
    queryFn: () => knowledgeUniverse.list(),
    staleTime: 60 * 1000,
  });
  const effectiveKuIds = React.useMemo(
    () => (selectedKuIds.length > 0 ? selectedKuIds : [kuId]),
    [kuId, selectedKuIds],
  );
  const itemsByKuId = React.useMemo(() => {
    const map = new Map<string, KuItem>();
    for (const item of kuList.data ?? []) map.set(item.ku_id, item);
    map.set(kuId, map.get(kuId) ?? kuItem);
    return map;
  }, [kuId, kuItem, kuList.data]);

  React.useEffect(() => () => askRef.current.abortController?.abort(), []);

  const onSend = async (content: string) => {
    askRef.current.abortController?.abort();
    const ac = new AbortController();
    setAsk({ query: content, status: 'pending', response: null, error: null, abortController: ac });

    const blocks: AskBlock[] = [];
    try {
      await knowledgeUniverse.askStream(
        content,
        effectiveKuIds,
        topKRef.current ?? (searchModeRef.current === 'balanced' ? 8 : searchModeRef.current === 'precise' ? 3 : 5),
        {
          onMeta: () => {
            setAsk((s) => ({
              ...s,
              status: 'running',
              response: { query: content, results: blocks },
            }));
          },
          onBlock: (block) => {
            blocks.push(block);
            setAsk((s) => ({
              ...s,
              status: 'running',
              response: { query: content, results: [...blocks] },
            }));
          },
          onDone: () => {
            const response = { query: content, results: [...blocks] };
            setAsk({
              query: content,
              status: 'ok',
              response,
            });
            void annotationApi.sampleFromKbAsk({
              query: content,
              ku_ids: effectiveKuIds,
              top_k: topKRef.current ?? 5,
              result: response as unknown as Record<string, unknown>,
              options: {
                source: 'KbDetailComposer',
                model: composerModelIdRef.current,
                rewrite: rewriteStrategyRef.current,
                search_mode: searchModeRef.current,
              },
            }).catch(() => {/* sampling is best effort */});
          },
          onError: (err) => {
            setAsk({ query: content, status: 'error', response: null, error: err.message });
          },
        },
        {
          signal: ac.signal,
          model: composerModelIdRef.current,
          rewrite: rewriteStrategyRef.current,
          useHybrid: searchModeRef.current === 'balanced',
          useRerank: searchModeRef.current === 'balanced',
        },
      );
    } catch (e) {
      if (ac.signal.aborted) return;
      reportError(e, '问一问失败');
      setAsk({ query: content, status: 'error', response: null, error: (e as Error).message });
    }
  };

  const onClear = () => {
    askRef.current.abortController?.abort();
    setAsk({ query: '', status: 'idle', response: null });
  };

  const isStreaming = ask.status === 'pending' || ask.status === 'running';

  return (
    <div className="flex flex-shrink-0 flex-col border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]">
      {ask.status !== 'idle' && (
        <div className="relative max-h-[40vh] overflow-y-auto border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]">
          <button
            type="button"
            onClick={onClear}
            className="absolute right-3 top-3 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            aria-label="关闭结果"
            title="关闭结果"
          >
            <X className="h-4 w-4" />
          </button>
          {ask.status === 'error' ? (
            <div className="mx-auto flex max-w-3xl items-start gap-2 px-6 py-6 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-[var(--cy-text-primary)]">问一问失败</p>
                <p className="mt-0.5 text-xs text-[var(--cy-text-secondary)]">{ask.error || '未知错误'}</p>
              </div>
            </div>
          ) : (
            <KbAskResultPanel
              query={ask.query}
              result={ask.response}
              itemsByKuId={itemsByKuId}
              loading={isStreaming}
              selectedKuIds={effectiveKuIds}
            />
          )}
        </div>
      )}
      <div className="px-4 py-2.5">
        <ChatComposer
          isStreaming={isStreaming}
          onSend={onSend}
          onStop={() => askRef.current.abortController?.abort()}
          glow={false}
          placeholder={placeholder ?? '问问这个知识库里的内容…'}
          showRetrievalControls
        />
      </div>
    </div>
  );
};
