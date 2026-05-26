# 知识库创建 & 拖拽上传 — Phase 5

> 目标:把"添加 KB"占位换成真实的多类型创建流程,卡片支持拖拽上传 + 实时进度条。
> 站在架构师 + UE 的角度做模块化、可复用、高并发实现。

## 0. 现状

- 后端**全部就绪**:
  - `POST /knowledge_base/create_knowledge_base` — 创建文档型 KB
  - `POST /knowledge_base/upload_docs` — 上传文档(含图片) + 自动向量化
  - `POST /knowledge_source/` 支持 `kind=image / sql / mongo / es / vector / vs`
  - `POST /knowledge_source/{id}/image/upload` — 图像入索引(自动 embed)
- 前端缺:
  - 创建只有 `notifyInfo` 占位 + 已有的 `AddStructuredSourceDialog`
  - 上传走 `fetch` 没法拿 progress 事件
  - 卡片没有 drop-zone / 进度条

## 1. 架构

```
features/kb/
├── upload/                          [新] 上传 + 进度抽象
│   ├── uploadProgress.ts            XHR-based upload(api 层),返 Promise + onProgress
│   ├── kbUploadStore.ts             zustand:ku_id → UploadJob[]
│   ├── useKbUpload.ts               hook,把 File[] 投递到队列 + 失效查询
│   └── KbDropZone.tsx               卡片包裹的 drop 容器,hover 视觉 + 拒绝不支持类型
├── create/                          [新] 多类型创建
│   ├── CreateKbDialog.tsx           容器:kind picker + 路由到子表单
│   ├── DocumentKbForm.tsx           name + kb_info + visibility(+ vs_type 高级)
│   ├── ImageKbForm.tsx              name + kb_info + 嵌入模型 + visibility
│   ├── VectorKbForm.tsx             外部向量库连接(host/port/auth + 探测)
│   └── useCreateKb.ts               4 类创建 mutation,统一 onSuccess 失效列表
└── components/
    └── KbProgressBar.tsx            [新] 卡片底部进度条,聚合多文件
```

复用既有:
- `AddStructuredSourceDialog`(sql/mongo/es)— 不动,被 CreateKbDialog 当作 sub-form 调起
- `imageSource.upload` / `kb.uploadDocs`(API)— 升级为 XHR 版获得 progress

## 2. 关键设计决策

### 2.1 XHR vs fetch
浏览器 `fetch` API **不支持 upload progress 事件**(只支持 download 端的 reader)。要做"上传 30%"的进度条**必须**走 `XMLHttpRequest`。封装在 `api/uploadProgress.ts`,与既有 `request()` 并列,不污染主链路。

### 2.2 并发上限
- 单 KB:**最多 3 个文件并发上传**(队列 FIFO);超出排队等待
- 全局:不限(每 KB 独立队列)
- 这样既能榨干带宽,又不会让大批量上传把单 KB 的 connection pool 打死

### 2.3 进度状态:zustand 而非 RQ
RQ 的 mutation state 一次只对应一个调用,不适合"多文件 + 队列 + 实时百分比"。用 zustand 维护:
```ts
interface UploadJob {
  jobId: string;
  kuId: string;
  fileName: string;
  fileSize: number;
  loaded: number;          // 已上传字节
  status: 'queued' | 'uploading' | 'ok' | 'error';
  error?: string;
  startedAt?: number;
}
```
`useKbProgress(kuId)` selector 只订阅该 kuId 的 jobs → 单卡片重渲不会牵连其他卡片。

### 2.4 Drop 仅在支持类型上接受
Doc / Image KB 接 drop;Structured / Vector 弹 toast 拒绝。`KbDropZone` 拿 `kind` prop 自决定是否 enable drop 监听 + hover 视觉。

### 2.5 失效策略
上传成功后:
- doc KB:invalidate `['ku.detail', kuId]` → 文件列表更新
- image KB:同上
- 都同时 invalidate `['ku.list']` → 卡片右下文件数 / count 更新

