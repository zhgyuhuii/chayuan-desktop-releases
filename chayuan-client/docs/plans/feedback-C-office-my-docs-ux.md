# Block C — 察元办公「我的文档」UX 强化

> 关联反馈点：8（右键菜单 + inline 重命名）、9（顶部搜索）、10（右侧栏右键 + 拖拽）

## 0. 当前落地状态

- [x] 文档卡片支持更多菜单：重命名、移到分组、分享、下载、删除、触发向量化。
- [x] 文档名与分组名支持点击后内联输入，Enter 保存，Esc 取消。
- [x] 我的文档顶部搜索框已接入 `/office/search`，支持标题/描述与已索引内容融合搜索。
- [x] 右侧分组树支持文档拖入分组、分组拖到分组、分组拖回根目录。
- [x] 右侧栏支持真实右键菜单：空白/根目录新建分组；分组新建子分组、重命名、移到根目录、删除。

## 0.1 现状诊断

`packages/app/src/features/office/`：
- 早期缺口已补齐 P1 首版；后续重点转向键盘操作、批量导出、分组颜色、搜索结果分面与全文高亮。

用户痛点：交互重，鼠标操作多，达不到办公软件应有的"流畅 / 顺手"。

## 1. 目标交互（参考 Notion / VS Code Explorer）

### 1.1 右键菜单（反馈 8, 10）

**文档项右键**：

```
打开                Cmd+O
打开新窗口          Cmd+Shift+O
─────────
重命名              Enter
复制                Cmd+D
移动到分组 ▶
   工作 / 个人 / 已归档 / + 新建分组…
─────────
分享…
导出 ▶ (PDF / Word / Markdown)
─────────
归档
删除                Delete
```

**分组项右键**：

```
新建文档…
新建表格…
新建演示稿…
─────────
重命名              Enter
新建子分组
─────────
排序：按名称 / 按时间 / 自定义
颜色 ▶ (蓝 / 绿 / 紫 / 红 / 灰)
─────────
删除分组（保留文档）
删除分组与文档
```

**右侧栏空白处右键**：
```
新建分组…
─────────
全部展开
全部折叠
─────────
显示已归档
```

### 1.2 inline 重命名（反馈 8）

**触发**：双击文件名 / 分组名 / 右键 → 重命名 / 选中后按 `F2` `Enter`

**行为**：当前文件名变成 `<input>`，自动选中除扩展名外的部分（`合同审核.docx` → 选中 `合同审核`）；
- `Enter` / blur 提交
- `Esc` 取消（恢复原名）
- 提交时本地乐观更新，后端 `PATCH /office/docs/{id}` 失败回滚 + toast

**实现**：

```tsx
// InlineRename.tsx
export const InlineRename: React.FC<{
  value: string;
  onCommit: (next: string) => Promise<void> | void;
  className?: string;
  /** ".docx" 等扩展名 - 重命名时不会被选中 */
  ext?: string;
}> = ({ value, onCommit, ext, className }) => {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(value);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (editing && inputRef.current) {
      const el = inputRef.current;
      el.focus();
      // 选中除 ext 外的部分
      const stem = ext && el.value.endsWith(ext) ? el.value.slice(0, -ext.length) : el.value;
      el.setSelectionRange(0, stem.length);
    }
  }, [editing, ext]);

  if (!editing) {
    return (
      <span
        onDoubleClick={() => setEditing(true)}
        className={cn('cursor-text', className)}
      >
        {value}
      </span>
    );
  }
  return (
    <input
      ref={inputRef}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={async () => {
        if (draft !== value) await onCommit(draft);
        setEditing(false);
      }}
      onKeyDown={async (e) => {
        if (e.key === 'Enter') {
          if (draft !== value) await onCommit(draft);
          setEditing(false);
        }
        if (e.key === 'Escape') {
          setDraft(value);
          setEditing(false);
        }
      }}
      className={cn('rounded border border-[var(--cy-brand-300)] bg-white px-1 outline-none', className)}
    />
  );
};
```

