/**
 * 自定义 HTTP 工具的"新建 / 编辑 / 删除"弹窗。
 *
 * 三种模式由 props 决定:
 *   - mode="create" + initial=undefined → 空白表单,save 走 createCustom
 *   - mode="edit"   + initial=existing  → 回填,save 走 updateCustom,有删除按钮
 *
 * 表单分四块,符合"先填谁、再填谁"的认知顺序:
 *   1. 基础(name / title / description)
 *   2. 请求(method / url / headers / body_template / timeout)
 *   3. 参数(ParamsEditor 动态行)
 *   4. 启用(switch)
 *
 * 校验在前端做一次"明显错"的兜底(name 标识符 / url 协议 / params name 唯一),
 * 后端 ``custom_tools_store.validate_spec`` 仍是真源,失败提示走 ErrorDialog。
 */
import * as React from 'react';
import { Trash2, Save } from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogDescription, DialogTitle, Input, Switch, Textarea, cn,
} from '@chayuan/ui';
import type { CustomToolParam, CustomToolSpec } from '@chayuan/api';
import { notifyInfo, notifySuccess, reportError } from '../../../store/errorDialog';
import { getPlatform } from '@chayuan/platform-shared';
import {
  useCreateCustomTool, useDeleteCustomTool, useUpdateCustomTool,
} from '../hooks';
import { ParamsEditor } from './ParamsEditor';
import { HeadersEditor } from './HeadersEditor';

export type CustomToolFormMode = 'create' | 'edit';

export interface CustomToolFormDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  mode: CustomToolFormMode;
  initial?: CustomToolSpec | null;
}

const HTTP_METHODS: ReadonlyArray<CustomToolSpec['method']> = [
  'GET', 'POST', 'PUT', 'PATCH', 'DELETE',
];

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,79}$/;

function blank(): CustomToolSpec {
  return {
    name: '',
    title: '',
    description: '',
    method: 'GET',
    url: '',
    params: [],
    headers: {},
    body_template: null,
    timeout: 15,
    enabled: true,
  };
}

