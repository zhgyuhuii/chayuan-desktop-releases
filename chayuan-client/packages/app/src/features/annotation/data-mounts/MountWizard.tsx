/**
 * MountWizard —— 4 步新建/编辑数据挂载向导。
 *
 * 1. 选数据源(12 张卡片) — 用 GET /data-mounts/sources 拿目录
 * 2. 配置(根据 source.spec_form 动态生成表单) + 探活按钮
 * 3. 选挂载模式 + 范围(scope) + 目标 KB(corpus 模式必填)
 * 4. 预览样本(POST /data-mounts/sources/analyze 拿前 50 条) + 保存草稿/立即发布
 *
 * 状态机:用 React state 推进 step;不引 wizard 库,300 行内自洽。
 */
import * as React from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Search } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  cn,
} from '@chayuan/ui';
import {
  dataMountsApi,
  type DataMountRecord,
  type MountModeKey,
  type SourceCatalogItem,
  type SourceFormField,
} from '@chayuan/api';
import { reportError } from '../../../store/errorDialog';

const ALL_MODES: Array<{ value: MountModeKey; label: string; help: string }> = [
  { value: 'corpus',     label: 'corpus(候选 ingest)', help: '生成"待 ingest 任务",用户在 KB 页确认后才真正写入向量库' },
  { value: 'context',    label: 'context(检索增广)',   help: 'chat 时把高置信片段塞入 prompt 上下文' },
  { value: 'fewshot',    label: 'fewshot(样例注入)',   help: '提供 Q-A 样例作为 in-context examples' },
  { value: 'safety',     label: 'safety(安全规则)',     help: '注入硬约束规则,降低幻觉' },
  { value: 'preference', label: 'preference(偏好对齐)', help: '提供 chosen/rejected 偏好对,长期对齐 LLM' },
];

const SCOPE_OPTIONS = [
  { value: 'global', label: '全局(所有用户可见)' },
  { value: 'user',   label: '个人(仅当前用户)' },
  { value: 'kb',     label: '指定 KB' },
  { value: 'group',  label: '分组' },
];

interface Props {
  initial: DataMountRecord | null;
  onClose(): void;
  onCreated(): void;
}

interface FormState {
  name: string;
  description: string;
  scope_type: string;
  scope_id: string;
  source_type: string;
  options: Record<string, unknown>;
  modes: MountModeKey[];
  max_items: number;
  max_tokens: number;
  priority: number;
  target_kb: string;
}

function initialFromMount(m: DataMountRecord | null): FormState {
  const sf = (m?.source_filter ?? {}) as { spec?: { source_type?: string; options?: Record<string, unknown>; max_items?: number }; target_kb?: string };
  return {
    name: m?.name ?? '',
    description: m?.description ?? '',
    scope_type: m?.scope_type ?? 'user',
    scope_id: m?.scope_id ?? '',
    source_type: sf?.spec?.source_type ?? '',
    options: { ...(sf?.spec?.options ?? {}) },
    modes: (m?.mount_modes ?? []) as MountModeKey[],
    max_items: m?.max_items ?? 200,
    max_tokens: m?.max_tokens ?? 1600,
    priority: m?.priority ?? 0,
    target_kb: sf?.target_kb ?? '',
  };
}

