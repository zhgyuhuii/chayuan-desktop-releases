/**
 * 94-5:混合集合主页面。
 *
 * 顶部:[新建集合] 按钮 + 列表筛选
 * 主区:集合卡片网格,每张卡显示 display_name + 文档/图像 子库计数 + 操作
 *
 * 集合 = 同 owner 名下若干 doc-KB + image-KB 的绑定关系(94-1 后端已落地)。
 */
import * as React from 'react';
import { Boxes, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input, cn } from '@chayuan/ui';
import { kbCollections, type KbCollection } from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';
import { KbCollectionCard } from './KbCollectionCard';
import { CreateCollectionDialog } from './CreateCollectionDialog';
import { getPlatform } from '@chayuan/platform-shared';

export function KbCollectionsPage(): React.JSX.Element {
  const [collections, setCollections] = React.useState<KbCollection[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [showCreate, setShowCreate] = React.useState(false);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    try {
      const items = await kbCollections.list();
      setCollections(items);
    } catch (e) {
      reportError(e as Error, '加载混合集合失败');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { void refresh(); }, [refresh]);

  const handleDelete = async (id: number, name: string) => {
    const ok = await getPlatform().dialog.confirm(
      `确定删除集合「${name}」?\n所有子 KB(文档+图像)会被一并删除。此操作不可撤销。`,
      { title: '删除集合' },
    );
    if (!ok) return;
    try {
      const ret = await kbCollections.remove(id);
      const errCount = (ret.errors as unknown[])?.length || 0;
      if (errCount > 0) {
        notifySuccess(`集合已删除,但 ${errCount} 个子 KB 删除失败(看服务端日志)`);
      } else {
        notifySuccess('集合及全部子 KB 已删除');
      }
      void refresh();
    } catch (e) {
      reportError(e as Error, '删除集合失败');
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Boxes className="h-5 w-5 text-primary" />
            混合知识库集合
          </h2>
          <p className="text-sm text-muted-foreground">
            把同 owner 的文档库 + 图像库绑成一个集合,搜索时自动并发查全部子库。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void refresh()}
                  disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            刷新
          </Button>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            新建集合
          </Button>
        </div>
      </header>

      {!loading && collections.length === 0 && (
        <div className="rounded-md border border-dashed p-8 text-center text-muted-foreground">
          <Boxes className="mx-auto mb-2 h-8 w-8 opacity-50" />
          <p>还没有集合。点【新建集合】绑定一个文档库 + 一个图像库,
            搜索时它们会同时被查到。</p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {collections.map((c) => (
          <KbCollectionCard
            key={c.id}
            collection={c}
            onDelete={() => void handleDelete(c.id, c.display_name || c.name)}
            onChanged={() => void refresh()}
          />
        ))}
      </div>

      <CreateCollectionDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        onCreated={() => { setShowCreate(false); void refresh(); }}
      />
    </div>
  );
}