不在 token / 文件级 invalidate(避免抖动)。

### 2.6 文档 KB 接受图片
后端 `upload_docs` 早就支持任何 mimetype(切片 + embed),不需要改后端。前端 drop accept 设 `'*/*'`,doc KB 不限制类型。Image KB 限 `image/*`。

## 3. 数据流(写后即时反馈)

```
用户拖文件 → KbDropZone.onDrop
   └─ useKbUpload(kuId, kind).submit(files)
        ├─ 立即给 store 添 N 条 jobs(status='queued')
        ├─ 队列调度器(并发 3):take queued → status='uploading'
        │    └─ uploadProgress(formData, onProgress)
        │         └─ XHR.upload.onprogress → store.updateLoaded(jobId, loaded)
        │         └─ XHR.onload → store.markOk(jobId) + qc.invalidate(...)
        │         └─ XHR.onerror → store.markError(jobId, msg)
        └─ 自动出队下一个
卡片底部 KbProgressBar 订阅 useKbProgress(kuId) → 聚合渲染
```

UE:
- 拖入卡片瞬间 brand 蓝虚线边框 + "拖入 N 个文件到 [KB 名]" 浮层
- 上传中:卡片底部 2px 进度条 + "X/Y 文件 · ZZ%" 副标题
- 完成后 800ms 淡出条;失败的 job 显示红色 + 重试按钮

## 4. 任务拆分(随实施勾选)

### P0 — 上传基础设施
- [x] **U-1** `api/uploadProgress.ts`:XHR with onProgress + signal cancel
- [x] **U-2** `kbUploadStore.ts`:zustand 持 jobs 字典,actions: enqueue / start / progress / ok / error / clear
- [x] **U-3** `useKbUpload.ts`:接 file[] + kind,做并发调度(默认 3)
- [x] **U-4** `KbProgressBar.tsx`:聚合显示

### P1 — 拖拽
- [x] **U-5** `KbDropZone.tsx`:卡片 wrapper,onDragEnter/Leave/Over/Drop
- [x] **U-6** `KbCardCompact` 接 KbDropZone:hover 边框 + drop 指示
- [x] **U-7** drop 后调 useKbUpload.submit;非支持 kind 弹 toast

### P2 — 创建对话框
- [x] **C-1** `CreateKbDialog.tsx`:kind picker(4 卡)+ 路由到子表单
- [x] **C-2** `DocumentKbForm.tsx`:简表单,默认 vs_type/embed_model
- [x] **C-3** `ImageKbForm.tsx`:name + kb_info + embedder_model 选择
- [x] **C-4** `VectorKbForm.tsx`:外部向量库连接表单 + 测试连通
- [x] **C-5** `useCreateKb.ts`:doc / image / structured / vector 四个 mutation 统一
- [x] **C-6** Header `onCreateDoc/onCreateDb` 改为打开 CreateKbDialog,kind picker 默认值预填

### P3 — 验证 & 提交
- [x] **V-1** typecheck + vitest 全过
- [x] **V-2** commit + push

## 5. 验收
1. 点 Header「+」→ 弹 CreateKbDialog,有四个 kind 卡可选
2. 选「文档」→ 简表单 → 创建 → 卡片立刻出现
3. 拖文件到刚建好的卡片 → 卡片 hover brand 边框 → 释放 → 卡片底部出进度条
4. 多个文件同时拖入 → 队列(3 并发)→ 进度聚合
5. 进度跑完 → 800ms 淡出 → 文件数 +N(走 ku.list invalidate)
6. 拖到 Structured / Vector KB → toast「此知识库不支持文件上传」
7. 选「图像」→ 创建 → 拖入图 → 自动 embed 入索引
8. 选「向量」→ 填外部 Milvus 连接 → 测试 → 落库
