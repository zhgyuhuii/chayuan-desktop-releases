/**
 * 添加结构化数据库 —— 对话框。
 *
 * 数据流:
 *   - 打开时拉 GET /knowledge_source/dialects(后端返 13 种,含 kingbase / dm / mysql / postgres / oracle / mssql / clickhouse / doris / hive / sqlite / mongo / es / image)
 *   - 选 dialect 自动填默认端口(getDefaultPort);用户可改
 *   - "测试连接" → POST /knowledge_source/test_connection(不落库),按 ok 显示 ✓ / ✗
 *   - "保存"   → POST /knowledge_source/(后端会先 test 再落库);成功后 invalidate 'kb-sources' query
 *
 * 兼容:即使后端 /dialects 端点不可达,fallback 列表仍包含金仓 / 达梦。
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  cn,
} from '@chayuan/ui';
import {
  knowledgeSource,
  getDefaultPort,
  FALLBACK_DIALECTS,
  DIALECT_META,
  type CreateSourceInput,
  type KsKind,
  type TestConnectionResult,
} from '@chayuan/api';
import { SchemaScopePicker, scopeKindFor } from './SchemaScopePicker';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';

export interface AddStructuredSourceDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  onCreated?(sourceId: number): void;
}

interface FormState {
  name: string;
  display_name: string;
  description: string;
  dialect: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  visibility: 'private' | 'public';
}

const DEFAULT_FORM: FormState = {
  name: '',
  display_name: '',
  description: '',
  dialect: 'mysql',
  host: '127.0.0.1',
  port: 3306,
  database: '',
  username: '',
  password: '',
  visibility: 'private',
};

export const AddStructuredSourceDialog: React.FC<AddStructuredSourceDialogProps> = ({
  open,
  onOpenChange,
  onCreated,
}) => {
  const qc = useQueryClient();
  const [form, setForm] = React.useState<FormState>(DEFAULT_FORM);
  const [testResult, setTestResult] = React.useState<TestConnectionResult | null>(null);
  // 创建成功后,自动弹"选择可见表/集合"二步;sourceId !=null 时 SchemaScopePicker 接管
  const [scopeForSourceId, setScopeForSourceId] = React.useState<number | null>(null);

  // 重置表单,每次打开都从默认开始
  React.useEffect(() => {
    if (open) {
      setForm(DEFAULT_FORM);
      setTestResult(null);
      setScopeForSourceId(null);
    }
  }, [open]);

  // 拉后端支持的方言;失败 fallback 到 FALLBACK_DIALECTS(仍含金仓/达梦)
  const dialects = useQuery({
    queryKey: ['ks', 'dialects'],
    queryFn: () => knowledgeSource.listDialects(),
    staleTime: 60 * 60 * 1000, // 长缓存,后端方言变动不频繁
    enabled: open,
  });

  const dialectOptions = React.useMemo<Array<{ value: string; label: string; meta?: typeof DIALECT_META[string] }>>(() => {
    const map = dialects.data ?? FALLBACK_DIALECTS;
    return Object.entries(map).map(([value, label]) => ({
      value,
      label,
      ...(DIALECT_META[value] ? { meta: DIALECT_META[value] } : {}),
    }));
  }, [dialects.data]);

  const meta = DIALECT_META[form.dialect];
  const ksKind: KsKind = meta?.kind ?? 'sql';
  const isStructured = ksKind === 'sql' || ksKind === 'mongo' || ksKind === 'es';

  // 切方言时把 port 重置为该方言默认端口(仅当用户没改过端口);
  // 简化策略:只要 dialect 变,直接重置端口,用户可再改。
  const onDialectChange = (next: string) => {
    setForm((s) => ({
      ...s,
      dialect: next,
      port: getDefaultPort(next),
    }));
    setTestResult(null);
  };

  // 测连接
  const test = useMutation({
    mutationFn: () =>
      knowledgeSource.testConnection({
        dialect: form.dialect,
        host: form.host,
        port: form.port,
        database: form.database,
        username: form.username,
        password: form.password,
      }),
    onSuccess: (data) => setTestResult(data),
    onError: (e) =>
      setTestResult({
        ok: false,
        msg: e instanceof Error ? e.message : '测试连接失败',
      }),
  });

  // 保存
  const create = useMutation({
    mutationFn: () => {
      const body: CreateSourceInput = {
        name: form.name.trim(),
        kind: ksKind,
        display_name: form.display_name.trim() || form.name.trim(),
        description: form.description.trim(),
        dialect: form.dialect,
        host: form.host.trim(),
        port: form.port,
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password,
        visibility: form.visibility,
      };
      return knowledgeSource.create(body);
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ['ks', 'list'] });
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
      onCreated?.(data.id);
      // 结构化(sql/mongo/es)创建后自动进入"选择可见表/集合"步骤;
      // 用户跳过 = 默认查询全部(后端语义)。其它 kind(image/vector)直接关 dialog。
      if (isStructured) {
        setScopeForSourceId(data.id);
      } else {
        onOpenChange(false);
      }
    },
  });

  const canSave = form.name.trim().length > 0 && form.dialect && !create.isPending;

  // 已经进入"选择范围"二步:首层 dialog 让位给 SchemaScopePicker;关掉它则
  // 整个流程结束(用户已经创建好,跳过 = 默认全部)
  if (scopeForSourceId != null) {
    return (
      <SchemaScopePicker
        sourceId={scopeForSourceId}
        kind={scopeKindFor(form.dialect === 'mongo' ? 'mongo' : form.dialect === 'es' ? 'es' : 'sql')}
        open={open}
        onOpenChange={(o) => {
          if (!o) {
            // 用户保存或关闭了选择 dialog → 整个创建流程结束
            setScopeForSourceId(null);
            onOpenChange(false);
          }
        }}
        onSaved={() => {
          void qc.invalidateQueries({ queryKey: ['ks', 'list'] });
          void qc.invalidateQueries({ queryKey: ['ku.list'] });
        }}
      />
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>添加结构化数据库</DialogTitle>
          <DialogDescription>
            将 SQL / NoSQL 数据源接入察元 RAG;支持国产数据库(金仓 / 达梦)。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Field label="名称(唯一标识) *">
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="例如 hr_pg_main"
              required
            />
          </Field>

          <Field label="显示名">
            <Input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="留空则与名称相同"
            />
          </Field>

          <Field label="数据库类型 *">
            <select
              value={form.dialect}
              onChange={(e) => onDialectChange(e.target.value)}
              className={cn(
                'w-full rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 py-2 text-sm',
              )}
            >
              {dialectOptions.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
            {meta?.hint && (
              <p className="mt-1 text-[11px] text-[var(--cy-text-tertiary)]">{meta.hint}</p>
            )}
          </Field>

          {isStructured && form.dialect !== 'sqlite' && (
            <>
              <div className="grid grid-cols-3 gap-3">
                <Field label="主机" className="col-span-2">
                  <Input
                    value={form.host}
                    onChange={(e) => setForm({ ...form, host: e.target.value })}
                    placeholder="127.0.0.1"
                  />
                </Field>
                <Field label="端口">
                  <Input
                    type="number"
                    value={form.port || ''}
                    onChange={(e) =>
                      setForm({ ...form, port: Number.parseInt(e.target.value, 10) || 0 })
                    }
                    placeholder={String(getDefaultPort(form.dialect) || 0)}
                  />
                </Field>
              </div>

              {meta?.requiresDatabase && (
                <Field label={form.dialect === 'sqlite' ? '路径' : '库名'}>
                  <Input
                    value={form.database}
                    onChange={(e) => setForm({ ...form, database: e.target.value })}
                    placeholder={form.dialect === 'sqlite' ? '/data/app.db 或 :memory:' : ''}
                  />
                </Field>
              )}

              <div className="grid grid-cols-2 gap-3">
                <Field label="用户名">
                  <Input
                    value={form.username}
                    onChange={(e) => setForm({ ...form, username: e.target.value })}
                  />
                </Field>
                <Field label="密码">
                  <Input
                    type="password"
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                  />
                </Field>
              </div>
            </>
          )}

          {form.dialect === 'sqlite' && (
            <Field label="路径(/abs/path 或 :memory:)">
              <Input
                value={form.database}
                onChange={(e) => setForm({ ...form, database: e.target.value })}
                placeholder=":memory:"
              />
            </Field>
          )}

          <Field label="可见性">
            <div className="flex gap-2">
              {(['private', 'public'] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setForm({ ...form, visibility: v })}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs transition-colors',
                    form.visibility === v
                      ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
                      : 'border-[var(--cy-border-default)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
                  )}
                >
                  {v === 'private' ? '私有' : '公开'}
                </button>
              ))}
            </div>
          </Field>

          <Field label="描述">
            <Input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="可选"
            />
          </Field>

          {/* 测试连接结果 */}
          {testResult && (
            <div
              className={cn(
                'flex items-start gap-2 rounded-md border px-3 py-2 text-xs',
                testResult.ok
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                  : 'border-red-200 bg-red-50 text-red-700',
              )}
            >
              {testResult.ok ? (
                <Check className="mt-0.5 h-3.5 w-3.5" />
              ) : (
                <X className="mt-0.5 h-3.5 w-3.5" />
              )}
              <div className="flex-1 break-all">
                <p className="font-medium">{testResult.ok ? '连接成功' : '连接失败'}</p>
                {testResult.msg && <p className="mt-0.5">{testResult.msg}</p>}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            disabled={test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending && <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />}
            测试连接
          </Button>
          <Button onClick={() => create.mutate()} disabled={!canSave}>
            {create.isPending && <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{
  label: string;
  className?: string;
  children: React.ReactNode;
}> = ({ label, className, children }) => (
  <div className={cn('space-y-1', className)}>
    <label className="block text-xs font-medium text-[var(--cy-text-secondary)]">{label}</label>
    {children}
  </div>
);
