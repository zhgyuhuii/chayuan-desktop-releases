/**
 * 本地 LLM runtime 配置表单。
 *
 * 字段:
 *   - host (127.0.0.1 / 自定义,expose_lan=true 时强制 0.0.0.0)
 *   - port (1024-65535,默认 62582)
 *   - api_key (可选,设置后调用方需带 Authorization: Bearer)
 *   - expose_lan (开关,默认关;开启提示 LAN 风险)
 *   - preload_on_startup (开关,默认开)
 *
 * 不直接调 store.saveConfig:onSubmit 由父组件包一层,可加确认对话框 / 触发 restart。
 */

import * as React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Input } from '@chayuan/ui';
import type { LocalRuntimeSettings, LocalRuntimeSettingsPatch } from '@chayuan/api';

export interface LocalRuntimeConfigFormProps {
  /** 初始值 (从 store.config 来) */
  value: LocalRuntimeSettings;
  /** 提交时调用:父组件决定是否弹确认 / 触发 restart */
  onSubmit(patch: LocalRuntimeSettingsPatch): void | Promise<void>;
  /** 表单是否禁用 (saveConfig pending 时) */
  disabled?: boolean;
}

export const LocalRuntimeConfigForm: React.FC<LocalRuntimeConfigFormProps> = ({
  value,
  onSubmit,
  disabled,
}) => {
  const [draft, setDraft] = React.useState<LocalRuntimeSettings>(value);
  React.useEffect(() => {
    setDraft(value);
  }, [value]);

  const setField = <K extends keyof LocalRuntimeSettings>(
    key: K,
    v: LocalRuntimeSettings[K],
  ) => setDraft((s) => ({ ...s, [key]: v }));

  const portError =
    Number.isFinite(draft.port) && (draft.port < 1024 || draft.port > 65535)
      ? '端口范围 1024-65535'
      : null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (portError) return;
    // 仅发改动字段
    const patch: LocalRuntimeSettingsPatch = {};
    for (const k of Object.keys(draft) as (keyof LocalRuntimeSettings)[]) {
      if (draft[k] !== value[k]) (patch as Record<string, unknown>)[k] = draft[k];
    }
    if (Object.keys(patch).length === 0) return;
    void onSubmit(patch);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <Field label="启动时预热" help="桌面启动后自动拉起本地模型(关闭后首次聊天 lazy start)">
        <Toggle
          checked={draft.preload_on_startup}
          onCheckedChange={(v) => setField('preload_on_startup', v)}
          disabled={disabled}
        />
      </Field>

      <Field label="Host" help="expose_lan 开启时自动用 0.0.0.0">
        <Input
          value={draft.expose_lan ? '0.0.0.0' : draft.host}
          onChange={(e) => setField('host', e.target.value)}
          disabled={disabled || draft.expose_lan}
          placeholder="127.0.0.1"
          className="h-8"
        />
      </Field>

      <Field label="端口" help="默认 62582;改后需 restart 生效">
        <div className="flex flex-col gap-1">
          <Input
            type="number"
            value={String(draft.port)}
            onChange={(e) => setField('port', Number(e.target.value) || 0)}
            disabled={disabled}
            min={1024}
            max={65535}
            className="h-8 max-w-[120px]"
          />
          {portError && <span className="text-xs text-rose-600">{portError}</span>}
        </div>
      </Field>

      <Field
        label="API Key"
        help="留空 = 任何本机进程可调;设置后调用方需带 Authorization: Bearer <key>"
      >
        <Input
          type="password"
          value={draft.api_key}
          onChange={(e) => setField('api_key', e.target.value)}
          disabled={disabled}
          placeholder="留空 = 不校验"
          className="h-8"
        />
      </Field>

      <Field
        label="局域网暴露"
        help={draft.expose_lan ? '⚠️ 同网络内任意机器可访问,务必配 API Key' : '只允许本机访问 (推荐)'}
      >
        <Toggle
          checked={draft.expose_lan}
          onCheckedChange={(v) => setField('expose_lan', v)}
          disabled={disabled}
        />
      </Field>

      {draft.expose_lan && !draft.api_key.trim() && (
        <div className="flex items-start gap-2 rounded-md border border-amber-400/40 bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span>LAN 开启但 API Key 为空:任何同网段机器都能调用本机模型。建议至少设置一个 key。</span>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button type="submit" disabled={disabled || !!portError} size="sm">
          保存
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => setDraft(value)}
        >
          重置
        </Button>
      </div>
    </form>
  );
};

const Field: React.FC<{ label: string; help?: string; children: React.ReactNode }> = ({
  label,
  help,
  children,
}) => (
  <div className="grid grid-cols-[160px_1fr] items-start gap-3">
    <div className="pt-1">
      <div className="text-sm font-medium text-[var(--cy-text-primary)]">{label}</div>
      {help && <div className="mt-0.5 text-xs text-[var(--cy-text-tertiary)]">{help}</div>}
    </div>
    <div>{children}</div>
  </div>
);

const Toggle: React.FC<{
  checked: boolean;
  onCheckedChange(v: boolean): void;
  disabled?: boolean;
}> = ({ checked, onCheckedChange, disabled }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    onClick={() => !disabled && onCheckedChange(!checked)}
    disabled={disabled}
    className={
      'inline-flex h-5 w-9 items-center rounded-full border transition-colors ' +
      (checked
        ? 'border-emerald-500/50 bg-emerald-500'
        : 'border-zinc-400/40 bg-zinc-400/30') +
      (disabled ? ' opacity-50' : '')
    }
  >
    <span
      className={
        'inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ' +
        (checked ? 'translate-x-[18px]' : 'translate-x-[2px]')
      }
    />
  </button>
);