export const CustomToolFormDialog: React.FC<CustomToolFormDialogProps> = ({
  open, onOpenChange, mode, initial,
}) => {
  const create = useCreateCustomTool();
  const upd = useUpdateCustomTool();
  const del = useDeleteCustomTool();

  const [draft, setDraft] = React.useState<CustomToolSpec>(() => initial ?? blank());

  React.useEffect(() => {
    if (open) setDraft(initial ?? blank());
  }, [open, initial]);

  const set = <K extends keyof CustomToolSpec>(k: K, v: CustomToolSpec[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const showBody = ['POST', 'PUT', 'PATCH'].includes(draft.method);

  const validate = (): string | null => {
    if (!NAME_RE.test(draft.name))
      return 'name 必须是合法 Python 标识符(字母/数字/下划线,字母或下划线开头,≤80 字符)';
    if (!draft.description.trim())
      return 'description 不能为空(LLM 选工具时会读这段)';
    if (!/^https?:\/\//.test(draft.url))
      return 'url 必须以 http:// 或 https:// 开头';
    const seen = new Set<string>();
    for (const p of draft.params) {
      if (!p.name || !NAME_RE.test(p.name))
        return `参数名非法:${p.name || '(空)'}`;
      if (seen.has(p.name)) return `参数名重复:${p.name}`;
      seen.add(p.name);
    }
    return null;
  };

  const onSave = async () => {
    const err = validate();
    if (err) {
      notifyInfo('请检查表单', err);
      return;
    }
    try {
      const spec: CustomToolSpec = {
        ...draft,
        name: draft.name.trim(),
        title: (draft.title || draft.name).trim(),
        method: draft.method.toUpperCase() as CustomToolSpec['method'],
      };
      if (mode === 'create') {
        await create.mutateAsync(spec);
        notifySuccess('已创建', `自定义工具 ${spec.name} 已生效`);
      } else {
        if (!initial) return;
        await upd.mutateAsync({ name: initial.name, spec });
        notifySuccess('已保存', `自定义工具 ${spec.name} 已更新`);
      }
      onOpenChange(false);
    } catch (e) {
      reportError(e, mode === 'create' ? '创建自定义工具失败' : '保存自定义工具失败');
    }
  };

  const onDelete = async () => {
    if (!initial) return;
    const ok = await getPlatform().dialog.confirm(
      `确定要删除自定义工具「${initial.title || initial.name}」吗?\n删除后 LLM 将无法再调用,且无法恢复。`,
      { title: '删除自定义工具' },
    );
    if (!ok) return;
    try {
      await del.mutateAsync(initial.name);
      notifySuccess('已删除', `自定义工具 ${initial.name} 已移除`);
      onOpenChange(false);
    } catch (e) {
      reportError(e, '删除自定义工具失败');
    }
  };

  const saving = create.isPending || upd.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] w-[min(820px,94vw)] flex-col overflow-hidden p-0">
        <header className="border-b border-[var(--cy-border-subtle)] px-5 py-4">
          <DialogTitle className="text-base font-semibold text-[var(--cy-text-primary)]">
            {mode === 'create' ? '添加自定义 API 工具' : `编辑工具:${initial?.title || initial?.name}`}
          </DialogTitle>
          <DialogDescription className="mt-1 text-xs text-[var(--cy-text-secondary)]">
            填写 HTTP 端点,LLM 在对话时会按你写的描述决定是否调用。所有字段都会写入 ``custom_tools.yaml``。
          </DialogDescription>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {/* ── 1. 基础 ──────────────────────────────── */}
          <Section title="基础信息">
            <Field label="name" hint="LLM 调用时的标识符,合法 Python 标识符">
              <Input
                value={draft.name}
                disabled={mode === 'edit'}
                placeholder="my_query_user"
                onChange={(e) => set('name', e.target.value)}
                className="h-8 font-mono text-xs"
              />
            </Field>
            <Field label="title" hint="UI 卡片展示用">
              <Input
                value={draft.title}
                placeholder="查询用户"
                onChange={(e) => set('title', e.target.value)}
                className="h-8 text-xs"
              />
            </Field>
            <Field
              label="description"
              hint="LLM 选工具时读的提示词,描述清楚「干什么用 / 适合什么场景 / 输入输出」"
              span={2}
            >
              <Textarea
                value={draft.description}
                placeholder="根据用户 ID 查询用户详情。返回包括姓名、邮箱、注册时间等基础信息。"
                onChange={(e) => set('description', e.target.value)}
                rows={3}
                className="text-xs leading-relaxed"
              />
            </Field>
          </Section>

          {/* ── 2. 请求 ──────────────────────────────── */}
          <Section title="HTTP 请求">
            <Field label="method">
              <select
                value={draft.method}
                onChange={(e) => set('method', e.target.value as CustomToolSpec['method'])}
                className={cn(
                  'h-8 w-full rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs',
                  'focus:border-[var(--cy-brand-500)] focus:outline-none focus:ring-1 focus:ring-[var(--cy-brand-500)]',
                )}
              >
                {HTTP_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
            <Field label="timeout(秒)">
              <Input
                type="number"
                min={1}
                value={String(draft.timeout)}
                onChange={(e) => set('timeout', Math.max(1, Number(e.target.value) || 15))}
                className="h-8 text-xs"
              />
            </Field>
            <Field label="url" span={2} hint="path 参数用 {name} 占位,如 /users/{user_id}">
              <Input
                value={draft.url}
                placeholder="https://api.example.com/users/{user_id}"
                onChange={(e) => set('url', e.target.value)}
                className="h-8 font-mono text-xs"
              />
            </Field>
            <Field label="Headers" span={2} hint="静态键值对,如 Authorization、Content-Type">
              <HeadersEditor value={draft.headers} onChange={(v) => set('headers', v)} />
            </Field>
            {showBody && (
              <Field
                label="body_template (JSON)"
                span={2}
                hint='POST/PUT/PATCH 时拼请求体的模板,占位符用 {param_name}。例:{"id": "{user_id}"}'
              >
                <Textarea
                  value={draft.body_template ?? ''}
                  placeholder={'{"id": "{user_id}"}'}
                  onChange={(e) => set('body_template', e.target.value || null)}
                  rows={3}
                  className="font-mono text-xs"
                />
              </Field>
            )}
          </Section>

          {/* ── 3. 参数 ──────────────────────────────── */}
          <Section title="LLM 调用参数(动态)">
            <div className="col-span-2">
              <ParamsEditor value={draft.params} onChange={(v) => set('params', v)} />
            </div>
          </Section>

          {/* ── 4. 启用 ──────────────────────────────── */}
          <div className="flex items-center gap-3 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
            <Switch
              checked={draft.enabled}
              onCheckedChange={(v) => set('enabled', Boolean(v))}
            />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-[var(--cy-text-primary)]">启用此工具</p>
              <p className="text-[10px] text-[var(--cy-text-tertiary)]">
                启用后,工具立即注册到运行时,LLM 在对话中可调用
              </p>
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-5 py-3">
          {mode === 'edit' && initial ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive hover:bg-red-50"
              onClick={() => void onDelete()}
              disabled={del.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" /> 删除
            </Button>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button size="sm" onClick={() => void onSave()} disabled={saving}>
              <Save className="h-3.5 w-3.5" />
              {saving ? '保存中…' : mode === 'create' ? '创建' : '保存'}
            </Button>
          </div>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

// ── 内部 layout 辅助 ────────────────────────────────────────────────

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section>
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--cy-text-tertiary)]">
      {title}
    </h3>
    <div className="grid gap-3 sm:grid-cols-2">{children}</div>
  </section>
);

const Field: React.FC<{
  label: string;
  hint?: string;
  span?: 1 | 2;
  children: React.ReactNode;
}> = ({ label, hint, span = 1, children }) => (
  <div className={cn('space-y-1', span === 2 && 'sm:col-span-2')}>
    <label className="text-xs font-medium text-[var(--cy-text-primary)]">{label}</label>
    {children}
    {hint && <p className="text-[10px] leading-snug text-[var(--cy-text-tertiary)]">{hint}</p>}
  </div>
);
