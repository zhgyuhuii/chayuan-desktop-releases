/**
 * KbAdminMenu —— 知识库的 owner-only 操作菜单。
 *
 * 提供:
 *   - 编辑信息(display_name / description / visibility)
 *   - 编辑可见范围(只对结构化:sql/mongo/es)→ SchemaScopePicker
 *   - 删除
 *
 * 路由:
 *   - kuId = 'doc:<kb_name>' → 走 kb.updateInfo / kb.delete(documents)
 *   - kuId = 'src:<id>'      → 走 knowledgeSource.update / knowledgeSource.delete
 *
 * 设计:
 *   - 只在 mine=true 时挂载;非 owner 不显示该按钮
 *   - 菜单项点击都是 dialog/confirm;不直接做破坏性操作
 *   - 任意 mutate 完都 invalidate ku.list + ku.detail,UI 自动同步
 */

import * as React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Edit3,
  MoreHorizontal,
  Settings2,
  Trash2,
} from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Input,
  Textarea,
  cn,
} from '@chayuan/ui';
import {
  kb as kbApi,
  knowledgeSource,
  type KuDetail,
} from '@chayuan/api';
import { reportError, notifySuccess } from '../../store/errorDialog';
import {
  SchemaScopePicker,
  scopeKindFor,
  sourceIdFromKuId,
} from './SchemaScopePicker';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';
import { getPlatform } from '@chayuan/platform-shared';

export interface KbAdminMenuProps {
  detail: KuDetail;
  /** 删除成功 / 改名成功后的回调 — 通常用来关 detail tab / 跳回 /kb */
  onDeleted?(): void;
}

export const KbAdminMenu: React.FC<KbAdminMenuProps> = ({ detail, onDeleted }) => {
  const [editOpen, setEditOpen] = React.useState(false);
  const [scopeOpen, setScopeOpen] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState(false);
  const qc = useQueryClient();

  const isDoc = detail.kind === 'document';
  const isStructured = detail.kind === 'structured';
  const sourceId = isStructured ? sourceIdFromKuId(detail.ku_id) : null;
  const scopeKind = scopeKindFor((detail as { sub_kind?: string }).sub_kind);

  const onDelete = async () => {
    const ok0 = await getPlatform().dialog.confirm(
      `确定删除知识库「${detail.meta.display_name}」?\n` +
        (isDoc
          ? '所有文件 + 向量数据将被清除,该操作不可恢复。'
          : '数据源记录(连接信息 + 白名单 + 缓存)将被删除,但远端真实数据库不动。'),
      { title: '删除知识库' },
    );
    if (!ok0) return;
    setDeletePending(true);
    try {
      if (isDoc) {
        await kbApi.delete(detail.meta.name);
      } else {
        // src:42 → 42
        const id = sourceIdFromKuId(detail.ku_id);
        if (id == null) throw new Error('非法 ku_id');
        await knowledgeSource.delete(id);
      }
      notifySuccess('已删除');
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
      void qc.invalidateQueries({ queryKey: ['ku.detail', detail.ku_id] });
      onDeleted?.();
    } catch (e) {
      reportError(e, '删除失败');
    } finally {
      setDeletePending(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label="更多操作"
            title="更多操作"
            className="relative h-8 w-8 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
          >
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[180px]">
          <DropdownMenuItem onClick={() => setEditOpen(true)}>
            <Edit3 className="mr-2 h-3.5 w-3.5" /> 编辑信息
          </DropdownMenuItem>
          {isStructured && sourceId != null && (
            <DropdownMenuItem onClick={() => setScopeOpen(true)}>
              <Settings2 className="mr-2 h-3.5 w-3.5" /> 编辑可见范围
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={onDelete} disabled={deletePending} className="text-destructive">
            {deletePending ? (
              <AssistantBrandLogo running className="mr-2 h-3.5 w-3.5 rounded-full" />
            ) : (
              <Trash2 className="mr-2 h-3.5 w-3.5" />
            )}
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <EditKbInfoDialog detail={detail} open={editOpen} onOpenChange={setEditOpen} />

      {isStructured && sourceId != null && (
        <SchemaScopePicker
          sourceId={sourceId}
          kind={scopeKind}
          open={scopeOpen}
          onOpenChange={setScopeOpen}
        />
      )}
    </>
  );
};

// ──────────────────────────────────────────────────────────────
// Edit info dialog(display_name / description / visibility)
// ──────────────────────────────────────────────────────────────

interface EditKbInfoDialogProps {
  detail: KuDetail;
  open: boolean;
  onOpenChange(open: boolean): void;
}

export const EditKbInfoDialog: React.FC<EditKbInfoDialogProps> = ({ detail, open, onOpenChange }) => {
  const qc = useQueryClient();
  const [displayName, setDisplayName] = React.useState(detail.meta.display_name);
  const [description, setDescription] = React.useState(detail.meta.description ?? '');
  const [visibility, setVisibility] = React.useState<'private' | 'public'>(
    (detail.meta.visibility === 'public' ? 'public' : 'private') as 'private' | 'public',
  );
  const isDoc = detail.kind === 'document';
  const sourceId = !isDoc ? sourceIdFromKuId(detail.ku_id) : null;

  React.useEffect(() => {
    if (!open) return;
    setDisplayName(detail.meta.display_name);
    setDescription(detail.meta.description ?? '');
    setVisibility((detail.meta.visibility === 'public' ? 'public' : 'private') as 'private' | 'public');
  }, [open, detail]);

  const save = useMutation({
    mutationFn: async () => {
      if (isDoc) {
        // 文档 KB 当前后端只暴露 update_info(kb_info),没有 visibility/display_name
        // 端点;先把 description 落上,visibility 等待后端补能力。
        await kbApi.updateInfo({
          knowledge_base_name: detail.meta.name,
          kb_info: description,
        });
      } else {
        if (sourceId == null) throw new Error('非法 ku_id');
        await knowledgeSource.update(sourceId, {
          display_name: displayName.trim() || detail.meta.name,
          description,
          visibility,
        });
      }
    },
    onSuccess: () => {
      notifySuccess('已保存');
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
      void qc.invalidateQueries({ queryKey: ['ku.detail', detail.ku_id] });
      onOpenChange(false);
    },
    onError: (e) => reportError(e, '保存失败'),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>编辑知识库信息</DialogTitle>
          <DialogDescription>
            {isDoc ? '文档库当前后端仅支持改简介;改名 / 公私需后续版本' : '数据源支持改名 / 简介 / 可见性'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-[var(--cy-text-secondary)]">显示名</label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              disabled={isDoc}
              placeholder={detail.meta.name}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-[var(--cy-text-secondary)]">简介</label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="可选"
            />
          </div>
          {!isDoc && (
            <div>
              <label className="text-xs font-medium text-[var(--cy-text-secondary)]">可见性</label>
              <div className="mt-1 flex gap-2">
                {(['private', 'public'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setVisibility(v)}
                    className={cn(
                      'rounded-full border px-3 py-1 text-xs transition-colors',
                      visibility === v
                        ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
                        : 'border-[var(--cy-border-default)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
                    )}
                  >
                    {v === 'private' ? '私有' : '公开'}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={save.isPending}>
            取消
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <AssistantBrandLogo running className="mr-1.5 h-3.5 w-3.5 rounded-full" /> : null}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
