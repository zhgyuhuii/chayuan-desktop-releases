# 模型广场改造设计与实施计划

## 目标

把模型广场从“厂商卡片网格”升级为“厂商入口 + 模型目录”的工作台：

- 顶部展示 8 个高频厂商卡片，包含 logo、名称、图标、厂商介绍、配置入口；其后提供“更多”弹窗展示全部厂商。
- 中部提供一级来源标签：推荐、全部、国内、国外、本地、聚合、自定义。
- 中部第二行提供模型能力标签：全能力、对话、文本嵌入式、图像嵌入式、重排、图生文、文生图、文生视频、文生声音、语音识别、其他。
- 底部改为模型列表，展示模型名称、厂商、大小、类型、更新时间、下载量和操作。
- 列表支持搜索、同步、下载、本地状态、进度和测试。

## 架构边界

### 数据源

当前前端已有两个稳定来源：

- `/admin/model_platforms/catalog`：厂商目录、厂商默认模型、云 API 模型元信息。
- `/image_models`：图像嵌入模型三态、大小、缓存目录、下载/测试/上传操作。

本次前端实现统一为 `MarketplaceModelRow` 视图模型：

- 云 API 模型：来自厂商 catalog，操作为“配置厂商 / 选择模型”，不伪造本地下载能力。
- 图像嵌入模型：来自 `/image_models`，操作为下载、测试、上传离线包、刷新状态。
- 外部模型库同步：前端预留 `modelRegistry.sync()` 服务边界。当前后端无跨库同步端点时，按钮退化为刷新本地 catalog 与 image_models；后续后端可接 Hugging Face、hf-mirror、ModelScope 等索引源。

### 多源模型库汇入

模型目录不应该绑定单一 Hugging Face 生态。建议按“来源适配器”接入，所有来源归一化成统一模型索引：

- Hugging Face 官方：`https://huggingface.co/api/models`，全球模型最全，包含 downloads、tags、pipeline_tag、更新时间。
- HF Mirror：`https://hf-mirror.com/api/models`，国内网络优先，字段尽量兼容 Hugging Face。
- ModelScope 魔搭：国内模型生态，覆盖 Qwen、通义、视觉、语音和行业模型。已验证公开模型列表、模型详情、文件列表 API 可用，并接入同步与下载源。
- Civitai：文生图 / LoRA / Checkpoint 模型生态。已验证公开模型目录 API 和下载跳转地址可用，并接入同步与下载源。
- OpenXLab：上海 AI Lab 生态，覆盖 InternLM、书生、视觉/多模态模型；当前下载通常需要 AK/SK，先保留为凭据型扩展源。
- WiseModel：国内模型托管与企业模型生态；当前下载 SDK 通常需要账号 Token，先保留为凭据型扩展源。
- Ollama Library：本地运行模型目录，可归入“本地 / 对话 / 向量”等能力。
- Modelers 魔乐社区：Git 仓库协议已验证可连通，但尚未发现稳定全量目录 API，先作为后续自定义 Git 源。
- GitHub Releases / 直链仓库：适合企业私有模型或 GGUF 权重，后续作为 custom source。
- 本地图像嵌入模型：来自 `/image_models`，和外部目录统一显示，但下载/测试走本地管理 API。

后端新增 `model_registry` 轻量索引服务：

- `GET /model_registry/sources`：列出可同步来源。
- `POST /model_registry/sync`：从多源同步到 `$CHAYUAN_ROOT/model_registry/models.json`。
- `GET /model_registry/models`：离线查询本地索引，支持搜索、类型、来源过滤。
- `POST /model_registry/download`：按模型的多个 `source_refs` 自动选择下载源；当前源无法连接或下载失败时立即切换下一个源。

第一阶段使用 JSON 索引，优点是无需迁移数据库、数据目录可直接迁移；后续模型量变大时迁移到 SQL/FTS 表，API 契约不变。

同一个模型必须只展示一条。索引主键使用规范化后的 `canonical_id`，例如 Hugging Face 与 HF Mirror 都返回 `Qwen/Qwen2.5-32B-Instruct` 时，只生成一条模型记录：

