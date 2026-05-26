/**
 * 主页"按模型类型开始对话"网格 — 主页直接放这一个组件即可。
 *
 * 职责:
 *   1. 拉一次 ``useAvailableModelsByCapability`` 共享缓存
 *   2. 按 ``HOME_CAPABILITY_CARDS`` 顺序渲染每张 ``CapabilityCard``
 *   3. 卡片 onActivate / onShowHelp 都用 ``useCallback`` 稳定,避免 memo 失效
 *   4. ``loading`` 时铺骨架,首屏不闪
 *   5. "已配模型 + 未配模型"混排可读:section header 显示"已接入 X 类 / 共 7 类"
 *
 * 帮助 dialog 是一个 controlled Dialog,通过 ``initialCap`` 锚定 scroll;
 * 同一时刻只有一个,避免重复 mount 浪费。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Settings2 } from 'lucide-react';
import type { ModalityCapability } from '@chayuan/transport';
import { CapabilityCard } from './CapabilityCard';
import { HOME_CAPABILITY_CARDS } from './capabilityCardCatalog';
import {
  pickBestModel,
  useAvailableModelsByCapability,
} from './useAvailableModelsByCapability';
import { startCapabilityChat } from './startCapabilityChat';
import {
  ModelSupportHelpButton,
  ModelSupportHelpDialog,
} from '../../composer/ModelSupportHelpDialog';


export interface CapabilityCardGridProps {
  /** 没任何配置时,主页可能把卡片整片隐藏走另一条 onboarding;不传默认始终显示 */
  hideWhenAllEmpty?: boolean;
}


export const CapabilityCardGrid: React.FC<CapabilityCardGridProps> = ({
  hideWhenAllEmpty = false,
}) => {
  const navigate = useNavigate();
  const { byCap, hasAny, isLoading, isFetched } = useAvailableModelsByCapability();
  // 帮助 dialog 锚跳 — 未配卡 onShowHelp 时把它打开并自动滚到对应 cap 段
  const [helpOpen, setHelpOpen] = React.useState(false);
  const [helpAnchor, setHelpAnchor] = React.useState<ModalityCapability | undefined>(undefined);

  // 稳定回调:模型列表变 / nav 变才重建,React.memo 才有效
  const onActivate = React.useCallback(
    (cap: ModalityCapability) => {
      const best = pickBestModel(byCap.get(cap));
      if (!best) return; // 防御:卡片本来该禁用,但极端竞争下兜一手
      startCapabilityChat(
        { cap, model: best },
        { navigate: (to) => void navigate({ to: to as never }) },
      );
    },
    [byCap, navigate],
  );

  const onShowHelp = React.useCallback((cap: ModalityCapability) => {
    setHelpAnchor(cap);
    setHelpOpen(true);
  }, []);

  if (hideWhenAllEmpty && isFetched && !hasAny) return null;

  return (
    <section
      // 背景半透明 — 让底部金龙 / 星空动画透出来,不被整块挡住;再用边框 +
      // 极淡底色把 7 张卡片框成一个可辨识的「分组」。卡片自身不透明,内容清晰。
      className="rounded-3xl border border-[var(--cy-border-default)] p-5"
      style={{
        background: 'color-mix(in srgb, var(--cy-surface-1) 26%, transparent)',
      }}
    >
      <header className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[var(--cy-text-primary)]">按模型类型开始对话</h2>
          <p className="text-xs text-[var(--cy-text-tertiary)]">
            选一类能力直接开新对话,系统自动挑可用模型并切换好输入框的模式
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* "如何配置"入口 — 跟卡片"未配置"按钮共用同一个帮助面板 */}
          <span
            className="hidden text-[11px] text-[var(--cy-text-tertiary)] sm:inline-flex items-center gap-1"
            title="该选择器跟 ChatComposer 顶部 ✨ 模式 badge 同一套色"
          >
            <Settings2 className="h-3 w-3" /> 通用调色板
          </span>
          <ModelSupportHelpButton />
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {isLoading && !isFetched ? (
          // 骨架 — 不阻塞首屏,等 react-query 跑完自动替换
          Array.from({ length: HOME_CAPABILITY_CARDS.length }).map((_, i) => (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: 骨架卡固定数量,顺序稳定
              key={`skel-${i}`}
              className="h-36 animate-pulse rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]"
            />
          ))
        ) : (
          HOME_CAPABILITY_CARDS.map((spec) => {
            const count = byCap.get(spec.cap)?.length ?? 0;
            return (
              <CapabilityCard
                key={spec.cap}
                spec={spec}
                modelCount={count}
                onActivate={onActivate}
                onShowHelp={onShowHelp}
              />
            );
          })
        )}
      </div>

      {/* 主页未配置卡触发的"如何接入"帮助 — initialCap 自动滚到对应 cap 段 */}
      <ModelSupportHelpDialog
        open={helpOpen}
        onOpenChange={setHelpOpen}
        initialCap={helpAnchor}
      />
    </section>
  );
};