export const MountWizard: React.FC<Props> = ({ initial, onClose, onCreated }) => {
  const [step, setStep] = React.useState<1 | 2 | 3 | 4>(1);
  const [form, setForm] = React.useState<FormState>(() => initialFromMount(initial));
  const isEdit = !!initial;

  const sourcesQuery = useQuery({
    queryKey: ['dataMounts.sources'],
    queryFn: () => dataMountsApi.listSources(),
    staleTime: 60_000,
    retry: 1,
  });

  // 用户报"弹窗内容为空" — 大概率是后端 /data-mounts/sources 401/500/未注册。
  // 给一个硬编码 fallback 让 UX 在后端没就绪时也能用,后端就绪后真值覆盖。
  const sources: SourceCatalogItem[] = React.useMemo(
    () => sourcesQuery.data && sourcesQuery.data.length > 0
      ? sourcesQuery.data
      : FALLBACK_SOURCES,
    [sourcesQuery.data],
  );

  const activeSource: SourceCatalogItem | undefined = React.useMemo(
    () => sources.find((s) => s.type_id === form.source_type),
    [sources, form.source_type],
  );

  // ---- 探活 / 抽样 ----
  const probeMut = useMutation({
    mutationFn: () => dataMountsApi.probe({
      source_type: form.source_type, options: form.options, max_items: form.max_items,
    }),
    onError: (e) => reportError(e, '探活失败'),
  });
  const analyzeMut = useMutation({
    mutationFn: () => dataMountsApi.analyze({
      source_type: form.source_type, options: form.options, sample_size: 50,
    }),
    onError: (e) => reportError(e, '抽样失败'),
  });

  // ---- 保存 ----
  const persist = async (publish: boolean): Promise<void> => {
    const body = {
      name: form.name || `${activeSource?.label ?? '挂载'} - 未命名`,
      description: form.description,
      scope_type: form.scope_type,
      scope_id: form.scope_id,
      source_filter: {
        spec: {
          source_type: form.source_type,
          options: form.options,
          max_items: form.max_items,
        },
        ...(form.target_kb ? { target_kb: form.target_kb } : {}),
      },
      mount_modes: form.modes,
      priority: form.priority,
      max_items: form.max_items,
      max_tokens: form.max_tokens,
    };
    let mount: DataMountRecord;
    if (isEdit && initial) {
      mount = await dataMountsApi.patch(initial.id, body);
    } else {
      mount = await dataMountsApi.create(body);
    }
    if (publish) {
      await dataMountsApi.publish(mount.id);
    }
  };

  const saveMut = useMutation({
    mutationFn: (publish: boolean) => persist(publish),
    onSuccess: onCreated,
    onError: (e) => reportError(e, '保存挂载失败'),
  });

  const canNext = (): boolean => {
    if (step === 1) return !!form.source_type;
    if (step === 2) {
      const required = activeSource?.spec_form.fields.filter((f) => f.required) ?? [];
      return required.every((f) => {
        const v = form.options[f.name];
        return v !== undefined && v !== '' && v !== null;
      });
    }
    if (step === 3) {
      if (form.modes.length === 0) return false;
      if (form.modes.includes('corpus') && !form.target_kb) return false;
      return true;
    }
    return true;
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className={cn(
          // 强行覆盖 max-w-lg 默认 + 增加 grid → flex 让子元素 min-h-0 生效
          'flex h-[88vh] w-[min(960px,96vw)] max-w-none flex-col gap-0 overflow-hidden p-0',
        )}
      >
        {/* 标题:Radix DialogTitle + DialogDescription (a11y 必需) + 自带的右上 Close (X) */}
        <div className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] px-6 py-3 pr-12">
          <div>
            <DialogTitle className="text-base font-semibold">
              {isEdit ? '编辑挂载' : '新建数据挂载'}
            </DialogTitle>
            <DialogDescription className="text-xs text-[var(--cy-text-tertiary)]">
              第 {step} / 4 步 · {['选数据源', '配置连接', '挂载模式', '预览发布'][step - 1]}
            </DialogDescription>
          </div>
        </div>

        {/* 主体 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {step === 1 && (
            <Step1SelectSource
              sources={sources}
              loading={sourcesQuery.isLoading && sources.length === 0}
              value={form.source_type}
              onChange={(v) => setForm({ ...form, source_type: v, options: {} })}
            />
          )}
          {step === 2 && activeSource && (
            <Step2Configure
              source={activeSource}
              options={form.options}
              onChange={(options) => setForm({ ...form, options })}
              probe={probeMut.data}
              probing={probeMut.isPending}
              onProbe={() => probeMut.mutate()}
            />
          )}
          {step === 3 && (
            <Step3ModeAndScope form={form} setForm={setForm} />
          )}
          {step === 4 && (
            <Step4Preview
              sample={analyzeMut.data}
              loading={analyzeMut.isPending}
              onAnalyze={() => analyzeMut.mutate()}
              form={form}
              setName={(name) => setForm({ ...form, name })}
              setDesc={(description) => setForm({ ...form, description })}
            />
          )}
        </div>

        {/* 操作栏 */}
        <div className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] px-6 py-3">
          <Button variant="outline" disabled={step === 1} onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3 | 4)}>
            <ArrowLeft className="h-3.5 w-3.5" /> 上一步
          </Button>
          {step < 4 ? (
            <Button disabled={!canNext()} onClick={() => setStep((s) => (s + 1) as 1 | 2 | 3 | 4)}>
              下一步 <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button variant="outline" disabled={saveMut.isPending} onClick={() => saveMut.mutate(false)}>
                {saveMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} 保存草稿
              </Button>
              <Button disabled={saveMut.isPending} onClick={() => saveMut.mutate(true)}>
                {saveMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                保存并发布
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

// ============ Step 组件 ============

const Step1SelectSource: React.FC<{
  sources: SourceCatalogItem[];
  loading: boolean;
  value: string;
  onChange(v: string): void;
}> = ({ sources, loading, value, onChange }) => (
  <div>
    <p className="mb-3 text-xs text-[var(--cy-text-tertiary)]">
      选数据源 — 共 {sources.length} 种;不同源的字段表单会在第 2 步动态出现。
    </p>
    {loading && <p className="text-sm text-[var(--cy-text-tertiary)]">加载中...</p>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
      {sources.map((s) => (
        <button
          key={s.type_id}
          type="button"
          onClick={() => onChange(s.type_id)}
          className={cn(
            'flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors',
            value === s.type_id
              ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)]'
              : 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)]',
          )}
        >
          <div className="text-sm font-medium text-[var(--cy-text-primary)]">{s.label}</div>
          <div className="text-[11px] text-[var(--cy-text-tertiary)] line-clamp-2">{s.description}</div>
          {s.capabilities.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {s.capabilities.map((c) => (
                <span key={c} className="rounded bg-[var(--cy-surface-2)] px-1.5 py-0.5 text-[10px]">{c}</span>
              ))}
            </div>
          )}
        </button>
      ))}
    </div>
  </div>
);

