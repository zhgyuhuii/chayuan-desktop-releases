# 2026-04-30 全量功能执行测试计划

> 执行环境：`conda py312`、`/work/chayuan-server/docker/dev-stack/docker-compose.yml` 已部署 Redis / Postgres / MinIO / Milvus / OnlyOffice 等支撑服务。  
> 项目范围：`/work/chayuan-client` 前端、`/work/chayuan-server` 服务端。  
> 测试策略：先验证环境和基础构建，再跑后端/前端自动化，最后按业务功能做 smoke / API / UI 级验证；发现阻断问题优先修复，小问题记录缺口。

## 1. 测试目标

- 验证项目在 `py312` 和 dev-stack 支撑服务下能正常启动、连接、运行基础 API。
- 验证前端静态质量：类型检查、Biome、单测、构建。
- 验证后端静态质量：导入检查、单测、smoke test、关键 API。
- 验证核心功能链路：登录、模型配置、普通对话、知识库检索、察元办公、AI Space、文档编辑入口。
- 从架构、性能、并发、UE、视觉设计和用户可达性角度形成问题清单和重构建议。

## 2. 执行顺序

### 阶段 A：环境与服务

| 编号 | 测试项 | 命令 / 方法 | 验收 |
| --- | --- | --- | --- |
| A1 | Conda 环境 | `conda run -n py312 python --version` | Python 3.12.x |
| A2 | 后端依赖导入 | `conda run -n py312 python scripts/verify_chayuan_imports.py` | 核心模块可导入 |
| A3 | Docker 服务 | `docker compose -f docker/dev-stack/docker-compose.yml ps` | Postgres / Redis / MinIO / Milvus / OnlyOffice healthy |
| A4 | 端口连通 | Redis ping、Postgres select 1、Milvus health、MinIO health、OnlyOffice health | 全部成功或记录失败 |

### 阶段 B：后端自动化

| 编号 | 测试项 | 命令 / 方法 | 验收 |
| --- | --- | --- | --- |
| B1 | Python 编译 | `python -m compileall` 关键包 | 无语法错误 |
| B2 | 单元测试 | `pytest tests/server/ai_space ...` 优先轻量模块 | 通过或定位失败 |
| B3 | smoke test | `scripts/smoke_test.py` | 健康检查、模型、KB、Office 基础 API 可用 |
| B4 | 关键 API | `/healthz`、`/readyz`、`/v1/models`、`/knowledge_universe/list`、`/api/ai/space/apps`、`/office/docs` | 返回 JSON，鉴权行为符合预期 |

### 阶段 C：前端自动化

| 编号 | 测试项 | 命令 / 方法 | 验收 |
| --- | --- | --- | --- |
| C1 | 依赖一致性 | `pnpm install --frozen-lockfile` 或检查 lock | lock 与 package 一致 |
| C2 | Biome | `pnpm lint` / 先跑变更范围 | 无阻断 lint |
| C3 | Typecheck | `pnpm typecheck` | 无 TS 错误；已有历史错误需单列 |
| C4 | 单测 | `pnpm test` | 通过或记录失败 |
| C5 | Web 构建 | `pnpm build:web` | 产物生成 |

### 阶段 D：业务功能 smoke

| 模块 | 测试点 | 验收 |
| --- | --- | --- |
| 登录 | 登录弹窗、游客/鉴权、401 刷新 | 能进入主界面或错误提示明确 |
| Shell / Tab | 左侧菜单全高、Tab 点击切换、详情页 Tab、拖出窗口入口 | 页面可达、状态同步 |
| 模型配置 | 模型设置按类型分组；聊天模型只显示 LLM；默认配置不选非对话模型 | 非 LLM 不进入聊天下拉 |
| 普通对话 | 发送消息、停止、附件入口、知识库图标弹窗 | 输入链路可用 |
| 知识中心 | KB 列表、新建、上传、入库进度、搜索、引用、预览入口 | 能搜索上传内容，embedding 一致 |
| 察元办公 | 我的文档搜索、右键、内联重命名、分组、拖拽、编辑页 | 无不可达功能入口 |
| 编辑器助手 | 左助手清单、底部输入、右消息栏折叠、全屏/返回 | 页面骨架完整 |
| AI Space | 列表、新建、Studio、运行、市场、分享 | 路由/API 不 404 |

## 3. 知识库检索专项

构造最小评测集：

- 文档 A：文件名包含唯一关键词，正文包含政策条款。
- 文档 B：有标题、一级/二级章节、段落编号。
- 表格 C：包含 sheet 名、列名、特定单元格值。

问题类型：

- 文件名检索：“查找 XX 文件”
- 标题/章节检索：“第二章中关于 XX 的要求”
- 正文事实检索：“XX 条款如何规定”
- 跨文档对比：“A 与 B 对 XX 的差异”
- 表格字段检索：“XX 客户的金额是多少”

记录指标：

- Recall@5 / Recall@10
- 首条命中是否正确
- 引用文件名、页码、章节是否完整
- 是否使用 KB 自身 `embed_model`
- P95 检索耗时

## 4. 并发与性能验证

- API 并发：用轻量脚本并发请求 `/healthz`、`/v1/models`、`/knowledge_universe/list`。
- 检索并发：并发 10 / 50 个 KB search 请求，观察错误率、P95、Milvus / Redis 状态。
- 入库并发：多文件上传时应进入队列或限流，不应阻塞主服务。
- 前端交互：大列表滚动、Tab 切换、Office 文档搜索不应明显卡顿。

## 5. 架构与 UE 审查维度

架构：

- 模块边界是否清晰：Shell / Composer / KB / Office / AI Space / Marketplace。
- API 是否有统一错误格式、trace id、鉴权和权限校验。
- 检索链路是否可扩展：向量库、BM25、rerank、预览定位解耦。
- 高并发是否有队列、缓存、限流和连接池。

UE / 设计：

- 关键动作是否一键可达，是否存在“点了没反应”的入口。
- 普通用户是否需要理解 embedding / rerank 等专业概念。
- 失败提示是否可行动，而不是空白或技术栈错误。
- 页面布局是否符合主次关系，Office 编辑区是否能最大化。

## 6. 问题处理规则

- 阻断启动、编译、主链路的问题：立即修复。
- 大功能未实现：记录到缺口清单，不用临时假实现掩盖。
- 数据/服务依赖缺失：记录环境要求和复现命令。
- 与当前测试无关的历史脏改：不回滚，只在报告中说明。

