/**
 * 自定义工具参数行表格(动态增删行)。
 *
 * 每行字段:
 *   name(标识符)| type(下拉)| required(开关)| description | default | enum 编辑链接
 *
 * 复用:
 *   - P3 ``CustomToolFormDialog`` 直接用
 *   - P4 OpenAPI 导入预览的"参数清单"也用同一个组件,只是 ``readOnly``
 */
import * as React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button, Input, Switch, cn } from '@chayuan/ui';
import type { CustomToolParam } from '@chayuan/api';

export interface ParamsEditorProps {
  value: CustomToolParam[];
  onChange(next: CustomToolParam[]): void;
  /** P4 给预览模式用:只读 */
  readOnly?: boolean;
}

const PARAM_TYPES: ReadonlyArray<CustomToolParam['type']> = [
  'string', 'integer', 'number', 'boolean',
];

export const ParamsEditor: React.FC<ParamsEditorProps> = ({ value, onChange, readOnly }) => {
  const updateRow = (idx: number, patch: Partial<CustomToolParam>) => {
    onChange(value.map((p, i) => (i === idx ? { ...p, ...patch } : p)));
  };
  const removeRow = (idx: number) => onChange(value.filter((_, i) => i !== idx));
  const addRow = () =>
    onChange([
      ...value,
      { name: '', type: 'string', description: '', required: false, default: null, enum: [] },
    ]);

  return (
    <div className="space-y-2">
      {value.length === 0 ? (
        <p className="rounded-md border border-dashed border-[var(--cy-border-subtle)] py-4 text-center text-[10px] text-[var(--cy-text-tertiary)]">
          还没有参数。点下方「+ 添加参数」开始定义 LLM 调用时需要传的字段。
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border border-[var(--cy-border-subtle)]">
          <table className="w-full text-xs">
            <thead className="bg-[var(--cy-surface-1)] text-[10px] uppercase tracking-wider text-[var(--cy-text-tertiary)]">
              <tr>
                <th className="px-2 py-1.5 text-left font-medium">参数名</th>
                <th className="px-2 py-1.5 text-left font-medium">类型</th>
                <th className="px-2 py-1.5 text-left font-medium">必填</th>
                <th className="px-2 py-1.5 text-left font-medium">描述(LLM 读)</th>
                <th className="px-2 py-1.5 text-left font-medium">默认</th>
                <th className="w-8 px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--cy-border-subtle)]">
              {value.map((p, i) => (
                <tr key={i} className="bg-[var(--cy-surface-base)]">
                  <td className="px-2 py-1">
                    <Input
                      value={p.name}
                      placeholder="user_id"
                      disabled={readOnly}
                      onChange={(e) => updateRow(i, { name: e.target.value })}
                      className="h-7 font-mono text-[11px]"
                    />
                  </td>
                  <td className="px-2 py-1">
                    <select
                      value={p.type}
                      disabled={readOnly}
                      onChange={(e) => updateRow(i, { type: e.target.value as CustomToolParam['type'] })}
                      className={cn(
                        'h-7 w-full rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-1.5 text-[11px]',
                        'focus:border-[var(--cy-brand-500)] focus:outline-none focus:ring-1 focus:ring-[var(--cy-brand-500)]',
                        readOnly && 'cursor-not-allowed opacity-60',
                      )}
                    >
                      {PARAM_TYPES.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-1">
                    <Switch
                      checked={!!p.required}
                      disabled={readOnly}
                      onCheckedChange={(v) => updateRow(i, { required: Boolean(v) })}
                    />
                  </td>
                  <td className="px-2 py-1">
                    <Input
                      value={p.description ?? ''}
                      placeholder="用户 ID"
                      disabled={readOnly}
                      onChange={(e) => updateRow(i, { description: e.target.value })}
                      className="h-7 text-[11px]"
                    />
                  </td>
                  <td className="px-2 py-1">
                    <Input
                      value={p.default == null ? '' : String(p.default)}
                      placeholder="—"
                      disabled={readOnly}
                      onChange={(e) => updateRow(i, { default: e.target.value || null })}
                      className="h-7 font-mono text-[11px]"
                    />
                  </td>
                  <td className="px-1.5 py-1">
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={() => removeRow(i)}
                        aria-label="删除参数"
                        className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!readOnly && (
        <Button size="sm" variant="outline" onClick={addRow} className="h-7 text-[11px]">
          <Plus className="h-3 w-3" /> 添加参数
        </Button>
      )}
    </div>
  );
};
