/**
 * buildChatRequest —— 把 ChatMessage 数组 + composer 当前态拼成后端 ChatRequestV2。
 *
 * 抽出原因:KbBoard 也需要复用这套 mode 推导(vision > kb > file > agent > llm)
 * 与字段映射,避免与 ChatPage 写两份不同步的逻辑。
 */

import type { ChatRequestV2 } from '@chayuan/transport';
import { useComposerStore } from '../../store/composer';
import type { ChatMessage } from './useChayuanChat';

export function buildChatRequest(
  query: string,
  messages: ChatMessage[],
  composer: ReturnType<typeof useComposerStore.getState>,
): ChatRequestV2 {
  const imageUrls = composer.attachments
    .filter((a) => a.type === 'image' && a.status === 'ready' && a.remoteUrl)
    .map((a) => a.remoteUrl as string);

  // 模式推导:vision > kb(任何 ku_ids 都触发,doc/structured/image 通吃) > file > agent > llm
  const hasAnyKb = composer.selectedKuIds.length > 0 || composer.selectedKbs.length > 0;
  let mode: ChatRequestV2['mode'] = '';
  if (imageUrls.length) mode = 'vision';
  else if (hasAnyKb) mode = 'kb';
  else if (composer.fileChatId) mode = 'file';
  else if (composer.selectedTools.length || composer.selectedMcps.length) mode = 'agent';

  return {
    query,
    mode,
    history: messages.slice(0, -1).map((m) => ({
      role: (m.role === 'user' || m.role === 'assistant' ? m.role : 'system') as
        | 'user'
        | 'assistant'
        | 'system',
      content: m.content,
    })),
    stream: true,
    // 77 题:同名跨平台模型(deepseek 与 baidu-qianfan 都有 deepseek-v4-flash)需要
    // 携带 platform 让 server 精确路由,否则 server `get_model_client` 走 dict
    // 取一个(通常是 yaml 末尾的 platform),用户切换不生效。
    // 编码格式:`${platform}::${id}`(server openai_routes.get_model_client 解析)
    model: composer.platformName
      ? `${composer.platformName}::${composer.modelId}`
      : composer.modelId,
    conversation_id: composer.activeConversationId ?? undefined,
    kb_name: composer.selectedKbs[0],
    kb_names: composer.selectedKbs,
    // P0:ku_ids 是后端 KBHandler 多源聚合的真源(doc + structured + image)
    ku_ids: composer.selectedKuIds,
    file_chat_id: composer.fileChatId,
    image_url: imageUrls[0],
    image_urls: imageUrls.length ? imageUrls : undefined,
    tools: composer.selectedTools,
    use_mcp: composer.selectedMcps.length > 0,
    governance_enabled: true,
    temperature: composer.temperature,
    max_tokens: composer.maxTokens,
    prompt_name: composer.promptName,
    top_k: composer.topK,
    score_threshold: composer.scoreThreshold,
    // P2:检索模式 chip 三件套
    rewrite_strategy: composer.rewriteStrategy,
    use_hybrid: composer.searchMode === 'balanced' ? true
              : composer.searchMode === 'precise' || composer.searchMode === 'fast' ? false
              : undefined,
    use_rerank: composer.searchMode === 'balanced' ? true
              : composer.searchMode === 'precise' || composer.searchMode === 'fast' ? false
              : undefined,
    // 深度思考 ✨ — composer 里那颗按钮的状态;后端按模型族决定怎么注入(qwen3
    // enable_thinking / claude thinking budget / o-series 自带)
    deep_think: composer.deepThink,
  };
}