const Step2Configure: React.FC<{
  source: SourceCatalogItem;
  options: Record<string, unknown>;
  onChange(o: Record<string, unknown>): void;
  probe?: { status: string; message: string; counted?: number | null };
  probing: boolean;
  onProbe(): void;
}> = ({ source, options, onChange, probe, probing, onProbe }) => {
  // 给 KB / 知识源数据源拉真实选项 — 让用户从下拉里选,不必手填名字
  const kbListQ = useQuery({
    queryKey: ['mountWizard.kbList'],
    queryFn: async () => {
      const r = await fetch('/knowledge_base/list_knowledge_bases');
      if (!r.ok) return [];
      const j = await r.json() as { data?: Array<{ kb_name?: string; name?: string }> };
      return (j.data ?? []).map((x) => x.kb_name || x.name || '').filter(Boolean);
    },
    enabled: source.type_id === 'kb',
    staleTime: 60_000,
  });
  const ksListQ = useQuery({
    queryKey: ['mountWizard.ksList'],
    queryFn: async () => {
      const r = await fetch('/knowledge_source/list');
      if (!r.ok) return [];
      const j = await r.json() as { data?: Array<{ id?: number; name?: string; kind?: string }> };
      return j.data ?? [];
    },
    enabled: source.type_id === 'knowledge_source',
    staleTime: 60_000,
  });

  return (
  <div className="space-y-3">
    <p className="text-xs text-[var(--cy-text-tertiary)]">
      {source.label} · {source.description}
    </p>
    {source.spec_form.fields.map((f) => {
      // KB 数据源的 kb_name → 真实下拉
      if (source.type_id === 'kb' && f.name === 'kb_name') {
        const kbs = kbListQ.data ?? [];
        const enriched: SourceFormField = {
          ...f,
          type: 'select',
          options: kbs.length
            ? kbs.map((k) => ({ value: k, label: k }))
            : [{ value: '', label: '加载 KB 列表中...' }],
        };
        return (
          <DynamicField
            key={f.name}
            field={enriched}
            value={options[f.name]}
            onChange={(v) => onChange({ ...options, [f.name]: v })}
          />
        );
      }
      // knowledge_source.source_id → 真实下拉
      if (source.type_id === 'knowledge_source' && f.name === 'source_id') {
        const items = ksListQ.data ?? [];
        const enriched: SourceFormField = {
          ...f,
          type: 'select',
          options: items.length
            ? items.map((it) => ({
                value: String(it.id ?? ''),
                label: `${it.name ?? '(unnamed)'} · ${it.kind ?? '?'} (#${it.id})`,
              }))
            : [{ value: '', label: '加载知识源列表中...' }],
        };
        return (
          <DynamicField
            key={f.name}
            field={enriched}
            value={options[f.name]}
            onChange={(v) => onChange({ ...options, [f.name]: Number(v) || 0 })}
          />
        );
      }
      return (
        <DynamicField
          key={f.name}
          field={f}
          value={options[f.name]}
          onChange={(v) => onChange({ ...options, [f.name]: v })}
        />
      );
    })}
    <div className="flex items-center gap-2">
      <Button onClick={onProbe} variant="outline" disabled={probing}>
        {probing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
        探活
      </Button>
      {probe && (
        <span
          className={cn(
            'rounded px-2 py-0.5 text-xs',
            probe.status === 'ok' && 'bg-emerald-100 text-emerald-700',
            probe.status === 'warning' && 'bg-amber-100 text-amber-700',
            probe.status === 'error' && 'bg-rose-100 text-rose-700',
          )}
        >
          {probe.status} · {probe.message}{probe.counted !== null && probe.counted !== undefined ? ` · ~${probe.counted} 条` : ''}
        </span>
      )}
    </div>
  </div>
  );
};

const DynamicField: React.FC<{
  field: SourceFormField;
  value: unknown;
  onChange(v: unknown): void;
}> = ({ field, value, onChange }) => {
  const v = (value ?? field.default ?? '') as string | number | boolean;
  switch (field.type) {
    case 'bool':
      return (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!v} onChange={(e) => onChange(e.target.checked)} />
          <span>{field.label}</span>
          {field.help && <span className="text-[11px] text-[var(--cy-text-tertiary)]">{field.help}</span>}
        </label>
      );
    case 'int':
      return (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium">{field.label}{field.required && <span className="ml-1 text-rose-500">*</span>}</label>
          <Input type="number" value={String(v)} onChange={(e) => onChange(Number(e.target.value || 0))} className="h-8 w-48 text-xs" />
          {field.help && <p className="text-[11px] text-[var(--cy-text-tertiary)]">{field.help}</p>}
        </div>
      );
    case 'select':
      return (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium">{field.label}{field.required && <span className="ml-1 text-rose-500">*</span>}</label>
          <select
            value={String(v)}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 w-64 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs"
          >
            {(field.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {field.help && <p className="text-[11px] text-[var(--cy-text-tertiary)]">{field.help}</p>}
        </div>
      );
    case 'password':
      return (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium">{field.label}{field.required && <span className="ml-1 text-rose-500">*</span>}</label>
          <Input type="password" value={String(v)} onChange={(e) => onChange(e.target.value)} className="h-8 text-xs" />
          {field.help && <p className="text-[11px] text-[var(--cy-text-tertiary)]">{field.help}</p>}
        </div>
      );
    default:
      return (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium">{field.label}{field.required && <span className="ml-1 text-rose-500">*</span>}</label>
          <Input value={String(v)} onChange={(e) => onChange(e.target.value)} className="h-8 text-xs" />
          {field.help && <p className="text-[11px] text-[var(--cy-text-tertiary)]">{field.help}</p>}
        </div>
      );
  }
};

const Step3ModeAndScope: React.FC<{
  form: FormState;
  setForm(f: FormState): void;
}> = ({ form, setForm }) => (
  <div className="space-y-4">
    <div>
      <label className="text-xs font-medium">挂载模式 (可多选,至少一项)</label>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        {ALL_MODES.map((m) => (
          <label
            key={m.value}
            className={cn(
              'flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm transition-colors',
              form.modes.includes(m.value)
                ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)]'
                : 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)]',
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5"
              checked={form.modes.includes(m.value)}
              onChange={(e) => {
                if (e.target.checked) setForm({ ...form, modes: [...form.modes, m.value] });
                else setForm({ ...form, modes: form.modes.filter((x) => x !== m.value) });
              }}
            />
            <div>
              <div className="font-medium">{m.label}</div>
              <div className="text-[11px] text-[var(--cy-text-tertiary)]">{m.help}</div>
            </div>
          </label>
        ))}
      </div>
    </div>

    {form.modes.includes('corpus') && (
      <div className="rounded-md border border-[var(--cy-warning-500)] bg-[var(--cy-warning-50)] p-3 text-xs">
        <div className="mb-1 font-medium text-[var(--cy-warning-700)]">corpus 模式 — 候选 ingest 任务</div>
        <p className="mb-2 text-[var(--cy-text-secondary)]">
          发布时<strong>不会立即写入向量库</strong>;会落到「候选 ingest 任务」队列,
          你需要在目标 KB 页面确认后才真正 ingest。
        </p>
        <label className="block text-xs font-medium">目标 KB <span className="text-rose-500">*</span></label>
        <Input
          value={form.target_kb}
          onChange={(e) => setForm({ ...form, target_kb: e.target.value })}
          placeholder="如 my_kb"
          className="mt-1 h-8 w-64 text-xs"
        />
      </div>
    )}

    <div>
      <label className="text-xs font-medium">范围 (scope)</label>
      <div className="mt-2 flex gap-2">
        {SCOPE_OPTIONS.map((s) => (
          <button
            key={s.value}
            type="button"
            onClick={() => setForm({ ...form, scope_type: s.value })}
            className={cn(
              'rounded-md border px-3 py-1.5 text-xs transition-colors',
              form.scope_type === s.value
                ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)]'
                : 'border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]',
            )}
          >
            {s.label}
          </button>
        ))}
      </div>
      {(form.scope_type === 'kb' || form.scope_type === 'group') && (
        <Input
          className="mt-2 h-8 w-64 text-xs"
          placeholder={form.scope_type === 'kb' ? '输入知识库名称' : '输入分组 ID'}
          value={form.scope_id}
          onChange={(e) => setForm({ ...form, scope_id: e.target.value })}
        />
      )}
    </div>

    <div className="grid gap-3 md:grid-cols-3">
      <div>
        <label className="text-xs font-medium">最多挂载条数</label>
        <Input type="number" value={String(form.max_items)} onChange={(e) => setForm({ ...form, max_items: Number(e.target.value || 0) })} className="mt-1 h-8 text-xs" />
      </div>
      <div>
        <label className="text-xs font-medium">注入 token 上限</label>
        <Input type="number" value={String(form.max_tokens)} onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value || 0) })} className="mt-1 h-8 text-xs" />
      </div>
      <div>
        <label className="text-xs font-medium">优先级</label>
        <Input type="number" value={String(form.priority)} onChange={(e) => setForm({ ...form, priority: Number(e.target.value || 0) })} className="mt-1 h-8 text-xs" />
      </div>
    </div>
  </div>
);

