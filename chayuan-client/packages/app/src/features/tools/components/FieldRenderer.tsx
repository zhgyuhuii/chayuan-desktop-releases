/**
 * 工具字段渲染器 — 把 catalog 里的 ``ToolField`` 映射成对应的 widget。
 *
 * 复用点:
 *  - 内置工具 ``ToolConfigDialog`` 用它渲染 catalog.fields
 *  - 自定义工具 ``CustomToolForm``(P3)用它渲染参数行
 *  - OpenAPI 导入预览(P4)的"参数清单"也走同一个渲染管线
 *
 * widget 全集:
 *   text / textarea / number / password / switch / select / model_select
 *
 * model_select 暂时降级为 text(P5 接入模型广场状态再来开)。
 */
import * as React from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Input, Switch, Textarea, cn } from '@chayuan/ui';
import type { ToolField } from '@chayuan/api';

export interface FieldRendererProps {
  field: ToolField;
  value: unknown;
  onChange(next: unknown): void;
  disabled?: boolean;
}

export const FieldRenderer: React.FC<FieldRendererProps> = ({ field, value, onChange, disabled }) => {
  const help = field.help || '';
  const placeholder = field.placeholder || '';

  return (
    <div className="space-y-1.5">
      <label className="flex items-baseline gap-2 text-xs font-medium text-[var(--cy-text-primary)]">
        <span>{field.label || field.path}</span>
        <code className="text-[10px] font-mono font-normal text-[var(--cy-text-tertiary)]">{field.path}</code>
      </label>
      <FieldInput field={field} value={value} onChange={onChange} disabled={disabled} placeholder={placeholder} />
      {help && <p className="text-[10px] leading-snug text-[var(--cy-text-tertiary)]">{help}</p>}
    </div>
  );
};

const FieldInput: React.FC<{
  field: ToolField;
  value: unknown;
  onChange(next: unknown): void;
  disabled?: boolean;
  placeholder?: string;
}> = ({ field, value, onChange, disabled, placeholder }) => {
  switch (field.widget) {
    case 'switch':
      return (
        <Switch
          checked={Boolean(value)}
          onCheckedChange={(v) => onChange(Boolean(v))}
          disabled={disabled}
        />
      );

    case 'number':
      return (
        <Input
          type="number"
          value={value === null || value === undefined ? '' : String(value)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          step={field.step ?? undefined}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              onChange(null);
              return;
            }
            const n = Number(raw);
            onChange(Number.isFinite(n) ? n : raw);
          }}
          className="h-8 text-xs"
        />
      );

    case 'password':
      return <PasswordInput field={field} value={value} onChange={onChange} disabled={disabled} placeholder={placeholder} />;

    case 'textarea':
      return (
        <Textarea
          value={value === null || value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          rows={4}
          className="text-xs font-mono leading-relaxed"
        />
      );

    case 'select':
      return (
        <select
          value={value === null || value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className={cn(
            'h-8 w-full rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs',
            'focus:border-[var(--cy-brand-500)] focus:outline-none focus:ring-1 focus:ring-[var(--cy-brand-500)]',
            disabled && 'cursor-not-allowed opacity-60',
          )}
        >
          {(field.choices ?? []).map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      );

    case 'model_select':
      // P5 接入模型广场;暂时降级为文本输入,提示用户填模型 id
      return (
        <Input
          type="text"
          value={value === null || value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder || `模型 id(${field.model_type ?? 'llm'})`}
          disabled={disabled}
          className="h-8 text-xs font-mono"
        />
      );

    case 'text':
    default:
      return (
        <Input
          type="text"
          value={value === null || value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className="h-8 text-xs"
        />
      );
  }
};

/** 密码框 — eye toggle 默认开启;``field.password_toggle === false`` 时关闭可见切换 */
const PasswordInput: React.FC<{
  field: ToolField;
  value: unknown;
  onChange(v: unknown): void;
  disabled?: boolean;
  placeholder?: string;
}> = ({ field, value, onChange, disabled, placeholder }) => {
  const [reveal, setReveal] = React.useState(false);
  const showToggle = field.password_toggle !== false;
  return (
    <div className="relative">
      <Input
        type={reveal ? 'text' : 'password'}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="h-8 pr-8 text-xs font-mono"
      />
      {showToggle && (
        <button
          type="button"
          onClick={() => setReveal((v) => !v)}
          tabIndex={-1}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]"
          aria-label={reveal ? '隐藏' : '显示'}
        >
          {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        </button>
      )}
    </div>
  );
};
