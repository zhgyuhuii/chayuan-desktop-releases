# 2026-04-30 全量测试执行报告

## 1. 执行结论

本轮已完成环境、后端、前端和核心业务 smoke 测试。当前可确认：

- `py312` 环境可用：Python `3.12.13`。
- dev-stack 基础服务可用：Postgres、Redis、MinIO、Milvus、OnlyOffice 均 healthy。
- 后端 API 可启动，`/healthz`、`/readyz` 正常，DB 与 Redis readiness 通过。
- 后端黑盒 smoke：`30 passed / 0 failed`。
- AI Space 后端单测：`108 passed / 1 skipped`。
- 前端单测：`20 passed / 0 failed`。
- 前端 `@chayuan/app`、`@chayuan/web` typecheck 通过。
- Web 构建通过，SPA 关键路径硬刷新可达。

## 2. 已修复问题

### 2.1 Python 环境

- `py312` 原本没有 `pip`，已通过 `ensurepip` 补齐。
- 后端源码依赖已安装到 `py312`：`pip install -e libs/chayuan-server`。
- 补齐后端测试依赖：`pytest`、`pytest-asyncio`、`syrupy`、`pytest-cov`、`pytest-dotenv`、`pytest-mock`、`pytest-socket`、`responses`、`requests-mock` 等。

### 2.2 数据库初始化与迁移

- 发现 `chayuan init --profile prod` 首次会先建 SQLite 表、再写入 Postgres 配置，导致 Postgres 空库，登录和业务路由 500。
- 已按 dev-stack 写入 `127.0.0.1` Postgres / Redis / Milvus 配置。
- 已补齐内置迁移：
  - Office：`office_group`、`office_share`。
  - AI Space：`ai_app`、`ai_app_version`、`ai_app_key`、`ai_app_run`、`ai_app_task`、`ai_app_test_case`、`ai_app_rate_limit` 等 14 张表。
- 已在 API startup 增加自动迁移与默认 admin 种子，避免空库启动后业务端点 500。

### 2.3 AI Space 运行时

- 修复表达式 mini-lang：支持 DSL 文档声明的 `true / false / null`、`&& / || / !`。
- 修复空节点 DSL 运行期错误分类：空节点执行时返回 validation 错误。
- 修正 `test_rate_limit.py` 中同一输入既断言失败又断言成功的矛盾测试，保留生产语义：`800 < 900` 时 daily token 仍允许。

### 2.4 前端类型与代理

- 移除 `DocxRenderer`、`XlsxRenderer` 中已失效的 `@ts-expect-error`，修复 typecheck。
- 修复预览渲染器 Biome 问题：已消毒 HTML 加安全说明，Excel 表格 key 改为内容派生。
- 修复 Vite proxy 与 SPA 路由冲突：`/tools`、`/admin/*` 等前端路由硬刷新现在返回 `index.html`，API 请求仍按 `Accept: application/json` 代理到后端。

## 3. 业务功能验证

### 3.1 后端 smoke

已通过：

- Health / Ready / Metrics / Docs。
- Auth `/auth/me`。
- KB：列表、新建、列文件、上传 tiny 文件、搜索、删除。
- Knowledge Source：dialects、列表、连接测试、多源检索空源。
- Governance：policy、PII scan、guardrail、usage、lineage。
- Storage：status、list、MinIO connection test。
- Image models：列表、disk usage。
- Chat：`/chat/v2/chat` 非流式最小调用。
- Tools：`/tools`。

### 3.2 Office

已通过：

- `/office/settings`。
- `/office/groups` 列表。
- 新建分组。
- 新建空白 doc。
- `/office/docs/{id}/config` 生成 OnlyOffice 配置。

仍需产品级验证：

- OnlyOffice 浏览器内实际打开、保存回调、协作编辑。
- 文档右键菜单、内联重命名、拖拽移动等前端交互。
- 我的文档“搜索文件名/内容”目前需要进一步确认前后端是否已有完整搜索 API。

### 3.3 AI Space

已通过：

