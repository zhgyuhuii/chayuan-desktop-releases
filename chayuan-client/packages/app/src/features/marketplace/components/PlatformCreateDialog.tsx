/**
 * 添加新厂商弹窗 —— 两栏布局,左边表单 + 右边实时预览。
 *
 * 适用场景:
 *   - catalog 里没有的自部署 OpenAI 兼容服务(如自家 vLLM / OneAPI 实例);
 *   - 需要建多个同类型厂商(如两套不同 base_url 的 oneapi)。
 *
 * 布局:
 *   - 桌面 ≥md:左 表单 / 右 实时预览卡片;两列等宽,夹一道分隔线;
 *   - 移动 <md:单列堆叠,预览贴在表单下方。
 *
 * 必填:platform_name(唯一)
 * 选填:platform_type / api_base_url / api_key / 描述 / Logo URL
 *
 * 设计:把"添加 + 详细编辑"分两步,降低单次表单认知负担;新建后调用 onCreated(name) 让父级
 * 立即弹起 SettingsDialog 让用户继续完善 + 测试。
 */

import { Button, Dialog, DialogContent, DialogDescription, DialogTitle, Input, cn } from '@chayuan/ui';
import { Box, Plus, Server, Sparkles, Wifi } from 'lucide-react';
import * as React from 'react';
import { notifySuccess, reportError } from '../../../store/errorDialog';
import { useCreatePlatform } from '../../../store/modelPlatform';
import { AssistantBrandLogo } from '../../../components/AssistantBrandLogo';

export interface PlatformCreateDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 新建成功后回调,父级可立即打开 SettingsDialog */
  onCreated?(name: string): void;
}

interface PlatformTypeOption {
  value: string;
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  defaultBase?: string;
}

const PLATFORM_TYPES: PlatformTypeOption[] = [
  {
    value: 'custom openai',
    label: 'OpenAI 兼容',
    hint: '通用,绝大多数云厂商 / 中转 / 自部署',
    icon: Box,
  },
  {
    value: 'openai',
    label: 'OpenAI',
    hint: '官方 OpenAI 接口',
    icon: Sparkles,
    defaultBase: 'https://api.openai.com/v1',
  },
  {
    value: 'oneapi',
    label: 'OneAPI',
    hint: '自部署 OneAPI / NewAPI 中转',
    icon: Server,
  },
  {
    value: 'ollama',
    label: 'Ollama',
    hint: '本地 Ollama daemon',
    icon: Server,
    defaultBase: 'http://127.0.0.1:11434/v1',
  },
  {
    value: 'xinference',
    label: 'Xinference',
    hint: '本地 Xinference',
    icon: Server,
    defaultBase: 'http://127.0.0.1:9997/v1',
  },
  {
    value: 'fastchat',
    label: 'FastChat / vLLM',
    hint: '本地 vLLM / FastChat',
    icon: Server,
    defaultBase: 'http://127.0.0.1:8000/v1',
  },
];

