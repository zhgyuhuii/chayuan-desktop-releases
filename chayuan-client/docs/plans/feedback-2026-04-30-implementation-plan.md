# 2026-04-30 反馈修复与新功能实施计划

> 范围：`chayuan-client` 前端、`chayuan-server` 服务端，参考 `/work/chayuan` 的办公助手能力。  
> 目标：先修复 P0 可用性与检索命中问题，再分阶段建设知识检索、察元办公、编辑器助手、知识库与办公联动。

## 1. P0 已修复 / 本轮先行项

| 反馈 | 本轮处理 | 验收方式 |
| --- | --- | --- |
| 1 | Shell 已保持左侧菜单全高；TabBar 已提升到右侧主区最上方，紧贴左侧菜单面板右边界开始 | 打开任意页面，Sidebar 上下占满，Tab 位于主区顶部且不覆盖 Sidebar |
| 2 | Tab 点击现在直接 `activate + navigate`，避免只改 store 不切路由 | 连续打开 KB / 办公 / 聊天 Tab，点击可切换内容 |
| 3 | `/knowledge_base/search_docs` 路由补齐 `use_hybrid/use_rerank` 透传，便于对上传内容走混合检索 | 搜索上传文件内容时可显式开启 hybrid / rerank |
| 4,5 | 前端跨 KB ask 不再发送 `embed_model` 覆盖；后端文档 KB 与办公 KB 都忽略旧客户端传入的 `embed_model` | 同一 KB 查询始终使用建库时 `embed_model` |
| 6 | 通用输入框默认不显示“精准 / 全面 / 速度”；仅 KB / 办公 KB 对话显式开启 | 首页 / 普通聊天无检索档位，KB / 办公对话可见 |
| 9 | 新增 `/office/search`，我的文档搜索框接入标题/描述与已索引内容融合搜索 | 输入关键词可命中文档名/描述；已向量化文档可按内容命中 |

## 2. 知识中心检索重构（反馈 3,4,5,6,7,16）

### 技术路线

- 上传入库：解析文件名、标题、章节路径、页码、段落序号、字符偏移、表格 sheet/cell 范围，随 chunk metadata 入库。
- 召回链路：`query planner -> query rewrite -> vector -> BM25 -> title -> section -> RRF 融合 -> rerank -> context expansion -> answer`。
- 嵌入模型：KB 创建时定型；搜索强制读取 KB 元数据，不允许前端覆盖。变更嵌入模型只能走“重建索引”。
- 命中展示：SSE 推送检索阶段进度；结果默认折叠为总结；展开后显示命中文档、页码、章节、得分和预览入口。
- 高亮预览：文档预览按 `file_name + page + char_offset` 跳转，黄色高亮命中段。

### 分阶段

- P0：修复嵌入模型错配、0 命中诊断、上传后入库状态可见。
- P1：完成标题/章节/BM25 多路召回与 RRF 融合。
- P2：接入 rerank、context expansion、预览高亮。
- P3：做评测集，按文档类型统计 Recall@K、MRR、Answer Faithfulness，目标关键业务问题命中率 > 95%。

## 3. 察元办公“我的文档”体验（反馈 8,9,10）

### 功能计划

- 我的文档顶部增加搜索框：先按标题/描述/分组名本地过滤；已接入后端 `/office/search`，融合文件名命中与内容向量命中。
- 文档右键菜单：删除、移动、分享、下载、触发向量化、重命名。
- 分组右键菜单：新建子分组、重命名、移到根目录、删除。
- Inline 重命名：点击文件名/分组名进入输入态，Enter 保存，Esc 取消，失焦保存或按配置取消。
- 拖拽移动：文档拖到分组、分组拖到分组；后端需检测循环移动。

### 数据与接口

- 复用现有 `officeApi.patch`、`officeGroupsApi.patch`、`officeGroupsApi.moveDocs`。
- 新增 `officeGroupsApi.moveGroup`，支持 `{ parent_id | to_root }`，服务端校验不能移动到自身后代。

## 4. 文档 / 表格 / 演示稿独立编辑（反馈 11,13）

- 新建与打开文档都进入 `/office/edit/:docId` 独立 Tab。
- Tab 支持拖出独立窗口；Web 退化为 `window.open`，Tauri 走平台窗口 API。
- 编辑页支持“返回库”“全屏”“折叠助手/消息栏”。
- 编辑关闭或保存后触发向量化，列表显示 `queued/running/ok/error` 状态。

## 5. 编辑器 AI 助手页（反馈 12）

### 页面骨架

- 左侧：助手清单，包含内置助手、自定义助手、分类筛选、创建助手。
- 中间：OnlyOffice 编辑器主画布。
- 底部：对话输入框，支持输入源 `全文 / 选中 / 当前段落 / 光标上下文`。
- 右侧：消息列表，可折叠，显示对话、操作 trace、报告卡片、Apply/Reject。

### 7 大关键链路

| 链路 | 输入 | 输出 | 文档动作 |
| --- | --- | --- | --- |
| 纯对话 | none/fulltext | markdown | 不改文档 |
| 选中改写 | selection | text diff | replace |
| 插入生成 | cursor/paragraph | text | insert-at-cursor / insert-before / insert-after |
| 结构化批注 | fulltext | JSON issues | comment + highlight |
| 报告输出 | fulltext | report card | sidebar-report / append |
| 批量处理 | fulltext | full diff | replace-doc / bulk-format |
| 大纲成文 | outline | structured doc | replace-doc / new-doc |

