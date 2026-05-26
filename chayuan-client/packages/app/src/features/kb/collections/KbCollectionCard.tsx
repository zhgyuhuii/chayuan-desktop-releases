/**
 * 94-5:集合卡片。
 *
 * 显示:display_name + 描述 + 文档/图像 数量 chip + 操作按钮(管理成员 / 删除)。
 */
import * as React from 'react';
import { Boxes, FileText, Images, MoreHorizontal, Settings2, Trash2 } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { kbCollections, type KbCollection, type KbCollectionMember } from '@chayuan/api';
import { reportError } from '../../../store/errorDialog';
import { ManageMembersDialog } from './ManageMembersDialog';

interface Props {
  collection: KbCollection;
  onDelete: () => void;
  onChanged: () => void;
}

export function KbCollectionCard({ collection, onDelete, onChanged }: Props): React.JSX.Element {
  const [members, setMembers] = React.useState<KbCollectionMember[]>(
    collection.members ?? [],
  );
  const [showMembers, setShowMembers] = React.useState(false);

  React.useEffect(() => {
    // detail 拉一次拿真实成员清单
    let alive = true;
    void kbCollections.detail(collection.id)
      .then((d) => { if (alive) setMembers(d.members ?? []); })
      .catch(() => {});
    return () => { alive = false; };
  }, [collection.id]);

  const docCount = members.filter((m) => m.kind === 'document').length;
  const imgCount = members.filter((m) => m.kind === 'image').length;

  return (
    <div className={cn(
      'rounded-md border bg-card p-4 transition-shadow hover:shadow-md',
    )}>
      <div className="flex items-start gap-2">
        <div className="rounded bg-primary/10 p-2">
          <Boxes className="h-5 w-5 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-medium">
            {collection.display_name || collection.name}
          </h3>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {collection.description || '混合集合(文档 + 图像)'}
          </p>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-700">
          <FileText className="h-3 w-3" />
          {docCount} 文档
        </span>
        <span className="inline-flex items-center gap-1 rounded bg-purple-50 px-1.5 py-0.5 text-xs text-purple-700">
          <Images className="h-3 w-3" />
          {imgCount} 图像
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <Button variant="ghost" size="sm" onClick={() => setShowMembers(true)}>
          <Settings2 className="h-4 w-4" />
          管理成员
        </Button>
        <Button variant="ghost" size="sm" onClick={onDelete}
                className="text-destructive hover:text-destructive">
          <Trash2 className="h-4 w-4" />
          删除
        </Button>
      </div>

      <ManageMembersDialog
        open={showMembers}
        onOpenChange={setShowMembers}
        collection={collection}
        members={members}
        onChanged={() => {
          onChanged();
          // 重拉成员
          void kbCollections.detail(collection.id).then((d) => {
            setMembers(d.members ?? []);
          });
        }}
      />
    </div>
  );
}
