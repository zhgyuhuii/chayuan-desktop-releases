/**
 * 知识库管理页（/kb）。
 *
 * 三栏：
 *  - 左：KB 列表（含创建按钮 + 搜索）
 *  - 中：文件列表 + 上传 + 多选删除
 *  - 右：搜索调试 / KB 元信息
 *
 * 性能：
 * - 文件大上传走 Promise（后端可能切异步入库）；UI 显示进度提示但不阻塞列表。
 * - 文件多选用 Set 维护，避免 array indexOf O(n) 每次检查。
 */

import * as React from 'react';
import { Plus, Trash2, Upload, FileText, Search, Globe2, Lock, RefreshCw } from 'lucide-react';
import { Button, Input, cn } from '@chayuan/ui';
import { getPlatform } from '@chayuan/platform-shared';
import {
  useKbList,
  useCreateKb,
  useDeleteKb,
  useKbFiles,
  useUploadDocs,
  useDeleteDocs,
  useUpdateKbInfo,
} from './useKbAdmin';
import { kb as kbApi } from '@chayuan/api';

export const KbPage: React.FC = () => {
  const list = useKbList();
  const create = useCreateKb();
  const del = useDeleteKb();
  const updateInfo = useUpdateKbInfo();
  const [active, setActive] = React.useState<string | null>(null);
  const [filter, setFilter] = React.useState('');

  const filtered = React.useMemo(() => {
    const items = list.data ?? [];
    if (!filter.trim()) return items;
    const q = filter.toLowerCase();
    return items.filter(
      (k) => k.kb_name.toLowerCase().includes(q) || (k.kb_info ?? '').toLowerCase().includes(q),
    );
  }, [list.data, filter]);

  React.useEffect(() => {
    if (!active && filtered.length > 0) setActive(filtered[0]!.kb_name);
  }, [active, filtered]);

  return (
    <div className="flex h-full">
      {/* 左：KB 列表 */}
      <aside className="flex w-72 flex-shrink-0 flex-col border-r">
        <div className="flex items-center gap-2 border-b p-2">
          <Input
            placeholder="搜索知识库"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-8 text-xs"
          />
          <Button
            size="icon"
            variant="ghost"
            className="h-8 w-8"
            onClick={async () => {
              const name = window.prompt('知识库名称（英文 / 数字 / 下划线）')?.trim();
              if (!name) return;
              await create.mutateAsync({ knowledge_base_name: name, kb_info: '' });
              setActive(name);
            }}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {list.isLoading && <div className="p-4 text-xs text-muted-foreground">加载中…</div>}
          {!list.isLoading && filtered.length === 0 && (
            <div className="p-4 text-xs text-muted-foreground">无知识库</div>
          )}
          {filtered.map((k) => (
            <button
              key={k.kb_name}
              type="button"
              onClick={() => setActive(k.kb_name)}
              className={cn(
                'flex w-full flex-col items-start gap-0.5 border-b px-3 py-2 text-left transition-colors',
                active === k.kb_name ? 'bg-accent' : 'hover:bg-muted',
              )}
            >
              <div className="flex w-full items-center justify-between gap-2">
                <span className="flex items-center gap-1 truncate font-medium">
                  {k.visibility === 'public' ? <Globe2 className="h-3 w-3" /> : <Lock className="h-3 w-3" />}
                  {k.kb_name}
                </span>
                <span className="text-[10px] text-muted-foreground">{k.file_count ?? 0} 文件</span>
              </div>
              <span className="line-clamp-1 text-[11px] text-muted-foreground">{k.kb_info || '—'}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* 中：文件 */}
      <section className="flex flex-1 flex-col">
        {active ? (
          <KbDetail
            name={active}
            onRename={async (info) => {
              await updateInfo.mutateAsync({ knowledge_base_name: active, kb_info: info });
            }}
            onDeleteKb={async () => {
              // 用 platform.dialog.confirm(走 plugin-dialog ask),不要裸 confirm():
              // Tauri 2 webview 把 window.confirm 重定向到 plugin:dialog|confirm,
              // 在某些 Tauri 2.x 版本下即使声明了 dialog:allow-confirm 也会 ACL 拒,
              // 走 ask 走得通(见 platform-tauri/src/index.ts:733 注释)。
              const ok = await getPlatform().dialog.confirm(
                `删除知识库「${active}」?所有文件将被清除`,
                { title: '删除知识库' },
              );
              if (!ok) return;
              await del.mutateAsync(active);
              setActive(null);
            }}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            选择或创建一个知识库
          </div>
        )}
      </section>
    </div>
  );
};

const KbDetail: React.FC<{ name: string; onRename: (info: string) => void; onDeleteKb: () => void }> = ({
  name,
  onRename,
  onDeleteKb,
}) => {
  const files = useKbFiles(name);
  const upload = useUploadDocs(name);
  const delDocs = useDeleteDocs(name);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [searchQ, setSearchQ] = React.useState('');
  const [searchResults, setSearchResults] = React.useState<Array<{ page_content?: string; score?: number }>>([]);

  React.useEffect(() => setSelected(new Set()), [name]);

  const onUpload = async () => {
    const picked = await getPlatform().fs.pickFiles({ multiple: true });
    if (!picked.length) return;
    await upload.mutateAsync(picked);
  };

  const onDoSearch = async () => {
    if (!searchQ.trim()) return;
    const r = await kbApi.searchDocs({ query: searchQ.trim(), knowledge_base_name: name, top_k: 5 });
    setSearchResults(Array.isArray(r) ? r : []);
  };

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b p-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{name}</h2>
          <p className="truncate text-xs text-muted-foreground">{(files.data?.length ?? 0)} 个文件</p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setSearchOpen((v) => !v)}>
            <Search className="h-3.5 w-3.5" />
            检索调试
          </Button>
          <Button size="sm" variant="outline" onClick={() => files.refetch()}>
            <RefreshCw className={cn('h-3.5 w-3.5', files.isFetching && 'animate-spin')} />
          </Button>
          <Button size="sm" onClick={onUpload} disabled={upload.isPending}>
            <Upload className="h-3.5 w-3.5" />
            {upload.isPending ? '上传中…' : '上传'}
          </Button>
          <Button size="sm" variant="ghost" className="text-destructive" onClick={onDeleteKb}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {searchOpen && (
        <div className="border-b bg-muted/30 p-3">
          <div className="flex gap-2">
            <Input
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="检索 query"
              className="h-8 text-xs"
              onKeyDown={(e) => e.key === 'Enter' && void onDoSearch()}
            />
            <Button size="sm" onClick={() => void onDoSearch()}>检索</Button>
          </div>
          <div className="mt-2 space-y-2">
            {searchResults.map((r, i) => (
              <div key={i} className="rounded border bg-background p-2 text-xs">
                <div className="mb-1 text-[10px] font-medium text-muted-foreground">
                  score {(r.score ?? 0).toFixed(3)}
                </div>
                <div className="line-clamp-3 whitespace-pre-wrap text-foreground/80">{r.page_content}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {files.isLoading && <div className="p-4 text-xs text-muted-foreground">加载中…</div>}
        {!files.isLoading && (files.data?.length ?? 0) === 0 && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            还没有文件。点右上角上传，或拖拽文件到这里。
          </div>
        )}
        <table className="w-full text-sm">
          <tbody>
            {files.data?.map((f) => (
              <tr key={f.file_name} className="border-b hover:bg-muted/30">
                <td className="w-8 px-3 py-2">
                  <input
                    type="checkbox"
                    checked={selected.has(f.file_name)}
                    onChange={(e) =>
                      setSelected((s) => {
                        const next = new Set(s);
                        if (e.target.checked) next.add(f.file_name);
                        else next.delete(f.file_name);
                        return next;
                      })
                    }
                  />
                </td>
                <td className="px-2 py-2">
                  <FileText className="inline h-4 w-4 text-muted-foreground" />
                </td>
                <td className="px-2 py-2 font-medium">{f.file_name}</td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {f.in_db ? `已索引 · ${f.doc_count ?? 0} chunks` : '未索引'}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                  {f.file_size ? `${(f.file_size / 1024).toFixed(1)} KB` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected.size > 0 && (
        <footer className="flex items-center justify-between border-t bg-muted/30 px-4 py-2 text-sm">
          <span>已选 {selected.size} 个</span>
          <Button
            size="sm"
            variant="destructive"
            onClick={async () => {
              const ok = await getPlatform().dialog.confirm(
                `删除选中的 ${selected.size} 个文件?`,
                { title: '删除文件' },
              );
              if (!ok) return;
              await delDocs.mutateAsync([...selected]);
              setSelected(new Set());
            }}
          >
            删除
          </Button>
        </footer>
      )}
    </div>
  );
};
