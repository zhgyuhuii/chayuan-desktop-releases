/**
 * SchemaScopePicker —— 选择结构化数据源可见的表 / 集合 / 索引。
 *
 * 设计:
 *   - 空选 = 默认查询全部(后端语义,在 UI 上以"全部"标签明示)
 *   - 用 GET /knowledge_source/{id}/catalog 拉 names + 回填当前 allowed
 *   - 全选 / 清空 / 反选 / 关键字搜索
 *   - 保存走 PATCH /{id}/allowed,后端会级联失效 schema/template/result 缓存
 *
 * 复用:
 *   - 在 KbDetailHeader 的 "编辑可见范围" 入口里以 dialog 形式呈现
 *   - 在 AddStructuredSourceDialog 创建成功后,作为后置步骤呈现
 *   - 设置面板编辑数据源时也可作为子组件嵌入
 *
 * 性能 / 并发:
 *   - react-query 缓存 ['ks.catalog', sourceId];切回页不重拉
 *   - keyword 在 client 侧本地过滤,大表用 useMemo 避开 re-render 漏斗
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, RefreshCw, Search, X } from 'lucide-react';
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
import { knowledgeSource } from '@chayuan/api';
import { reportError, notifySuccess } from '../../store/errorDialog';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';

export type ScopeKind = 'tables' | 'collections' | 'indices';

const KIND_LABEL: Record<ScopeKind, string> = {
  tables: '表',
  collections: '集合',
  indices: '索引',
};

export interface SchemaScopePickerProps {
  /** 数据源 source id(注意 NOT ku_id;调用方负责从 ku_id 解出 src:<id>) */
  sourceId: number;
  /** 走的是哪一套白名单:sql → tables / mongo → collections / es → indices */
  kind: ScopeKind;
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 保存成功后回调(便于父级 invalidate 详情查询等) */
  onSaved?(allowed: string[]): void;
}

