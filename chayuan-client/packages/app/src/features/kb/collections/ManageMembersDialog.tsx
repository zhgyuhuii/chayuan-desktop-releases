/**
 * 94-5:管理集合成员对话框。
 *
 * 列表 + 加成员表单。成员 ku_id 必须与集合 owner 一致(94 决策 2),
 * 后端校验,前端只显示错误。
 */
import * as React from 'react';
import { FileText, Images, Plus, Trash2 } from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input,
  cn,
} from '@chayuan/ui';
import {
  kbCollections, knowledgeUniverse,
  type KbCollection, type KbCollectionMember, type KuItem, type KuKind,
} from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';
import { getPlatform } from '@chayuan/platform-shared';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  collection: KbCollection;
  members: KbCollectionMember[];
  onChanged: () => void;
}

export function ManageMembersDialog({
  open, onOpenChange, collection, members, onChanged,
}: Props): React.JSX.Element {
  const [kuList, setKuList] = React.useState<KuItem[]>([]);
  const [pickKuId, setPickKuId] = React.useState('');
  const [pickKind, setPickKind] = React.useState<'document' | 'image'>('document');
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    let alive = true;
    void Promise.all([
      knowledgeUniverse.list('document'),
      knowledgeUniverse.list('image'),
    ]).then(([docs, imgs]) => {
      if (!alive) return;
      // 过滤已是别的集合成员的(后端会再校验,前端先 hint)
      const existingKuIds = new Set(members.map((m) => m.ku_id));
      const out = [...docs, ...imgs].filter(
        (k) => !existingKuIds.has(k.ku_id),
      );
      setKuList(out);
    }).catch((e) => reportError(e as Error, '加载 KB 列表失败'));
    return () => { alive = false; };
  }, [open, members]);

  const handleAdd = async () => {
    if (!pickKuId) return;
    setBusy(true);
    try {
      await kbCollections.addMember(collection.id, {
        ku_id: pickKuId, kind: pickKind,
      });
      notifySuccess('已加入集合');
      setPickKuId('');
      onChanged();
    } catch (e) {
      reportError(e as Error, '加成员失败');
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (m: KbCollectionMember) => {
    const ok = await getPlatform().dialog.confirm(
      `从集合中移除「${m.ku_id}」?\n(只解绑不删 KB)`,
      { title: '移除成员' },
    );
    if (!ok) return;
    setBusy(true);
    try {
      await kbCollections.removeMember(collection.id, m.ku_id);
      notifySuccess('已移除');
      onChanged();
    } catch (e) {
      reportError(e as Error, '移除失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            管理成员 — {collection.display_name || collection.name}
          </DialogTitle>
        </DialogHeader>

        {/* 现有成员列表 */}
        <div className="space-y-2">
          <h3 className="text-sm font-medium">成员({members.length})</h3>
          {members.length === 0 ? (
            <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
              空集合 — 加成员让搜索能跨多个 KB
            </p>
          ) : (
            <ul className="space-y-1">
              {members.map((m) => (
                <li key={m.id}
                    className="flex items-center justify-between rounded border px-2 py-1.5">
                  <div className="flex items-center gap-2">
                    {m.kind === 'document'
                      ? <FileText className="h-4 w-4 text-blue-600" />
                      : <Images className="h-4 w-4 text-purple-600" />}
                    <span className="font-mono text-sm">{m.ku_id}</span>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => void handleRemove(m)}
                          disabled={busy}>
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 加新成员 */}
        <div className="mt-4 border-t pt-4">
          <h3 className="mb-2 text-sm font-medium">加成员</h3>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={pickKuId}
              onChange={(e) => {
                setPickKuId(e.target.value);
                const k = kuList.find((x) => x.ku_id === e.target.value);
                if (k && (k.kind === 'document' || k.kind === 'image')) {
                  setPickKind(k.kind);
                }
              }}
              className="flex-1 rounded border bg-background px-2 py-1.5 text-sm"
            >
              <option value="">选一个 KB(必须 owner 与集合一致)</option>
              {kuList.map((k) => (
                <option key={k.ku_id} value={k.ku_id}>
                  [{k.kind === 'document' ? '文档' : '图像'}] {k.display_name || k.name}
                </option>
              ))}
            </select>
            <Button onClick={() => void handleAdd()}
                    disabled={!pickKuId || busy}>
              <Plus className="h-4 w-4" />
              加入
            </Button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Owner 不一致时后端会拒绝。Image KB ku_id 形如 ``src:&lt;id&gt;``。
          </p>
        </div>

        <div className="mt-4 flex justify-end">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            完成
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