### 1.3 顶部搜索（反馈 9）

**位置**：我的文档区顶栏右侧（与"新建文档"按钮平行）。

**能力**：
- **文件名** 命中：实时 fuzzy（200ms debounce）
- **文件内容** 命中：调 `office_routes` 的 `/office/search?q=` —— 后端用 PG 全文索引（已有 `office_document.tsv` 字段）
- **分组名** 命中：高亮分组项

**UI**：

```
[ 🔍 搜索文档…              ⌘K ]
            ↓ 输入"合同"
┌─搜索结果 (12)─────────────────┐
│  📄 合同审核流程.docx (名命中) │
│  📄 销售合同.xlsx     (名命中) │
│  📋 客户合同模板               │
│  ...                            │
│ ───────────────                │
│  内容命中 (3)                   │
│  📄 法务备忘 …含"合同条款修订" │
│  ...                            │
└─────────────────────────────────┘
```

`Cmd+K` 全局打开，跨"我的文档 / 知识中心 / AI 应用 / 任务"四类聚合搜索（升级版）。

### 1.4 拖拽移动（反馈 10）

**右侧分组栏**：分组顺序可拖拽（`react-dnd` 或原生 `draggable`）。

**文档移动到分组**：把文档卡片拖到分组项上 → 分组高亮 → 释放即移动。

**子分组**（可选）：拖一个分组到另一个分组上 → 嵌套（深度限制 2 层防止无尽嵌套）。

**实现**：

```ts
// useDocDrag.ts
function onDragStart(e: DragEvent, doc: Doc) {
  e.dataTransfer!.setData('cy/doc-id', doc.id);
  e.dataTransfer!.effectAllowed = 'move';
}
function onGroupDragOver(e: DragEvent) { e.preventDefault(); /* 高亮 */ }
function onGroupDrop(e: DragEvent, groupId: string) {
  const docId = e.dataTransfer!.getData('cy/doc-id');
  api.moveDocToGroup(docId, groupId);  // 乐观更新
}
```

**键盘可达性**：选中文档后 `Alt+→` 移到下个分组、`Alt+↑↓` 排序。

## 2. 后端契约增量

需要新增 / 增强：
- `POST /office/groups` —— 创建分组（已有？需确认）
- `PATCH /office/groups/{id}` —— 重命名 / 改颜色 / 改父级
- `DELETE /office/groups/{id}?keep_docs=true|false` —— 删除（保留 / 一起删）
- `PATCH /office/docs/{id}` 增加 `name`、`group_id`、`archived` 字段
- `GET /office/search?q=` —— 名 + 内容（PG `tsvector` 全文索引；中文 jieba）

## 3. 工程量

| 项 | 工程量 |
| --- | --- |
| `InlineRename` 通用组件 | 0.2w |
| `ContextMenu` 通用组件（已有则复用）| 0.1w |
| 我的文档右键菜单 + 操作链路（删除 / 移动 / 复制 / 归档 / 导出） | 0.3w |
| 右侧分组栏右键 + 新建 + 拖拽 | 0.3w |
| 顶部搜索（前端 UI + debounce）| 0.2w |
| 后端：搜索 / 分组 CRUD 增量 | 0.3w |
| Tauri / Web 拖拽体验差异适配 | 0.1w |
| **合计** | **1w** |

## 4. 待你确认

- [ ] 子分组深度限制 2 层 OK 吗？还是允许 N 层？【N层】
- [ ] 删除文档的语义：默认进"已归档"（30 天软删）还是直接删？【软删】
- [ ] 全局 `Cmd+K` 聚合搜索是本块做还是单独立项？建议**单独立项**（涉及多源融合）。【单独立项】
- [ ] 导出 PDF / Word / Markdown 走办公编辑器 vs server-side pandoc？后者快但服务端依赖 pandoc。【pandoc】