### 自定义助手

- 用户用自然语言描述助手目标。
- LLM 生成 `systemPrompt + pipeline + inputSource + allowedActions + paramSchema`。
- 前端预览配置，用户确认后保存。
- 执行时所有 Action 先经 JSON Schema 校验，再落文档操作，保证可撤销。

## 6. 知识中心与办公联动（反馈 14,15）

- 知识中心新增“新建文档 / 表格 / 演示稿”，创建后调用办公编辑器。
- 保存后自动上传到当前知识库：文档文件进入 KB content，触发向量化任务。
- 知识库文件详情增加“编辑”按钮：如果格式支持 OnlyOffice，则创建/绑定 OfficeDoc 后打开编辑器。
- 编辑完成后回写 KB 原文件并重建对应文件索引。

## 7. 架构与性能要求（反馈 16）

- 前端：Shell、KB、Office、EditorAssistant 分模块；跨窗口能力走 `platform-shared`。
- 后端：router 只做鉴权和参数，检索/办公/助手放 service，持久化放 repository。
- 并发：检索多路并行，入库与重建走任务队列；SSE 反馈进度。
- 可观测：每次搜索带 trace id，记录 query rewrite、各路召回数量、rerank 耗时、最终引用。
- 可移植：本地 FAISS/Chroma 可单机跑，生产 Milvus/PGVector/ES 可横向扩展。

## 8. 建议实施顺序

1. P0 修复：Tab、嵌入模型一致性、检索控件显示范围。
2. 知识检索：上传元数据增强、多路召回、进度与高亮预览。
3. 办公体验：搜索、inline 重命名、右键与拖拽。
4. 编辑器骨架：独立编辑页、全屏、助手/消息栏布局。
5. 助手运行时：7 大链路、自定义助手、报告输出。
6. KB 与办公联动：KB 内新建/编辑，保存后自动入库。

## 9. 逐项实施清单

| 序号 | 反馈 | 状态 | 实现模块 | 验收标准 |
| --- | --- | --- | --- | --- |
| 1 | 左侧菜单全高，顶部标签从菜单右侧开始 | 已完成 | Shell / Chrome | Sidebar 上下占满，TabBar 不覆盖左栏 |
| 2 | 点击顶部标签切换界面 | 已完成 | Shell / TabBar | 点击任意 Tab 后 URL 与内容同步切换 |
| 3 | 知识中心命中上传文件内容 | 进行中 | Server KB / RAG | 上传完成后可按原文关键词命中文档 chunk |
| 4 | 搜索使用 KB 配置的嵌入模型 | 已完成 P0 | API / Server KU | 前端不传覆盖模型，后端忽略旧覆盖字段 |
| 5 | 对话输入框不选择嵌入模型 | 已完成 P0 | Composer | 普通对话无嵌入选择，KB 内仅只读展示 |
| 6 | 精准/全面/速度仅知识和办公对话显示 | 已完成 P0 | Composer | 首页/普通聊天隐藏，KB/Office KB 显示 |
| 7 | 行业知识库最佳搜索路线 | 已规划，待实现 | KB pipeline | 文件名/标题/章节/BM25/向量/Rerank 多路召回 |
| 8 | 我的文档右键删除/修改/分组/重命名，点击名称内联编辑 | 已完成 UI 首版 | Office UI | 文档/分组名称点击即可输入，右键保留高级操作 |
| 9 | 我的文档上方搜索框 | 已完成 P1 首版 | Office UI / Server search | 文件名/描述/分组名即时过滤，已索引文档可按内容检索 |
| 10 | 右侧栏右键菜单、新建分组、重命名、拖拽移动 | 已完成 P1 首版 | Office RightRail | 分组树支持右键新建/重命名/删除、文档拖入分组、分组拖到分组/根目录 |
| 11 | 新建文档/表格/演示稿独立界面，可返回，可拖出 | 部分完成 | Office Editor / Tab | `/office/edit/:id` 独立 Tab，拖出窗口继续完善 |
| 12 | 编辑页助手清单、消息栏、自定义助手、7 大链路 | 已完成页面骨架 | Office Assistant | 已有左助手清单、下输入、右消息栏；动作运行时待接 |
| 13 | 内容编辑页全屏单独打开并返回 | 部分完成 | Shell / Office Editor | Tab 独立打开已具备，全屏按钮接入编辑页 |
| 14 | 知识中心新建文档/表格/演示稿，保存自动上传 KB | 待实现 | KB + Office | KB 内创建后打开编辑器，关闭后入库并向量化 |
| 15 | 知识中心文件调用办公编辑器编辑 | 已完成入口首版 | KB detail + Office | KB 文件详情可创建办公编辑副本；保存回写原 KB 待接 |
| 16 | 架构/UE/高性能/准确率>95%/进度/折叠总结/预览高亮 | 进行中 | 全链路 | 有 trace、进度、引用、预览定位、评测集 |
| 17 | 每块新功能列计划生成 MD | 已完成并持续更新 | Docs | 本文档及 A-E 分块计划可审查 |

## 10. 待确认

- KB 检索生产向量库优先 Milvus 还是 PGVector？
- rerank 模型是否统一使用私有化 `bge-reranker`？
- OnlyOffice 是否作为所有文档格式的唯一编辑器？
- 自定义助手是否需要管理员审核后才能共享到团队？
- 文档编辑后的 KB 重建是自动立即执行，还是允许用户选择“保存但稍后入库”？