export const PlatformCreateDialog: React.FC<PlatformCreateDialogProps> = ({
  open,
  onOpenChange,
  onCreated,
}) => {
  const [name, setName] = React.useState('');
  const [type, setType] = React.useState<string>('custom openai');
  const [baseUrl, setBaseUrl] = React.useState('');
  const [apiKey, setApiKey] = React.useState('');
  const [desc, setDesc] = React.useState('');
  const create = useCreatePlatform();

  // 打开 / 关闭时清空
  React.useEffect(() => {
    if (!open) {
      setName('');
      setType('custom openai');
      setBaseUrl('');
      setApiKey('');
      setDesc('');
    }
  }, [open]);

  // 切类型时,如果用户还没填 base_url,自动给一个该类型的默认值
  const onSelectType = (next: string) => {
    setType(next);
    const opt = PLATFORM_TYPES.find((p) => p.value === next);
    if (opt?.defaultBase && !baseUrl.trim()) {
      setBaseUrl(opt.defaultBase);
    }
  };

  const trimmedName = name.trim();
  const canSubmit = !!trimmedName && !create.isPending;

  const onSubmit = async () => {
    if (!canSubmit) return;
    try {
      await create.mutateAsync({
        platform_name: trimmedName,
        platform_type: type,
        api_base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
        description: desc.trim(),
        enabled: true,
      });
      notifySuccess(`已新建厂商:${trimmedName}`);
      onOpenChange(false);
      onCreated?.(trimmedName);
    } catch (e) {
      reportError(e, '新建厂商失败');
    }
  };

  const previewName = trimmedName || '新厂商名称';
  const previewDesc = desc.trim() || '在右侧"实时预览"中查看卡片效果;保存后可在 ⚙️ 配置中继续完善';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="w-[92vw] max-w-3xl gap-0 overflow-hidden p-0"
        hideClose
      >
        <header className="border-b border-[var(--cy-border-subtle)] px-5 py-3">
          <DialogTitle asChild>
            <h2 className="text-base font-semibold text-[var(--cy-text-primary)]">添加厂商</h2>
          </DialogTitle>
          <DialogDescription className="text-[11px] text-[var(--cy-text-tertiary)]">
            创建后会立即生效;详细配置可在卡片 ⚙️ 内继续完善 / 测试 / 添加模型
          </DialogDescription>
        </header>

        <div className="grid gap-0 md:grid-cols-2">
          {/* 左侧:表单 */}
          <div className="space-y-3 px-5 py-4 md:border-r md:border-[var(--cy-border-subtle)]">
            <Field label="厂商名(唯一标识)" required hint="后续 API 调用以此为 platform_name">
              <Input
                value={name}
                autoFocus
                placeholder="例如:my-deepseek、internal-vllm-prod"
                onChange={(e) => setName(e.target.value)}
              />
            </Field>

            <Field label="平台类型">
              <div className="grid grid-cols-2 gap-1.5">
                {PLATFORM_TYPES.map((opt) => {
                  const Icon = opt.icon;
                  const active = type === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => onSelectType(opt.value)}
                      className={cn(
                        'group flex items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors',
                        active
                          ? 'border-[var(--cy-brand-400)] bg-[var(--cy-brand-50)]'
                          : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] hover:border-[var(--cy-brand-300)] hover:bg-[var(--cy-surface-1)]',
                      )}
                    >
                      <Icon
                        className={cn(
                          'mt-0.5 h-4 w-4 flex-shrink-0',
                          active
                            ? 'text-[var(--cy-brand-600)]'
                            : 'text-[var(--cy-text-tertiary)] group-hover:text-[var(--cy-text-secondary)]',
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            'block truncate text-xs font-medium',
                            active
                              ? 'text-[var(--cy-brand-700)]'
                              : 'text-[var(--cy-text-primary)]',
                          )}
                        >
                          {opt.label}
                        </span>
                        <span className="block truncate text-[10px] text-[var(--cy-text-tertiary)]">
                          {opt.hint}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </Field>

            <Field label="Base URL" hint="OpenAI 兼容路径,例如 https://api.deepseek.com/v1">
              <Input
                value={baseUrl}
                placeholder={
                  PLATFORM_TYPES.find((p) => p.value === type)?.defaultBase ??
                  'https://api.example.com/v1'
                }
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </Field>

            <Field label="API Key" hint="留空可保存后再补">
              <Input
                type="password"
                value={apiKey}
                placeholder="sk-..."
                onChange={(e) => setApiKey(e.target.value)}
              />
            </Field>

            <Field label="描述" hint="卡片副标题;可选">
              <Input
                value={desc}
                placeholder="自部署 vLLM 实例(prod 集群)"
                onChange={(e) => setDesc(e.target.value)}
              />
            </Field>
          </div>

          {/* 右侧:实时预览 */}
          <div className="px-5 py-4 bg-[var(--cy-surface-1)]">
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[var(--cy-text-tertiary)]">
              实时预览
            </p>
            <PreviewCard
              name={previewName}
              type={type}
              baseUrl={baseUrl.trim()}
              hasKey={!!apiKey.trim()}
              description={previewDesc}
            />
            <ul className="mt-3 space-y-1 text-[10px] text-[var(--cy-text-tertiary)]">
              <li className="flex items-start gap-1.5">
                <span aria-hidden className="mt-0.5 inline-block h-1 w-1 flex-shrink-0 rounded-full bg-[var(--cy-text-tertiary)]" />
                <span>保存后立即生效,无需重启服务</span>
              </li>
              <li className="flex items-start gap-1.5">
                <Wifi className="mt-0.5 h-2.5 w-2.5 flex-shrink-0 text-[var(--cy-text-tertiary)]" />
                <span>填了 API Key 后,在 ⚙️ 配置中失焦会自动拉取该厂商可用模型</span>
              </li>
              <li className="flex items-start gap-1.5">
                <Sparkles className="mt-0.5 h-2.5 w-2.5 flex-shrink-0 text-[var(--cy-text-tertiary)]" />
                <span>支持后续在配置内手动添加 / 删除 / AI 补全模型简介</span>
              </li>
            </ul>
          </div>
        </div>

        <footer className="flex justify-end gap-2 border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-5 py-3">
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button size="sm" onClick={onSubmit} disabled={!canSubmit}>
            {create.isPending ? (
              <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            创建
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, required, hint, children }) => (
  <div className="space-y-1">
    <div className="flex items-baseline justify-between">
      <label className="text-xs font-medium text-[var(--cy-text-secondary)]">
        {label}
        {required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {hint && <span className="text-[10px] text-[var(--cy-text-tertiary)]">{hint}</span>}
    </div>
    {children}
  </div>
);

/** 与 ProviderCard 视觉一致的极简预览卡 —— 不耦合真正的 ProviderCard 避免 props 漂移 */
const PreviewCard: React.FC<{
  name: string;
  type: string;
  baseUrl: string;
  hasKey: boolean;
  description: string;
}> = ({ name, type, baseUrl, hasKey, description }) => {
  const status: 'configured' | 'unconfigured' = baseUrl || hasKey ? 'configured' : 'unconfigured';
  return (
    <div className="relative flex min-h-[120px] flex-col gap-2 overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-4 pl-5">
      <span
        aria-hidden
        className={cn(
          'absolute left-0 top-0 h-full w-[3px]',
          status === 'configured' ? 'bg-emerald-400' : 'bg-amber-400',
        )}
      />
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md bg-[var(--cy-surface-2)] text-sm font-semibold text-[var(--cy-text-secondary)]">
          {(name[0] ?? 'N').toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-semibold text-[var(--cy-text-primary)]">{name}</p>
            {status === 'unconfigured' && (
              <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 ring-1 ring-amber-200/70">
                未配置
              </span>
            )}
            <span className="ml-auto rounded-full bg-[var(--cy-surface-2)] px-1.5 py-0.5 text-[9px] text-[var(--cy-text-tertiary)]">
              {type}
            </span>
          </div>
          <p className="line-clamp-2 mt-0.5 text-[11px] text-[var(--cy-text-tertiary)]">
            {description}
          </p>
        </div>
      </div>
      <p className="mt-auto truncate text-[10px] font-mono text-[var(--cy-text-tertiary)]">
        {baseUrl || '— 未填 base_url —'}
      </p>
    </div>
  );
};