- 模板列表。
- 应用列表。
- 从模板创建应用。
- 运行时单元测试覆盖 DSL、executor、state、eval、rate limit、vault。

仍需产品级验证：

- Studio 画布交互。
- 发布/撤回/市场审核。
- App run SSE 流。
- Human task 认领/提交/恢复运行。
- 公开分享、嵌入 SDK、OpenAPI key 调用。

### 3.4 前端页面可达性

Web preview 上已验证以下路径 `200` 并返回 SPA：

- `/`
- `/home`
- `/office`
- `/office/edit/1`
- `/chat`
- `/kb`
- `/space`
- `/settings`
- `/tools`
- `/mcp`
- `/admin/traces`
- `/preview-window`

API proxy 同时验证：

- `/tools` with `Accept: application/json` → 后端 API `200`。
- `/admin/model_platforms` with `Accept: application/json` → 后端 API `401`，符合未登录预期。

## 4. 未完全验证 / 环境限制

- Ollama 未启动，已改用在线模型继续测试：DeepSeek 作为 LLM，阿里云百炼 `text-embedding-v3` 作为 embedding。
- 已完成真实 LLM / embedding / KB 入库 / KB 检索 / KB 问答端到端验证；大规模准确率评估仍需正式评测集。
- Langfuse 未启动，因此观测 UI、trace、score 只能验证路由结构，不能验证完整链路。
- 前端浏览器自动化未跑 Playwright，当前页面验证为 preview 静态路由 smoke，未模拟真实点击/拖拽/右键。

## 6. 在线模型续测结果

用户提供在线模型密钥后，已将本地运行配置切换为：

- 默认对话模型：DeepSeek `deepseek-chat`。
- 默认 embedding：阿里云百炼 `text-embedding-v3`。
- `/v1/models` 已返回 `deepseek-chat`、`deepseek-reasoner`、`qwen-plus`、`qwen-turbo`、`text-embedding-v3`、`text-embedding-v2`、`gte-rerank`。

已验证：

- DeepSeek 对话：`/chat/v2/chat` 返回正常答案。
- 百炼 embedding：`check_embed_model("text-embedding-v3")` 通过，返回 1024 维向量。
- 知识库入库：创建测试 KB，上传包含唯一关键词的 txt 文件，异步入库任务完成。
- 知识库检索：`/knowledge_base/search_docs` 命中上传文件内容。
- 知识库问答：`/chat/v2/chat` 的 `mode=kb` 返回基于 KB 内容的答案，并带出处。

本轮新增修复：

- 修复 `chayuan worker` 启动失败：`worker_settings()` 内部类引用 `on_job_start / on_job_end` 的作用域错误。
- 验证 `chayuan worker --burst` 能消费入库任务，任务状态从 `queued` 到 `success`。

回归：

- 后端 smoke：`30 passed / 0 failed`。
- 后端触碰文件 `py_compile` 通过。
- 前端 `@chayuan/app`、`@chayuan/web` typecheck 通过。

## 5. 架构 / UE / 设计建议

- 初始化流程应统一：`chayuan init --profile prod` 应先解析最终目标配置，再建表和执行迁移，不应出现“先 SQLite 后 Postgres”的顺序问题。
- 数据库迁移应单一来源：当前同时存在手写 migration 与 alembic 版本文件，容易漏表；建议统一为 Alembic 或让手写迁移自动覆盖所有 ORM。
- Office 与 AI Space 应加入 smoke 脚本常规覆盖，避免缺表类问题等到页面点击才暴露。
- KB 检索准确率需要正式评测集：文件名、标题、章节、正文、表格字段、跨文档对比分别统计 Recall@5、首条命中率、P95 耗时。
- 面向用户的对话输入不应暴露 embedding 模型；embedding 必须始终来自 KB 配置，搜索模式只在知识对话/办公对话上下文展示。
- 前端代理和生产 Nginx 都要使用“API 请求代理、HTML 导航回 SPA”的规则，避免 `/tools`、`/admin/*`、`/office/edit/*` 这类路径冲突。

