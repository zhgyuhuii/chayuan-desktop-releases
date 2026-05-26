# 任意内容采集与大模型清洗计划

## 背景与目标

模型广场不能只依赖固定 API。不同模型源可能提供 REST API、GraphQL、静态 HTML、服务端渲染页面、Next.js 注水 JSON、站点地图、页面内异步接口，甚至只有普通网页列表。目标是建设一个可扩展的采集框架：

- 多策略采集：API JSON、网页 HTML、站点地图、页面接口侦测、链接扩展。
- 统一归一化：把任意来源整理为模型目录标准字段。
- 大模型清洗：对标题、描述、标签、能力类型、参数规模、算力需求做结构化清洗。
- 高性能并发：多源并行、单源多 URL 并行、限流、超时、去重。
- 可迁移存储：当前写入本地 JSON 索引，后续平滑迁移到 SQL / 搜索引擎。

## 架构设计

### 分层

1. Source Registry
   - 管理来源元信息：id、名称、base_url、采集策略、种子路径、优先级、是否启用。
   - 固定源仍保留专用 API 适配器，保证高质量和高速度。
   - 未提供稳定 API 的源走通用 Web 采集器。

2. Fetch Layer
   - 统一 HTTP 请求、User-Agent、超时、重试、内容类型识别。
   - 支持 JSON / HTML / 文本。
   - 后续可加代理、robots 策略、站点限流和断点续抓。

3. Discovery Layer
   - API JSON：直接解析列表和分页字段。
   - HTML：提取 `<title>`、meta description、链接、JSON-LD、Next.js `__NEXT_DATA__`。
   - 接口侦测：从 HTML / script 中发现包含 `api`、`model`、`models`、`library` 的接口地址，再二次请求。
   - 链接扩展：抓取 `/models/...`、`/library/...` 等详情页入口。

4. Normalize Layer
   - 把候选记录转成统一模型条目：model_id、name、vendor、type、tags、downloads、size、updated_at、source_refs。
   - 通过 canonical_id 去重，相同模型只显示一个，但保留所有下载源。

5. LLM Cleaning Layer
   - 规则先行：先用 pipeline tag、模型名、标签做确定性分类。
   - LLM 补强：调用已配置可用 LLM，把候选模型批量清洗为严格 JSON。
   - 清洗内容：模型类型、中文简介、参数规模、算力等级、无效项过滤建议。
   - 失败兜底：LLM 不可用时保留规则结果，不阻塞同步。

6. Storage & Query
   - 现阶段写入 `CHAYUAN_ROOT/model_registry/models.json`。
   - 查询接口只依赖标准结构，未来换 SQL / FTS 不影响前端。

## 并发与性能

- 多源同步可以并行，单源内部对种子 URL 和侦测 URL 使用线程池。
- 每个来源设置 `limit_per_source`，避免第一次同步过大。
- URL 去重、内容条目去重、source_refs 去重分开处理。
- 默认短超时，失败记录到 `skipped`，不阻塞其他来源。
- 后续可把同步改为后台任务，前端展示进度。

## 安全与治理

- 默认只抓公开页面，不绕过登录和付费墙。
- 不执行页面 JS，只解析静态 HTML 和可见注水数据。
- URL 只允许 `http/https`，后续增加内网地址拦截，避免 SSRF。
- 采集结果必须经过字段白名单归一化，不把原始 HTML 直接入库展示。

## 实施步骤

1. 新增通用采集模块 `model_registry/crawler.py`。
2. 在 `catalog.py` 中接入 `web` 类型来源，专用 API 失败或未提供 API 时走通用采集。
3. 新增 `crawl_url_to_registry()`，支持任意 URL 采集后入本地索引。
4. 新增 `POST /model_registry/crawl`，为后续 UI “添加任意来源”预留 API。
5. 保留已有专用下载逻辑，通用来源先提供目录索引和 source URL，下载适配器后续按来源补齐。

## 当前版本边界

- 本次实现不引入浏览器渲染器，先做静态网页、注水 JSON、接口侦测。
- 不新增第三方依赖，降低部署复杂度。
- LLM 清洗沿用现有模型归类入口，后续再拆成独立批处理任务和审计队列。
