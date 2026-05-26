/**
 * DataMountsPanel —— 训练数据中心第三 tab 主体。
 *
 * 三段:
 *   1. 工具条:[+ 新建] [↑ 导入 JSON/CSV] [↓ 导出全部] [刷新]
 *   2. 表格:name / source / mode / scope / 状态 / 数量 / 更新时间 / 操作
 *   3. 详情抽屉:点行打开 → 显示 spec、artifacts、预览样本
 *
 * 子组件:
 *   - MountWizard       —— 4 步向导(选源 → 配置 → 模式 → 预览发布)
 *   - MountDetailDrawer —— 行点击后抽屉
 */
import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Download, FileText, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Pill,
  cn,
} from '@chayuan/ui';
import { dataMountsApi, type DataMountRecord } from '@chayuan/api';
import { reportError } from '../../../store/errorDialog';
import { MountWizard } from './MountWizard';
import { MountDetailDrawer } from './MountDetailDrawer';

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
  published: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  disabled: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
};

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

export const DataMountsPanel: React.FC = () => {
  const qc = useQueryClient();
  const [scopeType, setScopeType] = React.useState<string>('');
  const [statusFilter, setStatusFilter] = React.useState<string>('');
  const [wizardOpen, setWizardOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<DataMountRecord | null>(null);
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const importInputRef = React.useRef<HTMLInputElement | null>(null);

  const listQuery = useQuery({
    queryKey: ['dataMounts.list', scopeType, statusFilter],
    queryFn: () => dataMountsApi.list({
      scope_type: scopeType || undefined,
      status: statusFilter || undefined,
    }),
    staleTime: 5_000,
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['dataMounts.list'] });
  };

  const publishMut = useMutation({
    mutationFn: (id: string) => dataMountsApi.publish(id),
    onSuccess: invalidate,
    onError: (e) => reportError(e, '发布挂载失败'),
  });

  const enableMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      dataMountsApi.setEnabled(id, enabled),
    onSuccess: invalidate,
    onError: (e) => reportError(e, '切换挂载启用态失败'),
  });

  const importMut = useMutation({
    mutationFn: (params: { format: 'json' | 'csv'; content: string }) =>
      dataMountsApi.import({
        format: params.format,
        content: params.content,
        scope_type: scopeType || 'user',
        scope_id: '',
        publish: false,
      }),
    onSuccess: (res) => {
      const errors = res.errors ?? [];
      if (errors.length) {
        // eslint-disable-next-line no-alert
        alert(`导入完成,${errors.length} 条失败:\n${errors.map((e) => `${e.name ?? '(无名)'}: ${e.error}`).join('\n')}`);
      } else {
        // eslint-disable-next-line no-alert
        alert(`导入成功,新增 ${res.created.length} 条草稿`);
      }
      invalidate();
    },
    onError: (e) => reportError(e, '导入挂载失败'),
  });

  const handleImport = async (file: File) => {
    const text = await file.text();
    const ext = file.name.toLowerCase().split('.').pop();
    const fmt: 'json' | 'csv' = ext === 'csv' || ext === 'tsv' ? 'csv' : 'json';
    importMut.mutate({ format: fmt, content: text });
  };

  const exportAll = async () => {
    const items = listQuery.data ?? [];
    const exported = await Promise.all(
      items.map(async (m) => {
        try {
          return await dataMountsApi.export(m.id);
        } catch {
          return m;
        }
      }),
    );
    const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `data-mounts-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  /**
   * 下载导入模板。
   *
   * 模板带 2 行示例 + 顶部说明,后端 ``_is_sample_row`` 自动过滤
   * (``_example: true`` 标记 OR ``name`` 以 ``[示例]`` 开头)。
   * 用户既能直接导入模板看效果,也能改改示例直接提交。
   */
  const downloadTemplate = (fmt: 'json' | 'csv') => {
    const filename = `data-mount-template.${fmt}`;
    const content = fmt === 'json' ? buildJsonTemplate() : buildCsvTemplate();
    const mime = fmt === 'json' ? 'application/json' : 'text/csv';
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 工具条 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--cy-border-subtle)] px-6 py-3">
        <Database className="h-4 w-4 text-[var(--cy-brand-600)]" />
        <span className="text-sm font-semibold text-[var(--cy-text-primary)]">数据挂载</span>
        <span className="text-xs text-[var(--cy-text-tertiary)]">
          · 共 {listQuery.data?.length ?? 0} 条
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <select
            value={scopeType}
            onChange={(e) => setScopeType(e.target.value)}
            className="h-8 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs"
          >
            <option value="">全部 scope</option>
            <option value="global">全局</option>
            <option value="user">个人</option>
            <option value="kb">KB</option>
            <option value="group">分组</option>
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-8 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs"
          >
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="disabled">已停用</option>
          </select>
          <Button size="sm" variant="outline" onClick={() => listQuery.refetch()}>
            <RefreshCw className={cn('h-3.5 w-3.5', listQuery.isFetching && 'animate-spin')} />
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".json,.csv,.tsv,application/json,text/csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleImport(f);
              e.target.value = '';
            }}
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="outline">
                <FileText className="h-3.5 w-3.5" /> 模板
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => downloadTemplate('json')}>
                下载 JSON 模板
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => downloadTemplate('csv')}>
                下载 CSV 模板
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button size="sm" variant="outline" onClick={() => importInputRef.current?.click()}>
            <Upload className="h-3.5 w-3.5" /> 导入
          </Button>
          <Button size="sm" variant="outline" onClick={() => void exportAll()}>
            <Download className="h-3.5 w-3.5" /> 导出
          </Button>
          <Button size="sm" onClick={() => { setEditing(null); setWizardOpen(true); }}>
            <Plus className="h-3.5 w-3.5" /> 新建挂载
          </Button>
        </div>
      </div>

      {/* 表格 */}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-[1] bg-[var(--cy-surface-1)] text-xs text-[var(--cy-text-tertiary)]">
            <tr>
              <th className="px-4 py-2 text-left font-medium">名称</th>
              <th className="px-4 py-2 text-left font-medium">数据源</th>
              <th className="px-4 py-2 text-left font-medium">模式</th>
              <th className="px-4 py-2 text-left font-medium">范围</th>
              <th className="px-4 py-2 text-left font-medium">状态</th>
              <th className="px-4 py-2 text-left font-medium">条数 / 优先级</th>
              <th className="px-4 py-2 text-left font-medium">更新时间</th>
              <th className="px-4 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={8} className="px-4 py-6 text-center text-xs text-[var(--cy-text-tertiary)]">加载中...</td></tr>
            )}
            {!listQuery.isLoading && (listQuery.data?.length ?? 0) === 0 && (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-xs text-[var(--cy-text-tertiary)]">
                还没有数据挂载,点击右上「新建挂载」开始 →
              </td></tr>
            )}
            {(listQuery.data ?? []).map((m) => {
              const sf = m.source_filter as { spec?: { source_type?: string }; target_kb?: string } | null;
              const sourceType = sf?.spec?.source_type ?? 'annotation';
              const updated = m.update_time ? new Date(m.update_time).toLocaleString('zh-CN', { hour12: false }) : '—';
              return (
                <tr
                  key={m.id}
                  className={cn(
                    'border-b border-[var(--cy-border-subtle)] hover:bg-[var(--cy-surface-1)]',
                    !m.enabled && 'opacity-60',
                  )}
                >
                  <td className="cursor-pointer px-4 py-3" onClick={() => setActiveId(m.id)}>
                    <div className="font-medium text-[var(--cy-text-primary)]">{m.name}</div>
                    {m.description && (
                      <div className="line-clamp-1 text-[11px] text-[var(--cy-text-tertiary)]">{m.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    <Pill tone="outline" size="sm">{sourceType}</Pill>
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {(m.mount_modes ?? []).slice(0, 3).map((mode) => (
                      <span key={mode} className="mr-1 inline-block rounded bg-[var(--cy-surface-2)] px-1.5 py-0.5">
                        {mode}
                      </span>
                    ))}
                  </td>
                  <td className="px-4 py-3 text-xs">{m.scope_type}{m.scope_id ? `/${m.scope_id}` : ''}</td>
                  <td className="px-4 py-3 text-xs">
                    <span className={cn('rounded-full px-2 py-0.5', STATUS_COLORS[m.status] ?? STATUS_COLORS.draft)}>
                      {STATUS_LABELS[m.status] ?? m.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs">{m.max_items} · P{m.priority}</td>
                  <td className="px-4 py-3 text-xs text-[var(--cy-text-tertiary)]">{updated}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => publishMut.mutate(m.id)} disabled={publishMut.isPending}>
                        发布
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => enableMut.mutate({ id: m.id, enabled: !m.enabled })}
                        disabled={enableMut.isPending}
                      >
                        {m.enabled ? '停用' : '启用'}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => { setEditing(m); setWizardOpen(true); }}>
                        编辑
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {wizardOpen && (
        <MountWizard
          initial={editing}
          onClose={() => { setWizardOpen(false); setEditing(null); }}
          onCreated={() => { setWizardOpen(false); setEditing(null); invalidate(); }}
        />
      )}

      {activeId && (
        <MountDetailDrawer
          mountId={activeId}
          onClose={() => setActiveId(null)}
        />
      )}
    </div>
  );
};

// ===========================================================================
// 模板内容生成
// ===========================================================================

/**
 * JSON 模板:顶层 ``{"_meta": ..., "items": [...]}`` 形式
 *
 * 后端 ``_parse_import_payload`` 兼容这种 envelope 形式;``items`` 内带
 * ``_example: true`` 的会自动跳过(``_is_sample_row`` 判定)。用户可:
 *   - 直接 "试装一遍" 看效果(全示例 → 实际创建 0 条,因为都被过滤)
 *   - 删 ``_example`` 字段 / 改 name 让示例变正式条目
 *   - 添加自己的条目(模板里没 ``_example`` 的会被导入)
 */
function buildJsonTemplate(): string {
  const tpl = {
    _meta: {
      _comment: [
        '数据挂载导入模板 — 字段说明请参考 docs/data-mount-guide.md',
        'name + source_filter.spec.source_type + mount_modes 是必填三件套',
        '带 _example: true 标记的条目导入时会自动跳过(本模板示例)',
        '复制条目去掉 _example 标记即变成真实导入',
      ],
      _version: 1,
    },
    items: [
      {
        _example: true,
        name: '[示例] KB → corpus 模式挂载',
        description: '把 legal_kb 的切片作为候选 ingest 任务,等用户在 main_kb 详情页确认入库',
        scope_type: 'global',
        scope_id: '',
        source_filter: {
          spec: {
            source_type: 'kb',
            options: { kb_name: 'legal_kb', top_k: 200 },
            max_items: 200,
          },
          target_kb: 'main_kb',
        },
        mount_modes: ['corpus'],
        priority: 5,
        max_items: 200,
        max_tokens: 1600,
      },
      {
        _example: true,
        name: '[示例] 标注样本 → fewshot',
        description: '从已通过的 annotation 任务抽 30 条作 in-context examples',
        scope_type: 'user',
        scope_id: '',
        source_filter: {
          spec: {
            source_type: 'annotation',
            options: { status: 'approved', task_type: '' },
            max_items: 30,
          },
        },
        mount_modes: ['fewshot'],
        priority: 0,
        max_items: 30,
        max_tokens: 1200,
      },
    ],
  };
  return JSON.stringify(tpl, null, 2);
}

/**
 * CSV 模板:`#` 开头是注释行,首行表头,然后 2 条 [示例] 行。
 *
 * 后端 ``_parse_import_payload`` 处理时:
 *   1. 过滤所有 ``#`` 开头的行
 *   2. ``name`` 以 [示例] / [example] 开头的行 _is_sample_row → 跳过
 *
 * 注意 csv.DictReader 不会自动 unquote 嵌套逗号,JSON 类字段建议放 JSON 模板。
 */
function buildCsvTemplate(): string {
  const lines: string[] = [
    '# 数据挂载 CSV 导入模板',
    '# 必填: name / source_type / mount_modes (后者多个用逗号分隔)',
    '# 行首带 # 的是注释,导入时跳过',
    '# name 以 [示例] 开头的行也会跳过',
    '# 复杂 options 建议用 JSON 模板;CSV 只适合简单源 (kb / web / annotation)',
    'name,description,scope_type,scope_id,source_type,mount_modes,target_kb,priority,max_items,max_tokens,kb_name,query',
    // 示例 1: KB
    '"[示例] KB→corpus","示例:复制法务库切片到主 KB",global,,kb,corpus,main_kb,5,200,1600,legal_kb,',
    // 示例 2: Web
    '"[示例] Web→context","示例:抓官网首页",user,,web,context,,0,50,1200,,',
  ];
  return lines.join('\n') + '\n';
}