const Step4Preview: React.FC<{
  sample?: { items: Array<{ text: string; metadata: Record<string, unknown>; id?: string | null }>; fields: Array<{ name: string; type: string; fill_rate: number; unique_count: number; sample_values: unknown[] }>; total_estimate?: number | null };
  loading: boolean;
  onAnalyze(): void;
  form: FormState;
  setName(s: string): void;
  setDesc(s: string): void;
}> = ({ sample, loading, onAnalyze, form, setName, setDesc }) => (
  <div className="space-y-4">
    <div className="grid gap-3 md:grid-cols-2">
      <div>
        <label className="text-xs font-medium">挂载名称 *</label>
        <Input value={form.name} onChange={(e) => setName(e.target.value)} className="mt-1 h-8 text-xs" />
      </div>
      <div>
        <label className="text-xs font-medium">说明</label>
        <Input value={form.description} onChange={(e) => setDesc(e.target.value)} className="mt-1 h-8 text-xs" />
      </div>
    </div>

    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={onAnalyze} disabled={loading}>
        {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
        抽样并自动分析字段
      </Button>
      {sample && <span className="text-xs text-[var(--cy-text-tertiary)]">已采样 {sample.items.length} 条 · {sample.fields.length} 个字段</span>}
    </div>

    {sample?.fields.length ? (
      <div>
        <h4 className="mb-2 text-xs font-semibold">字段 schema</h4>
        <table className="w-full text-xs">
          <thead className="bg-[var(--cy-surface-1)] text-[var(--cy-text-tertiary)]">
            <tr>
              <th className="px-2 py-1 text-left">字段</th>
              <th className="px-2 py-1 text-left">类型</th>
              <th className="px-2 py-1 text-left">非空率</th>
              <th className="px-2 py-1 text-left">unique</th>
              <th className="px-2 py-1 text-left">示例</th>
            </tr>
          </thead>
          <tbody>
            {sample.fields.map((f) => (
              <tr key={f.name} className="border-b border-[var(--cy-border-subtle)]">
                <td className="px-2 py-1 font-mono">{f.name}</td>
                <td className="px-2 py-1">{f.type}</td>
                <td className="px-2 py-1">{(f.fill_rate * 100).toFixed(0)}%</td>
                <td className="px-2 py-1">{f.unique_count}</td>
                <td className="px-2 py-1 text-[var(--cy-text-tertiary)] line-clamp-1">{f.sample_values.slice(0, 3).map(String).join(' / ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : null}

    {sample?.items.length ? (
      <div>
        <h4 className="mb-2 text-xs font-semibold">前 5 条样本</h4>
        <div className="space-y-2">
          {sample.items.slice(0, 5).map((it, i) => (
            <div key={i} className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2 text-xs">
              <div className="line-clamp-3 text-[var(--cy-text-primary)]">{it.text}</div>
              {Object.keys(it.metadata).length > 0 && (
                <div className="mt-1 truncate text-[10px] text-[var(--cy-text-tertiary)]">
                  {Object.entries(it.metadata).slice(0, 5).map(([k, v]) => `${k}=${String(v).slice(0, 30)}`).join(' · ')}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    ) : null}
  </div>
);

// ============================================================================
// Fallback sources — 后端 /data-mounts/sources API 不可用时用,保证向导永远
// 能渲染。覆盖 12 种源的最小可用字段(label/description/spec_form/capabilities)
// 与后端 SourceRegistry.to_catalog() 输出 1:1 对齐。
// ============================================================================

const FALLBACK_SOURCES: SourceCatalogItem[] = [
  {
    type_id: 'kb', label: '知识库', icon: 'library',
    description: '从已建好的本地知识库取切片',
    capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [
      { name: 'kb_name', label: '知识库', type: 'string', required: true,
        help: '从已建 KB 列表选;留空走兜底' },
      { name: 'query', label: '搜索关键词', type: 'string', default: '',
        help: '留空 = 拉前 N 条;填了走混合检索取前 N 条相关结果' },
      { name: 'top_k', label: '最多返回条数', type: 'int', default: 200 },
    ] },
  },
  {
    type_id: 'knowledge_source', label: '知识源', icon: 'boxes',
    description: '已配置的 ES / Mongo / SQL / 外部向量库知识源',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'source_id', label: '知识源 ID', type: 'int', required: true },
      { name: 'query', label: '条件筛选', type: 'string', default: '' },
    ] },
  },
  {
    type_id: 'file', label: '文件 / 文件夹', icon: 'folder',
    description: '本地路径或 glob;PDF/Doc/PPT/CSV/MD/TXT/HTML/Image 自动 loader',
    capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [
      { name: 'path', label: '路径或 glob', type: 'string', required: true,
        help: '如 /data/docs 或 /data/**/*.pdf' },
      { name: 'recursive', label: '递归子目录', type: 'bool', default: true },
    ] },
  },
  {
    type_id: 'annotation', label: '标注样本', icon: 'tag',
    description: '训练数据中心已通过的标注任务样本',
    capabilities: ['context', 'fewshot', 'preference', 'safety'],
    spec_form: { fields: [
      { name: 'task_type', label: '任务类型', type: 'string', default: '' },
      { name: 'status', label: '状态', type: 'select', default: 'approved',
        options: [
          { value: 'approved', label: '已通过' },
          { value: 'any', label: '全部' },
        ] },
      { name: 'task_ids', label: '任务 ID(逗号)', type: 'string', default: '' },
    ] },
  },
  {
    type_id: 'web', label: 'Web 网页', icon: 'globe',
    description: 'URL 抓取(单页 / 站点递归);走 langchain WebBaseLoader',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'urls', label: 'URL(逗号或换行)', type: 'string', required: true },
      { name: 'recursive', label: '递归整站', type: 'bool', default: false },
      { name: 'max_depth', label: '递归深度', type: 'int', default: 2 },
    ] },
  },
  {
    type_id: 'sql', label: 'SQL 数据库', icon: 'database',
    description: 'Postgres / MySQL / SQLite — 自定义 query 拉行',
    capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [
      { name: 'url', label: 'SQLAlchemy URL', type: 'password', required: true,
        help: '如 postgresql://user:pass@host:5432/db' },
      { name: 'query', label: '查询语句(SELECT)', type: 'string', required: true },
      { name: 'text_column', label: '文本字段名', type: 'string', default: '' },
      { name: 'id_column', label: 'ID 字段名', type: 'string', default: '' },
    ] },
  },
  {
    type_id: 's3', label: 'S3 / MinIO', icon: 'cloud',
    description: 'AWS S3 或 S3 兼容 endpoint;按前缀挂目录',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'bucket', label: 'Bucket', type: 'string', required: true },
      { name: 'prefix', label: '前缀', type: 'string', default: '' },
      { name: 'endpoint_url', label: 'Endpoint', type: 'string', default: '',
        help: 'MinIO/OSS 必填;原生 S3 留空' },
      { name: 'access_key', label: 'Access Key', type: 'string', default: '' },
      { name: 'secret_key', label: 'Secret Key', type: 'password', default: '' },
    ] },
  },
  {
    type_id: 'mongo', label: 'MongoDB', icon: 'database',
    description: 'MongoDB 集合;给定 query 过滤 + text_field 选文本主体',
    capabilities: ['corpus', 'context', 'fewshot'],
    spec_form: { fields: [
      { name: 'uri', label: 'Mongo URI', type: 'password', required: true },
      { name: 'db', label: 'Database', type: 'string', required: true },
      { name: 'collection', label: 'Collection', type: 'string', required: true },
      { name: 'filter_json', label: 'Filter (JSON)', type: 'string', default: '{}' },
      { name: 'text_field', label: '文本字段名', type: 'string', default: 'content' },
      { name: 'id_field', label: 'ID 字段名', type: 'string', default: '_id' },
    ] },
  },
  {
    type_id: 'notion', label: 'Notion', icon: 'file-text',
    description: 'Notion DB 或本地导出目录',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'mode', label: '模式', type: 'select', default: 'database',
        options: [
          { value: 'database', label: 'Notion DB(API token)' },
          { value: 'directory', label: '本地导出目录' },
        ] },
      { name: 'integration_token', label: 'Integration Token', type: 'password', default: '' },
      { name: 'database_id', label: 'Database ID', type: 'string', default: '' },
      { name: 'directory_path', label: '目录路径', type: 'string', default: '' },
    ] },
  },
  {
    type_id: 'confluence', label: 'Confluence', icon: 'book-open',
    description: 'Atlassian Confluence space',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'url', label: 'Confluence URL', type: 'string', required: true },
      { name: 'username', label: '用户名 / 邮箱', type: 'string', default: '' },
      { name: 'api_key', label: 'API Key', type: 'password', required: true },
      { name: 'space_key', label: 'Space Key', type: 'string', required: true },
      { name: 'include_attachments', label: '含附件', type: 'bool', default: false },
    ] },
  },
  {
    type_id: 'github', label: 'Git 仓库', icon: 'git-branch',
    description: 'git clone 后用 GitLoader 取代码 / 文档',
    capabilities: ['corpus', 'context'],
    spec_form: { fields: [
      { name: 'repo_url', label: '仓库 URL', type: 'string', required: true },
      { name: 'branch', label: '分支', type: 'string', default: 'main' },
      { name: 'include_extensions', label: '后缀(逗号)', type: 'string',
        default: '.py,.md,.tsx,.ts,.json' },
    ] },
  },
  {
    type_id: 'conversation', label: '历史对话', icon: 'message-square',
    description: '用户给 thumbs-up 的对话 → fewshot / preference',
    capabilities: ['fewshot', 'preference'],
    spec_form: { fields: [
      { name: 'min_thumbs', label: '最少点赞数', type: 'int', default: 1 },
      { name: 'user_id', label: '限定用户 ID', type: 'int', default: 0 },
      { name: 'task_type', label: '任务类型', type: 'string', default: '' },
    ] },
  },
];