export const SchemaScopePicker: React.FC<SchemaScopePickerProps> = ({
  sourceId,
  kind,
  open,
  onOpenChange,
  onSaved,
}) => {
  const qc = useQueryClient();
  const [keyword, setKeyword] = React.useState('');
  const [draft, setDraft] = React.useState<Set<string>>(new Set());

  const catalog = useQuery({
    queryKey: ['ks.catalog', sourceId],
    queryFn: () => knowledgeSource.catalog(sourceId),
    enabled: open,
    staleTime: 30 * 1000,
  });

  // 打开 dialog 时把当前 allowed 灌成 draft;每次刷新拉到最新也要同步一次
  React.useEffect(() => {
    if (!open || !catalog.data) return;
    const cur =
      kind === 'tables'
        ? catalog.data.allowed_tables
        : kind === 'collections'
          ? catalog.data.allowed_collections
          : catalog.data.allowed_indices;
    setDraft(new Set(cur ?? []));
  }, [open, catalog.data, kind]);

  // 关键字过滤本地做,大目录(几百张表)无压力
  const filtered = React.useMemo(() => {
    const all = catalog.data?.names ?? [];
    if (!keyword.trim()) return all;
    const q = keyword.toLowerCase();
    return all.filter((n) => n.toLowerCase().includes(q));
  }, [catalog.data, keyword]);

  const allCount = catalog.data?.names?.length ?? 0;
  const isAllMode = draft.size === 0; // 空 = 全部

  const toggle = (name: string) => {
    setDraft((s) => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const onSelectAllVisible = () => {
    setDraft((s) => {
      const next = new Set(s);
      for (const n of filtered) next.add(n);
      return next;
    });
  };
  const onClearAll = () => setDraft(new Set());
  const onInvert = () => {
    setDraft((s) => {
      const next = new Set<string>();
      for (const n of filtered) {
        if (!s.has(n)) next.add(n);
      }
      // 保留 keyword 范围之外的原选中
      for (const n of s) {
        if (!filtered.includes(n)) next.add(n);
      }
      return next;
    });
  };

  const save = useMutation({
    mutationFn: () =>
      knowledgeSource.patchAllowed(sourceId, {
        [kind]: Array.from(draft),
      } as { tables?: string[]; collections?: string[]; indices?: string[] }),
    onSuccess: () => {
      notifySuccess(
        isAllMode
          ? `已取消范围限制,默认查询全部${KIND_LABEL[kind]}`
          : `已保存 ${draft.size} 个${KIND_LABEL[kind]}`,
      );
      // 失效相关 query:catalog 自身 + KU detail(white list 改后 list_tables 也变)
      void qc.invalidateQueries({ queryKey: ['ks.catalog', sourceId] });
      void qc.invalidateQueries({ queryKey: ['ku.detail'] });
      onSaved?.(Array.from(draft));
      onOpenChange(false);
    },
    onError: (e) => reportError(e, '保存范围失败'),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] w-[92vw] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle>选择可见{KIND_LABEL[kind]}</DialogTitle>
          <DialogDescription>
            不选则默认查询全部 {KIND_LABEL[kind]};选中后只在白名单内查询(语义对齐
            sql/mongo/es 三种 connector)。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-shrink-0 items-center gap-2 px-1 pt-1">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={`搜索 ${KIND_LABEL[kind]} 名`}
              className="pl-7"
            />
          </div>
          <Button
            size="icon"
            variant="ghost"
            disabled={catalog.isFetching}
            onClick={() => catalog.refetch()}
            title="重新探测"
            aria-label="重新探测"
            className="h-8 w-8"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', catalog.isFetching && 'animate-spin')} />
          </Button>
        </div>

        <div className="flex flex-shrink-0 items-center justify-between gap-2 px-1 py-2 text-[11px] text-[var(--cy-text-tertiary)]">
          <span>
            共 {allCount} 项;已选 <b className="text-[var(--cy-text-primary)]">{draft.size}</b>
            {isAllMode && (
              <span className="ml-2 rounded-full bg-emerald-50 px-1.5 py-0.5 text-emerald-700">
                ✓ 默认全部
              </span>
            )}
          </span>
          <div className="flex items-center gap-1">
            <button type="button" onClick={onSelectAllVisible} className="rounded px-2 py-0.5 hover:bg-[var(--cy-surface-2)]">
              全选(可见)
            </button>
            <button type="button" onClick={onInvert} className="rounded px-2 py-0.5 hover:bg-[var(--cy-surface-2)]">
              反选
            </button>
            <button type="button" onClick={onClearAll} className="rounded px-2 py-0.5 hover:bg-[var(--cy-surface-2)]">
              清空
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-[var(--cy-border-subtle)]">
          {catalog.isLoading ? (
            <div className="flex h-32 items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
              <AssistantBrandLogo running className="mr-1.5 h-3.5 w-3.5 rounded-full" /> 加载目录…
            </div>
          ) : catalog.isError ? (
            <div className="p-4 text-xs text-red-600">
              拉取目录失败:{(catalog.error as Error)?.message ?? '未知错误'}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex h-32 items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
              {keyword ? '没有匹配' : '该数据源没有可见的项'}
            </div>
          ) : (
            <ul className="divide-y divide-[var(--cy-border-subtle)] text-sm">
              {filtered.map((name) => {
                const checked = draft.has(name);
                return (
                  <li key={name}>
                    <button
                      type="button"
                      onClick={() => toggle(name)}
                      className={cn(
                        'flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors',
                        checked
                          ? 'bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                          : 'text-[var(--cy-text-primary)] hover:bg-[var(--cy-surface-1)]',
                      )}
                    >
                      <span
                        className={cn(
                          'flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border',
                          checked
                            ? 'border-[var(--cy-brand-600)] bg-[var(--cy-brand-600)] text-white'
                            : 'border-[var(--cy-border-default)] bg-white',
                        )}
                      >
                        {checked && <Check className="h-3 w-3" />}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={save.isPending}>
            取消
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <AssistantBrandLogo running className="mr-1.5 h-3.5 w-3.5 rounded-full" /> : null}
            {isAllMode ? '保存(默认全部)' : `保存 (${draft.size})`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/** 把 ku_id (形如 'src:42') 解为 sourceId(int);文档 KB 'doc:xxx' 返 null */
export function sourceIdFromKuId(kuId: string): number | null {
  if (!kuId.startsWith('src:')) return null;
  const n = Number.parseInt(kuId.slice(4), 10);
  return Number.isFinite(n) ? n : null;
}

/** 由 KuKind/sub_kind 决定该用哪一套 allowed_* 字段 */
export function scopeKindFor(subKind: string | undefined): ScopeKind {
  switch ((subKind ?? '').toLowerCase()) {
    case 'mongo':
      return 'collections';
    case 'es':
      return 'indices';
    default:
      return 'tables';
  }
}
