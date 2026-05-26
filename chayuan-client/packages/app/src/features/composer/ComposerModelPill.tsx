/**
 * ComposerModelPill —— Composer 顶行的模型选择器(Pill 样式)。
 *
 * 数据源:GET /v1/models(`@chayuan/api` models.list)。
 * 选中后:写 useComposerStore.setModel(id);ChatPage / KbPage 在
 *        sendMessage 时用 composer.modelId 作为请求 model 字段,因此
 *        这里的选择直接决定下一次对话用哪个模型。
 *
 * 列表组织 / 折叠 / 搜索:统一在 features/composer/ModelMenuList。
 * 顶部 ModelPicker(features/chat/ModelPicker) 也复用同一份。
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { ChevronDown, Cpu } from 'lucide-react';
import {
  Button,
  cn,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  Pill,
} from '@chayuan/ui';
import { models, type RawModelItem } from '@chayuan/api';
import { logEvent, Events } from '@chayuan/observability';
import { useComposerStore, useComposerS } from '../../store/composer';
import { useLocalRuntimeStore } from '../../store/localRuntime';
import { LOCAL_PLATFORM_KEY, LOCAL_PLATFORM_LABEL, displayLabel } from './ModelMenuList';
import { useLogosManifest, useResolveLogo } from '../catalog/useLogos';
import { useTranslation } from '../../i18n';
import { ModelMenuList } from './ModelMenuList';
import { CHAT_CAPABILITIES, inferCapability, isChatCapability } from './modelCapabilityChip';

const Logo: React.FC<{ src?: string; name: string }> = ({ src, name }) => {
  const [errored, setErrored] = React.useState(false);
  if (src && !errored) {
    return (
      <img
        src={src}
        alt={name}
        className="h-3.5 w-3.5 flex-shrink-0 rounded-sm object-contain"
        loading="lazy"
        onError={() => setErrored(true)}
      />
    );
  }
  return (
    <span
      className={cn(
        'h-3.5 w-3.5 flex flex-shrink-0 items-center justify-center rounded-sm',
        'bg-[var(--cy-brand-50)] text-[9px] font-bold text-[var(--cy-brand-600)]',
      )}
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
};

export interface ComposerModelPillProps {
  menuMaxHeight?: number;
  contentClassName?: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  iconOnly?: boolean;
}

export const ComposerModelPill: React.FC<ComposerModelPillProps> = ({
  menuMaxHeight = 384,
  contentClassName,
  side = 'bottom',
  iconOnly = false,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const modelId = useComposerS((s) => s.modelId);
  const platformName = useComposerS((s) => s.platformName);
  const setModel = useComposerS((s) => s.setModel);
  // 绑定了知识库 → 知识问答必走 LLM 链路,模型选择收窄到对话类(chat/vl)
  const kbBound = useComposerS((s) => s.selectedKuIds.length > 0);
  const [open, setOpen] = React.useState(false);

  // 单机/桌面版关闭 check_access:每个不可达厂商会触发 OpenAI client 4 次重试
  // (~5s),多厂商累加可让下拉打开几十秒看着像"没加载"。改用快速读+按需重试,
  // 真正不可达的模型会在调用时由 chat 路径报错(可定位的明确错误)。
  //
  // 不再限 model_type='llm' — 全模态选择器要让用户在同一个下拉里挑 t2i / tts /
  // asr / t2v / image_edit 等模型。ModelMenuList 顶部有 capability chip 行筛选,
  // 每条前面有色块,用户能一眼区分"这是对话 / 文生图 / 语音合成"。
  const { data = [] } = useQuery<RawModelItem[]>({
    queryKey: ['v1.models', { type: 'all' }],
    queryFn: () => models.list(),
    staleTime: 60_000,
    retry: 0,
  });
  // 本地 LLM runtime ready 时,顶部 prepend 一个合成 model 让 ModelMenuList 渲染「本地」分组
  const localStatus = useLocalRuntimeStore((s) => s.status);
  const modelsWithLocal = React.useMemo<RawModelItem[]>(() => {
    if (!localStatus || localStatus.state !== 'ready' || !localStatus.model_id) {
      return data;
    }
    const localItem: RawModelItem = {
      id: localStatus.model_id,
      object: 'model',
      // 后端 SidecarRuntimeManager 注册的 platform_name 是 `local-<capability>`(必须唯一,
      // 不能重名),这里 chat sidecar 对应 `local-chat`。前端用这个 namespace 后,
      // get_model_info 才能在 MODEL_PLATFORMS 找到对应 sidecar endpoint;
      // 老的 'local' 单值 namespace 永远找不到 → ModelNotConfigured。
      platform_name: 'local-chat',
      platform_display_name: LOCAL_PLATFORM_LABEL,
      model_type: 'llm',
      available: true,
    };
    return [localItem, ...data];
  }, [data, localStatus]);

  useLogosManifest();
  const resolveLogo = useResolveLogo();

  // 81 题:跨平台同名时,用 (id, platform_name) 复合匹配
  // 用 modelsWithLocal:本地合成项也算"已知模型",选中后不会被 96 题 effect 复位
  const current = modelsWithLocal.find(
    (m) => m.id === modelId && (!platformName || m.platform_name === platformName),
  );

  // 96 题:store 里的 modelId 必须在真实 /v1/models 里才视为有效
  // 不在的(用户决策"凭空出现 claude" → 上次硬编码 default)→ 自动清空 + 默认选第一个
  React.useEffect(() => {
    if (!modelId || modelsWithLocal.length === 0) return;
    if (!current) {
      const first = modelsWithLocal.find((m) => m.available !== false) ?? modelsWithLocal[0];
      if (first) {
        setModel(first.id, first.platform_name);
      }
    }
  }, [modelId, modelsWithLocal, current, setModel]);

  // KB 防呆(反向场景):绑定知识库时,当前模型必须是对话类。用户若先选了非
  // 对话模型(asr/tts/t2i…)再绑 KB,或持久化恢复出"非对话模型 + KB"的组合,
  // 这里自动切到第一个可用对话模型 —— 否则知识问答发送时才会报
  // ModelNotConfigured / 404。正向场景(KB 已绑时再选模型)由下拉的
  // allowCapabilities 过滤兜住,用户根本选不到非对话模型。
  React.useEffect(() => {
    if (!kbBound || !current) return;
    if (isChatCapability(inferCapability(current))) return;
    const fallback = modelsWithLocal.find(
      (m) => m.available !== false && isChatCapability(inferCapability(m)),
    );
    if (fallback) setModel(fallback.id, fallback.platform_name);
  }, [kbBound, current, modelsWithLocal, setModel]);

  const currentLogo = resolveLogo(current?.platform_name, current?.id);
  // pill 上显示用 displayLabel(本地模型短化:Qwen3-4B-Instruct-2507 而不是
  // models/bundled/chat/Qwen3-4B-Instruct-2507-GGUF);完整 id 仍走 title hover。
  const currentLabel = current ? displayLabel(current) : (modelId || '— 未选 —');
  const compact = true || iconOnly;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Pill
          tone="outline"
          size="sm"
          aria-label={t('chat.modelSelector')}
          title={`当前模型：${currentLabel}${current && current.id !== currentLabel ? `\n${current.id}` : ''}`}
          className={cn(
            'shrink-0',
            compact
              ? 'h-8 w-8 justify-center px-0'
              : 'max-[560px]:h-8 max-[560px]:w-8 max-[560px]:justify-center max-[560px]:px-0',
          )}
        >
          <Logo src={currentLogo} name={current?.platform_name ?? current?.id ?? modelId} />
          {!compact && (
            <>
              <span className="max-w-[160px] truncate max-[560px]:hidden">{currentLabel}</span>
              <ChevronDown className="h-3 w-3 opacity-60 max-[560px]:hidden" />
            </>
          )}
        </Pill>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        side={side}
        sideOffset={8}
        collisionPadding={12}
        className={cn('z-[120] w-80 overflow-hidden p-0', contentClassName)}
      >
        <ModelMenuList
          models={modelsWithLocal}
          currentId={modelId}
          currentPlatform={platformName}
          allowCapabilities={kbBound ? CHAT_CAPABILITIES : undefined}
          resolveLogo={resolveLogo}
          emptyLabel={t('chat.noModelAvailable')}
          emptyAction={
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setOpen(false);
                void navigate({ to: '/marketplace' });
              }}
            >
              <Cpu className="h-3.5 w-3.5" />
              去模型广场
            </Button>
          }
          maxHeight={menuMaxHeight}
          onPick={(m) => {
            if (m.available === false) return;
            // 77 题:传 platform_name 让 server 精确路由
            setModel(m.id, m.platform_name);
            logEvent(Events.ComposerModelChange, {
              metadata: { modelId: m.id, platform: m.platform_name },
            });
            setOpen(false);
          }}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
