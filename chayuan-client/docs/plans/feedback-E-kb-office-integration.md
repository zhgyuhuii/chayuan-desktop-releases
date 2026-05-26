# Block E — 知识中心 ↔ 察元办公 双向打通

> 关联反馈点：14（知识中心可新建文档/表格/演示稿，保存自动入库）、15（知识中心文件可调用办公编辑器编辑）

## 0. 现状诊断

- **知识中心**：只能上传现成文件；不能在 KB 内"新建空白文档"
- **办公文档**：编辑后存到 `office_document` 表；不会自动写到任意 KB 的向量库
- 两个模块数据互不知晓，用户要"边写边沉淀进知识库"必须人工导出再上传，断流

## 1. 目标体验

### 1.1 在知识中心新建（反馈 14）

```
KB 详情页右上 → 「+ 新建」▼
  ├─ 上传文件…       (现有)
  ├─ 文件夹上传…     (现有)
  ├─ 远端同步…       (现有)
  ├─ ─────────────
  ├─ 新建文档 (.docx)
  ├─ 新建表格 (.xlsx)
  └─ 新建演示稿 (.pptx)
```

点击"新建文档" → 跳到办公编辑器（在新 Tab 中）→ 用户编辑 → 保存 → **自动入库到当前 KB**。

### 1.2 在 KB 列表内编辑（反馈 15）

每个 KB 内的文档卡片右键 / 详情页"⋮"菜单：

```
预览
─────
打开编辑器（察元办公）   ← 反馈 15
重新切片 / 重新嵌入
─────
下载
删除
```

打开编辑器：
- 把文件复制到 `office_document` 表（保留原 KB 关联）
- 跳转到 `/office/edit/<doc_id>?from=kb&kb_name=law_kb`
- 用户编辑 → 保存 → **同步**回 KB（重新切片 + 重新嵌入）

## 2. 设计要点

### 2.1 数据模型增量

`office_document` 表新增：
- `source: enum('user', 'kb', 'kb-edit')` —— 来源
- `kb_name: string | null` —— 关联 KB
- `kb_file_id: string | null` —— KB 内的文件 id
- `auto_sync: boolean` —— 编辑后是否自动回写 KB（默认 true）

### 2.2 API 增量

```http
# 反馈 14：在 KB 内创建空白文档并跳转办公编辑器
POST /knowledge_base/{kb_name}/new_blank
Body: { type: 'docx' | 'xlsx' | 'pptx', title?: string }
Resp: { doc_id: number, redirect: '/office/edit/{doc_id}?from=kb' }

# 反馈 15：把已有 KB 文件克隆为 office 文档供编辑
POST /knowledge_base/{kb_name}/files/{file_id}/edit
Resp: { doc_id: number, redirect: '/office/edit/{doc_id}?from=kb' }

# 办公保存时反向同步到 KB（已有 /office/docs/{id}/save 增加参数）
POST /office/docs/{doc_id}/save
Body: { content: ..., sync_to_kb: true }
```

### 2.3 后端流程

#### 反馈 14（新建 → 入库）

```
POST /knowledge_base/{kb}/new_blank type=docx
  ↓
1. 用 python-docx 生成空白 docx，写入 FileStorage 临时位
2. 在 office_document 写一行（source=kb, kb_name=kb, kb_file_id=null）
3. 返回 doc_id + 跳转 url
  ↓ 用户在编辑器编辑后
POST /office/docs/{id}/save (sync_to_kb=true)
  ↓
1. 写入 office_document.content
2. 异步触发 KB ingest:
   a. update_docs([{kb_name, file_path, file_chat_id}])  # 复用现有 KB 入库流程
   b. KB 切片 + 嵌入（用 KB.embed_model；与 Block B 一致）
   c. 写入 office_document.kb_file_id（关联）
3. 返回 200
```

#### 反馈 15（KB 文件 → 编辑 → 回写）

```
POST /knowledge_base/{kb}/files/{file_id}/edit
  ↓
1. 读取 KB 中文件（FileStorage / minio）
2. 复制到 office_document 表（source=kb-edit, kb_name, kb_file_id）
3. 跳转 url
  ↓ 用户编辑 + 保存
POST /office/docs/{id}/save (sync_to_kb=true)
  ↓
1. 写入 office_document
2. **同名覆盖**到 KB（删旧 chunk → 入新版）
   - 旧 chunks 软删除（保留 30 天审计）
   - 新 chunks 切片 + 嵌入
3. KB 文件版本号 +1（用户可看历史）
```

## 3. UX 关键点

### 3.1 入库进度

新建 / 编辑保存触发的 ingest 是异步的；用户应该看到状态：

```
[文档卡片底部状态条]
  📥 切片中… 30%
  🧠 嵌入中… 60%
  ✅ 已入库 (just now)
```

复用 Block B 的 SSE 进度帧，KB 详情页订阅。

### 3.2 同步开关

`auto_sync` 默认 ON，但用户可在编辑器顶栏关掉（"草稿模式"），关掉时保存只写 office_document，不入 KB。再次打开时一次性 sync。

### 3.3 命名冲突

新建文档时若 KB 内已有同名 → 自动加 `(2)` 后缀。

### 3.4 删除一致性

KB 内删除文件 → 同步软删除关联的 office_document（保留 30 天）；office 内删除文档（带 KB 关联）→ 提示"是否同时从 KB 移除"。

## 4. 实现关键

- `KBService.update_docs` 已有；复用即可，不改后端切分逻辑
- `office_document.content` 已有 `tsvector` 全文索引；与 KB 文件分开维护，不冲突
- KB 与 Office 的"统一文件 ID"用 `office_document.id` + `kb_file_id` 双向 FK 即可
- ingest 走异步队列：复用 `ingest_queue`（KB 远端同步同款），用户体验流畅

## 5. 工程量

| 项 | 工程量 |
| --- | --- |
| 后端：office_document 表新增字段（migration）| 0.1w |
| 后端：`POST /knowledge_base/{kb}/new_blank` | 0.2w |
| 后端：`POST /knowledge_base/{kb}/files/{id}/edit` | 0.2w |
| 后端：保存反向同步（async ingest） | 0.2w |
| 前端：KB 详情页"+ 新建"菜单 | 0.1w |
| 前端：KB 文件项"打开编辑器"按钮 + 跳转 | 0.1w |
| 前端：办公编辑器顶栏"自动同步" toggle + 入库进度条 | 0.1w |
| **合计** | **1w** |

## 6. 待你确认

- [ ] 新建文档默认存储位置：KB 关联还是独立 office 库？建议**默认 office 主存储 + KB 仅放 chunks**（避免 KB 存全量原文导致体积膨胀）。【按照建议】
- [ ] 编辑后回写 KB 是**全量替换**还是**增量 diff**？建议全量替换（简单可靠；切片成本低）。【全量替换】
- [ ] KB 文件已被 N 个应用引用（grants），编辑后入新版会不会破坏引用？建议**保留旧版本可回滚**（office_document 加 `version` 字段）。【保留旧版本可回滚*】
- [ ] 保存触发 KB 重切+重嵌入有几秒延迟，UX 怎么设计？建议**乐观提示"已保存（KB 同步中…）"**，进度走另一条 SSE 通知卡片。【乐观提示】
- [ ] PPT / Excel 的 KB 索引语义和 Word 不同（Sheet / Slide 维度）— 切分策略需要 KB 内部预设 file-type-aware（已在 Block B §1.2.1 提及，本块复用）。
