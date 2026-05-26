/**
 * OpenAPI / Swagger 批量导入弹窗 — 一次性把 Java Spring Doc / FastAPI 的
 * ``/openapi.json`` 全量解析,用户勾选后写进 ``custom_tools.yaml``。
 *
 * 三步状态机:
 *   1. ``input``    填 URL 或粘贴 JSON/YAML → 点「解析」走 preview
 *   2. ``preview``  显示 drafts 清单(每行 method+url+name+title+desc + 复选框
 *                    + 展开行查看参数);用户勾选要导入的、就地改 description
 *                    / title 等;点「导入选中的 N 条」走 commit
 *   3. ``done``     展示 commit 结果(created/updated/skipped 统计 + 跳过原因)
 *
 * 描述自动生成:后端 ``desc_synthesizer`` 已经按"summary → description →
 * method+path"三层兜底填好了,前端拿到的 ``draft.description`` 就是中文友好版,
 * 用户可在预览行直接编辑覆盖。
 */
import * as React from 'react';
import { ArrowLeft, ArrowRight, Check, Download, Loader2, Search } from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogDescription, DialogTitle, Input, Switch, Textarea, cn,
} from '@chayuan/ui';
import type { OpenApiDraft, OpenApiPreview } from '@chayuan/api';
import { notifyInfo, notifySuccess, reportError } from '../../../store/errorDialog';
import { useImportOpenApiCommit, useImportOpenApiPreview } from '../hooks';
import { ParamsEditor } from './ParamsEditor';

export interface OpenApiImportDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

type Step = 'input' | 'preview' | 'done';

interface DraftRow extends OpenApiDraft {
  /** 是否勾选导入。默认全勾。 */
  _checked: boolean;
  /** 是否展开参数详情 */
  _expanded: boolean;
}

const METHOD_TINT: Record<string, string> = {
  GET: 'bg-emerald-50 text-emerald-700',
  POST: 'bg-sky-50 text-sky-700',
  PUT: 'bg-amber-50 text-amber-700',
  PATCH: 'bg-violet-50 text-violet-700',
  DELETE: 'bg-red-50 text-red-700',
};

