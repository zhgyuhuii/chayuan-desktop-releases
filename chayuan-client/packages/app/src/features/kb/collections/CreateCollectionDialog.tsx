/**
 * 94-5:新建集合对话框 — 只填基础信息(name / display_name / description),
 * 创建后用 ManageMembersDialog 加成员。
 */
import * as React from 'react';
import { Boxes } from 'lucide-react';
import {
  Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input, Textarea,
} from '@chayuan/ui';
import { kbCollections } from '@chayuan/api';
import { reportError, notifySuccess } from '../../../store/errorDialog';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function CreateCollectionDialog({ open, onOpenChange, onCreated }: Props): React.JSX.Element {
  const [name, setName] = React.useState('');
  const [displayName, setDisplayName] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [creating, setCreating] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setName('');
      setDisplayName('');
      setDescription('');
    }
  }, [open]);

  const submit = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      await kbCollections.create({
        name: name.trim(),
        display_name: displayName.trim() || name.trim(),
        description: description.trim(),
      });
      notifySuccess('集合已创建,接下来可加成员');
      onCreated();
    } catch (e) {
      reportError(e as Error, '创建集合失败');
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Boxes className="h-5 w-5 text-primary" />
            新建混合集合
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">
              名称 <span className="text-destructive">*</span>
            </label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="proj_alpha(英文/数字/下划线)"
              autoFocus
            />
            <p className="mt-1 text-xs text-muted-foreground">
              系统内唯一标识。后端调用 / WPS 加载项也用 ``coll:&lt;name&gt;`` 来引用此集合。
            </p>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">显示名</label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="项目 Alpha(可中文)"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">描述</label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="项目相关的所有文档和图像"
              rows={3}
            />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={!name.trim() || creating}>
            {creating ? '创建中…' : '创建'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
