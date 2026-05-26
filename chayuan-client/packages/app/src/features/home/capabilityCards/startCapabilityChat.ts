/**
 * 主页能力卡的"点击 → 开新对话"行为 — 纯函数,可复用、易测。
 *
 * 步骤(严格顺序,zustand 串行写入保证):
 *   1. setModel(modelId, platformName)        预选模型,ChatComposer mount 时直接见
 *   2. setActive(null)                          进入草稿模式,不绑老 conversation
 *   3. open('/chat', { forceNew:true, ... })   每次都开一个全新 tab,不复用旧 /chat
 *   4. navigate('/chat')                        切到这个 tab
 *
 * 为什么放在外部纯函数而不是 CapabilityCard 内部 closure:
 *   - 多张卡共享同一份"点击逻辑";写在 closure 里每张卡的 onClick 都会因为
 *     函数引用变化触发 React.memo 失效,所有卡都重渲染
 *   - 纯函数 + 用 useCallback 在父级稳定一次,所有 React.memo 卡片真正只在
 *     props(model 数量 / 状态)变化时重渲
 *   - 单测好写:不依赖 React 树
 */

import type { RawModelItem } from '@chayuan/api';
import type { ModalityCapability } from '@chayuan/transport';
import { useComposerStore } from '../../../store/composer';
import { useTabsStore } from '../../../store/tabs';
import { getCapabilityMeta } from '../../composer/modelCapabilityChip';

export interface StartCapabilityChatDeps {
  /** TanStack Router 的 navigate 函数(由调用方传,避免在纯函数里耦合 router) */
  navigate(to: string): void;
}

export interface StartCapabilityChatInput {
  cap: ModalityCapability;
  model: RawModelItem;
}


/**
 * 真正开新对话 — 调用方需已经确认该 cap 有可用模型(否则走 onNoModel 路径)。
 */
export function startCapabilityChat(
  input: StartCapabilityChatInput,
  deps: StartCapabilityChatDeps,
): void {
  const { cap, model } = input;
  const meta = getCapabilityMeta(cap);

  // 1. 预选模型 — ChatComposer 监听 composer.modelId,会自动联动 ComposerModeBadge /
  //    placeholder / submitLabel / 参数条
  const composer = useComposerStore.getState();
  composer.setModel(model.id, model.platform_name);
  // 2. 草稿态 — 不绑老 conversation,handleSend 首发时再 lazy create
  composer.setActive(null);

  // 3. 新 tab — forceNew 确保跟现有 /chat tab 不复用(否则用户当前的草稿会被覆盖)
  const tabs = useTabsStore.getState();
  tabs.open('/chat', {
    title: `新对话 · ${meta.label}`,
    icon: 'message-square',
    forceNew: true,
  });

  // 4. 路由跳转
  deps.navigate('/chat');
}