- 模型自身字段：`model_id`、`type`、`size_bytes`、`parameter_count_b`、`compute_tier`、`downloads`、`updated_at`。
- 来源字段：`source_refs[]`，记录每个来源的 `source`、`base_url`、`url`、`model_id`、下载适配器类型。
- 展示字段：列表显示一条模型，并提示“多个下载源”；下载时按 `source_refs` 优先级自动切换。
- 下载路径：统一落在 `$CHAYUAN_ROOT/models/model_registry/<model_id-safe-name>`，跟随数据目录迁移。
- 下载源优先级：国内可用源优先，如 `hf_mirror`、`modelscope`；失败后自动尝试下一个 `source_refs`。

参数规模和算力档位：

- `parameter_count_b`：从模型名、标签或 LLM 归类结果提取，例如 `32B`。
- `compute_tier`：按参数规模给出边缘设备、单卡、工作站、多卡、集群、未知等档位。
- `size_bytes`：优先从来源 API 的文件列表累计；来源未提供时留空，后续可在下载后回写真实磁盘大小。

### 自动归类策略

归类输出统一为：

- `chat`
- `text-embedding`
- `image-embedding`
- `rerank`
- `image-to-text`
- `text-to-image`
- `text-to-video`
- `text-to-audio`
- `speech-to-text`
- `other`

归类分三层：

- 来源字段直判：优先读取 Hugging Face `pipeline_tag`、tags、library_name 等结构化字段。
- 规则兜底：通过模型名和标签识别 bge/gte/e5/reranker/clip/siglip/flux/whisper/tts 等常见模式。
- LLM 补强：同步时如果已配置可用 LLM，则后端可批量调用 LLM 进行归类和摘要补全，结果写入本地索引；LLM 不可用时不阻塞同步，保留规则分类。

LLM 归类提示词必须要求输出严格 JSON，并限制枚举类型，避免自由发挥。分类结果必须入库并带上 `type_source=llm|rules|source`，便于审计和后续重新归类。

### 模块拆分

- `MarketplacePage.tsx`：组合页面、状态管理、筛选、搜索、分页。
- `VendorHeroStrip.tsx`：顶部 8 个厂商卡片 + 更多弹窗。
- `MarketplaceModelTable.tsx`：模型列表、操作列、下载进度。
- `endpoints.ts`：前端 API 边界，封装 `imageModels` 与可选 `modelRegistry`。

## UE / 视觉策略

- 顶部厂商卡片使用横向滚动与 8 卡上限，避免一屏被厂商淹没。
- 更多厂商采用弹窗网格，保留 logo 和简介，降低寻找成本。
- 筛选标签保持两层：来源维度和能力维度分离，减少“国内嵌入模型”这类组合筛选的认知成本。
- 模型列表使用表格式信息密度，便于比较大小、类型、更新时间、下载量和状态。
- 下载进度采用行内进度条：支持后台任务时显示阶段状态；无百分比时使用不确定进度，避免虚假精度。

## 性能与并发

- 使用 React Query 缓存 catalog/image_models，筛选和搜索走 `useMemo`。
- 厂商弹窗按需渲染，列表行保持轻量，无卡片内复杂嵌套。
- 下载/测试是逐模型 mutation，行级 pending 状态隔离，避免阻塞整个页面。
- 同步按钮只触发服务层边界，当前本地刷新并行执行，后续可接后端异步任务。

## 实施步骤

1. 恢复后端配置面板原有图像向量化模型位置，不再在后端 NiceGUI 模型配置页伪造“模型广场”入口。
2. 在 `chayuan-client` API 层补充图像模型和模型库同步边界。
3. 新增顶部厂商条组件和更多弹窗。
4. 新增统一模型列表组件，支持搜索、筛选、下载、测试和上传。
5. 重组 `MarketplacePage`，用三层布局替代原厂商卡片网格。
6. 运行 typecheck，修复类型和格式问题。