export const OpenApiImportDialog: React.FC<OpenApiImportDialogProps> = ({ open, onOpenChange }) => {
  const previewMut = useImportOpenApiPreview();
  const commitMut = useImportOpenApiCommit();

  const [step, setStep] = React.useState<Step>('input');
  const [specUrl, setSpecUrl] = React.useState('');
  const [specText, setSpecText] = React.useState('');
  const [preview, setPreview] = React.useState<OpenApiPreview | null>(null);
  const [rows, setRows] = React.useState<DraftRow[]>([]);
  const [keyword, setKeyword] = React.useState('');
  const [overwrite, setOverwrite] = React.useState(false);
  const [commitResult, setCommitResult] = React.useState<{
    created: string[]; updated: string[]; skipped: { name: string; reason: string }[];
  } | null>(null);

  // 弹窗每次重开都重置
  React.useEffect(() => {
    if (!open) return;
    setStep('input');
    setSpecUrl('');
    setSpecText('');
    setPreview(null);
    setRows([]);
    setKeyword('');
    setOverwrite(false);
    setCommitResult(null);
  }, [open]);

  // ── step 1 → step 2 ───────────────────────────────────────────
  const onParse = async () => {
    const hasUrl = specUrl.trim().length > 0;
    const hasText = specText.trim().length > 0;
    if (!hasUrl && !hasText) {
      notifyInfo('需要 spec', '请填入 OpenAPI / Swagger spec 的 URL 或粘贴 JSON/YAML 文本');
      return;
    }
    try {
      const p = await previewMut.mutateAsync(
        hasText ? { spec_text: specText } : { spec_url: specUrl.trim() },
      );
      setPreview(p);
      setRows(p.drafts.map((d) => ({ ...d, _checked: true, _expanded: false })));
      setStep('preview');
    } catch (e) {
      reportError(e, '解析 OpenAPI spec 失败');
    }
  };

  // ── step 2 → step 3 ───────────────────────────────────────────
  const onCommit = async () => {
    const selected = rows.filter((r) => r._checked);
    if (selected.length === 0) {
      notifyInfo('未选中', '请至少勾选一条接口再导入');
      return;
    }
    try {
      // 把内部状态(_checked / _expanded)剥掉,只交后端 OpenApiDraft
      const drafts = selected.map(({ _checked, _expanded, ...d }) => d);
      const result = await commitMut.mutateAsync({ drafts, overwrite });
      setCommitResult(result);
      setStep('done');
      const total = result.total_created + result.total_updated;
      if (total > 0) {
        notifySuccess(
          '导入完成',
          `创建 ${result.total_created} 条,更新 ${result.total_updated} 条;跳过 ${result.total_skipped} 条`,
        );
      }
    } catch (e) {
      reportError(e, '导入 OpenAPI 接口失败');
    }
  };

  const filtered = React.useMemo(() => {
    if (!keyword.trim()) return rows.map((r, idx) => ({ row: r, idx }));
    const q = keyword.toLowerCase();
    return rows
      .map((r, idx) => ({ row: r, idx }))
      .filter(({ row }) =>
        `${row.name} ${row.title} ${row.description} ${row.method} ${row.url}`
          .toLowerCase()
          .includes(q),
      );
  }, [rows, keyword]);

  const selectedCount = rows.filter((r) => r._checked).length;

  const setRow = (idx: number, patch: Partial<DraftRow>) =>
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));

  const setAllChecked = (checked: boolean) =>
    setRows((prev) => prev.map((r) => ({ ...r, _checked: checked })));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] w-[min(960px,96vw)] flex-col overflow-hidden p-0">
        <header className="border-b border-[var(--cy-border-subtle)] px-5 py-4">
          <DialogTitle className="flex items-center gap-2 text-base font-semibold text-[var(--cy-text-primary)]">
            <Download className="h-4 w-4 text-[var(--cy-brand-600)]" />
            从 OpenAPI / Swagger 批量导入
            <StepBadge step={step} />
          </DialogTitle>
          <DialogDescription className="mt-1 text-xs text-[var(--cy-text-secondary)]">
            {step === 'input' && '填写 Java Spring Doc / FastAPI 的 spec 地址,后端拉取并解析每个 operation。也可粘贴 JSON/YAML 文本(离线场景)。'}
            {step === 'preview' && (
              <>
                解析了 <b>{preview?.drafts.length ?? 0}</b> 个接口
                {preview?.skipped?.length ? `,跳过 ${preview.skipped.length} 条无法解析项` : ''}。
                可逐条改 name / title / description,勾选要导入的项。
              </>
            )}
            {step === 'done' && '导入完成,所有勾选的接口已写入 custom_tools.yaml 并立即生效。'}
          </DialogDescription>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {step === 'input' && (
            <InputStep
              specUrl={specUrl}
              specText={specText}
              setSpecUrl={setSpecUrl}
              setSpecText={setSpecText}
              loading={previewMut.isPending}
            />
          )}

          {step === 'preview' && (
            <PreviewStep
              preview={preview!}
              rows={filtered}
              keyword={keyword}
              setKeyword={setKeyword}
              setRow={setRow}
              setAllChecked={setAllChecked}
              selectedCount={selectedCount}
              total={rows.length}
              overwrite={overwrite}
              setOverwrite={setOverwrite}
            />
          )}

          {step === 'done' && commitResult && (
            <DoneStep result={commitResult} />
          )}
        </div>

        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-5 py-3">
          <div className="text-[11px] text-[var(--cy-text-tertiary)]">
            {step === 'input' && '步骤 1 / 3 · 填 spec'}
            {step === 'preview' && `步骤 2 / 3 · 预览(已选 ${selectedCount} / ${rows.length})`}
            {step === 'done' && '步骤 3 / 3 · 完成'}
          </div>
          <div className="flex items-center gap-2">
            {step === 'preview' && (
              <Button size="sm" variant="ghost" onClick={() => setStep('input')}>
                <ArrowLeft className="h-3.5 w-3.5" /> 上一步
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              {step === 'done' ? '关闭' : '取消'}
            </Button>
            {step === 'input' && (
              <Button size="sm" onClick={() => void onParse()} disabled={previewMut.isPending}>
                {previewMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ArrowRight className="h-3.5 w-3.5" />
                )}
                解析 spec
              </Button>
            )}
            {step === 'preview' && (
              <Button size="sm" onClick={() => void onCommit()} disabled={commitMut.isPending || selectedCount === 0}>
                {commitMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                导入选中的 {selectedCount} 条
              </Button>
            )}
          </div>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

// ── 顶部 step badge ────────────────────────────────────────────────

const StepBadge: React.FC<{ step: Step }> = ({ step }) => {
  const map = {
    input: { label: '填 spec', tint: 'bg-[var(--cy-surface-2)] text-[var(--cy-text-secondary)]' },
    preview: { label: '预览', tint: 'bg-amber-50 text-amber-700' },
    done: { label: '完成', tint: 'bg-emerald-50 text-emerald-700' },
  } as const;
  const m = map[step];
  return (
    <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-medium', m.tint)}>{m.label}</span>
  );
};

// ── step 1: 输入 ───────────────────────────────────────────────────

const InputStep: React.FC<{
  specUrl: string;
  specText: string;
  setSpecUrl(v: string): void;
  setSpecText(v: string): void;
  loading: boolean;
}> = ({ specUrl, specText, setSpecUrl, setSpecText, loading }) => (
  <div className="space-y-5">
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-[var(--cy-text-primary)]">spec URL</label>
      <Input
        value={specUrl}
        placeholder="https://your-service/openapi.json   或   https://your-service/v3/api-docs"
        onChange={(e) => setSpecUrl(e.target.value)}
        disabled={loading}
        className="h-9 font-mono text-xs"
      />
      <p className="text-[10px] text-[var(--cy-text-tertiary)]">
        FastAPI 默认在 <code>/openapi.json</code>;Spring Doc 默认在 <code>/v3/api-docs</code>。后端会拉取并解析。
      </p>
    </div>
    <div className="flex items-center gap-3 text-[10px] text-[var(--cy-text-tertiary)]">
      <span className="h-px flex-1 bg-[var(--cy-border-subtle)]" />
      <span>或</span>
      <span className="h-px flex-1 bg-[var(--cy-border-subtle)]" />
    </div>
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-[var(--cy-text-primary)]">粘贴 spec 文本(JSON / YAML)</label>
      <Textarea
        value={specText}
        placeholder='{"openapi": "3.0.0", "info": {...}, "paths": {...}}'
        onChange={(e) => setSpecText(e.target.value)}
        disabled={loading}
        rows={10}
        className="font-mono text-[11px] leading-relaxed"
      />
      <p className="text-[10px] text-[var(--cy-text-tertiary)]">
        离线场景或内网无法直拉时用。两个都填会优先用粘贴文本。
      </p>
    </div>
  </div>
);

// ── step 2: 预览 ──────────────────────────────────────────────────

const PreviewStep: React.FC<{
  preview: OpenApiPreview;
  rows: { row: DraftRow; idx: number }[];
  keyword: string;
  setKeyword(v: string): void;
  setRow(idx: number, patch: Partial<DraftRow>): void;
  setAllChecked(checked: boolean): void;
  selectedCount: number;
  total: number;
  overwrite: boolean;
  setOverwrite(v: boolean): void;
}> = ({
  preview, rows, keyword, setKeyword, setRow, setAllChecked,
  selectedCount, total, overwrite, setOverwrite,
}) => (
  <div className="space-y-3">
    {/* 摘要条 */}
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2 text-xs">
      <span className="text-[var(--cy-text-tertiary)]">spec:</span>
      <span className="font-medium text-[var(--cy-text-primary)]">{preview.title || '(无标题)'}</span>
      {preview.version && (
        <span className="rounded bg-[var(--cy-surface-base)] px-1.5 py-0.5 text-[10px] text-[var(--cy-text-tertiary)] ring-1 ring-[var(--cy-border-subtle)]">
          v{preview.version}
        </span>
      )}
      {preview.base_url && (
        <span className="truncate font-mono text-[10px] text-[var(--cy-text-tertiary)]">{preview.base_url}</span>
      )}
    </div>

    {preview.skipped.length > 0 && (
      <details className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800">
        <summary className="cursor-pointer font-medium">
          解析时跳过 {preview.skipped.length} 项(展开查看)
        </summary>
        <ul className="mt-2 space-y-0.5 font-mono text-[10px]">
          {preview.skipped.map((s, i) => (
            <li key={i}>• {s}</li>
          ))}
        </ul>
      </details>
    )}

    {/* 工具条:全选 + 搜索 + overwrite */}
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() => setAllChecked(selectedCount < total)}
        className="h-7 text-[11px]"
      >
        {selectedCount === total ? '全部不选' : '全选'} ({selectedCount} / {total})
      </Button>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索 name / 路径 / 描述"
          className="h-7 w-56 pl-6 text-[11px]"
        />
      </div>
      <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-1 text-[11px]">
        <Switch checked={overwrite} onCheckedChange={(v) => setOverwrite(Boolean(v))} />
        覆盖同名(否则跳过)
      </label>
    </div>

    {/* 行清单 */}
    <ul className="space-y-2">
      {rows.map(({ row, idx }) => (
        <DraftRowItem
          key={`${row.name}_${idx}`}
          row={row}
          onChange={(patch) => setRow(idx, patch)}
        />
      ))}
      {rows.length === 0 && (
        <p className="rounded-md border border-dashed border-[var(--cy-border-subtle)] py-8 text-center text-xs text-[var(--cy-text-tertiary)]">
          没有匹配的接口
        </p>
      )}
    </ul>
  </div>
);

const DraftRowItem: React.FC<{
  row: DraftRow;
  onChange(patch: Partial<DraftRow>): void;
}> = ({ row, onChange }) => {
  const tint = METHOD_TINT[row.method] ?? 'bg-slate-100 text-slate-700';
  return (
    <li
      className={cn(
        'rounded-md border bg-[var(--cy-surface-base)] transition-colors',
        row._checked ? 'border-[var(--cy-brand-300)]' : 'border-[var(--cy-border-subtle)] opacity-70',
      )}
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <input
          type="checkbox"
          checked={row._checked}
          onChange={(e) => onChange({ _checked: e.target.checked })}
          className="mt-1 h-3.5 w-3.5 cursor-pointer"
        />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className={cn('rounded px-1.5 py-0.5 font-mono text-[10px] font-bold', tint)}>
              {row.method}
            </span>
            <span className="truncate font-mono text-[11px] text-[var(--cy-text-secondary)]">{row.url}</span>
            {row.tags?.slice(0, 2).map((t) => (
              <span
                key={t}
                className="rounded bg-[var(--cy-surface-1)] px-1.5 py-0.5 text-[10px] text-[var(--cy-text-tertiary)]"
              >
                {t}
              </span>
            ))}
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Input
              value={row.name}
              onChange={(e) => onChange({ name: e.target.value })}
              placeholder="工具 name"
              className="h-7 font-mono text-[11px]"
            />
            <Input
              value={row.title}
              onChange={(e) => onChange({ title: e.target.value })}
              placeholder="title"
              className="h-7 text-[11px]"
            />
          </div>
          <Textarea
            value={row.description}
            onChange={(e) => onChange({ description: e.target.value })}
            placeholder="LLM 选工具时读的描述(已自动生成中文,可改)"
            rows={2}
            className="text-[11px] leading-relaxed"
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onChange({ _expanded: !row._expanded })}
              className="text-[10px] text-[var(--cy-brand-600)] hover:underline"
            >
              {row._expanded ? '收起参数' : `展开 ${row.params.length} 个参数`}
            </button>
            {row.issues?.length ? (
              <span className="text-[10px] text-amber-700">⚠ {row.issues.length} 项告警</span>
            ) : null}
          </div>
          {row._expanded && (
            <div className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2">
              <ParamsEditor
                value={row.params}
                onChange={(v) => onChange({ params: v })}
              />
            </div>
          )}
        </div>
      </div>
    </li>
  );
};

