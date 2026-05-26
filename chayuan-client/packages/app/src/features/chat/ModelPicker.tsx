/**
 * ChatPage 顶栏的模型选择器(Button 样式)。
 *
 * 与 features/composer/ComposerModelPill 共用同一份 ModelMenuList,
 * 拿到分组折叠 / 搜索 / 当前组默认展开 等行为。
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Cpu } from 'lucide-react';
import { models, type RawModelItem } from '@chayuan/api';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@chayuan/ui';
import { useComposerStore, useComposerS } from '../../store/composer';
import { logEvent, Events } from '@chayuan/observability';
import { useLogosManifest, useResolveLogo } from '../catalog/useLogos';
import { ModelMenuList } from '../composer/ModelMenuList';
import { CHAT_CAPABILITIES } from '../composer/modelCapabilityChip';

const Logo: React.FC<{ src?: string; name: string }> = ({ src, name }) => {
  const [errored, setErrored] = React.useState(false);
  if (src && !errored) {
    return (
      <img
        src={src}
        alt={name}
        className="h-4 w-4 flex-shrink-0 rounded-sm object-contain"
        loading="lazy"
        onError={() => setErrored(true)}
      />
    );
  }
  return (
    <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-sm bg-primary/10 text-[9px] font-bold text-primary">
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
};

/**
 * 96 题:支持可选 props 覆盖默认全局 store。
 *   - 单道(老 ChatPage)用法:不传 props,走 useComposerStore(行为不变,零回归)
 *   - 多道(模型对抗 Lane)用法:传 value/onChange 走 lane-local model
 */
interface ModelPickerProps {
  value?: { modelId: string; platformName?: string };
  onChange?: (modelId: string, platformName?: string) => void;
}

export const ModelPicker: React.FC<ModelPickerProps> = ({ value, onChange }) => {
  const navigate = useNavigate();
  const storeModelId = useComposerS((s) => s.modelId);
  const storePlatformName = useComposerS((s) => s.platformName);
  const storeSetModel = useComposerS((s) => s.setModel);
  const modelId = value?.modelId ?? storeModelId;
  const platformName = value?.platformName ?? storePlatformName;
  const setModel = onChange ?? storeSetModel;
  // 绑定知识库时模型选择收窄到对话类(跟 ComposerModelPill 一致的防呆)
  const kbBound = useComposerS((s) => s.selectedKuIds.length > 0);
  const [open, setOpen] = React.useState(false);

  // 与 ComposerModelPill 同步:快速读 + 不限 type — 让用户在顶栏也能选 t2i/tts 等
  // 全模态模型;ModelMenuList 内部有 capability chip 行筛选 + 每行色块,不会乱。
  const { data = [] } = useQuery<RawModelItem[]>({
    queryKey: ['v1.models', { type: 'all' }],
    queryFn: () => models.list(),
    staleTime: 60_000,
  });
  useLogosManifest();
  const resolveLogo = useResolveLogo();

  // 81 题:跨平台同名时,用 (id, platform_name) 复合匹配;没 platform 时退回旧行为
  const current = data.find(
    (m) => m.id === modelId && (!platformName || m.platform_name === platformName),
  );
  const currentLogo = resolveLogo(current?.platform_name, current?.id);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 rounded-full px-0"
          title={`当前模型：${current?.id ?? modelId}`}
          aria-label="选择模型"
        >
          <Logo src={currentLogo} name={current?.platform_name ?? current?.id ?? modelId} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80 overflow-hidden p-0">
        <ModelMenuList
          models={data}
          currentId={modelId}
          currentPlatform={platformName}
          allowCapabilities={kbBound ? CHAT_CAPABILITIES : undefined}
          resolveLogo={resolveLogo}
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
          onPick={(m) => {
            if (m.available === false) return;
            // 77 题:传 platform_name 让 server 精确路由,避免跨平台同名 model_id 被覆盖
            setModel(m.id, m.platform_name);
            logEvent(Events.ComposerModelChange, {
              metadata: { modelId: m.id, platformName: m.platform_name },
            });
            setOpen(false);
          }}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