// ── step 3: 完成 ──────────────────────────────────────────────────

const DoneStep: React.FC<{
  result: { created: string[]; updated: string[]; skipped: { name: string; reason: string }[] };
}> = ({ result }) => (
  <div className="space-y-4">
    <div className="grid gap-2 sm:grid-cols-3">
      <Stat label="新建" tint="bg-emerald-50 text-emerald-700" count={result.created.length} />
      <Stat label="更新" tint="bg-sky-50 text-sky-700" count={result.updated.length} />
      <Stat label="跳过" tint="bg-amber-50 text-amber-700" count={result.skipped.length} />
    </div>
    <ResultList title="新建" items={result.created} />
    <ResultList title="更新" items={result.updated} />
    {result.skipped.length > 0 && (
      <div>
        <h4 className="mb-1 text-xs font-medium text-[var(--cy-text-primary)]">跳过</h4>
        <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-50/40 px-3 py-2 text-[11px] text-amber-800">
          {result.skipped.map((s, i) => (
            <li key={i}>
              <span className="font-mono">{s.name}</span> — {s.reason}
            </li>
          ))}
        </ul>
      </div>
    )}
  </div>
);

const Stat: React.FC<{ label: string; tint: string; count: number }> = ({ label, tint, count }) => (
  <div className={cn('rounded-md px-3 py-2', tint)}>
    <p className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</p>
    <p className="text-2xl font-semibold tabular-nums">{count}</p>
  </div>
);

const ResultList: React.FC<{ title: string; items: string[] }> = ({ title, items }) => {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1 text-xs font-medium text-[var(--cy-text-primary)]">{title}</h4>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-[var(--cy-text-secondary)] sm:grid-cols-3">
        {items.map((n) => (
          <li key={n} className="truncate font-mono">• {n}</li>
        ))}
      </ul>
    </div>
  );
};
