# AI SPACE 编排平台开发计划

> 目标：把当前 `features/space/SpacePage.tsx` 的"App 商店外壳"升级为对标 **Coze（扣子）** + **Dify** 的 **AI 应用空间**：用户可在平台内创建 / 编排 / 发布 / 调用 AI 应用，应用可挂载知识库、工具、MCP、办公文档，并以低代码画布或表单化向导完成生产级编排。
>
> 范围基线：`/work/chayuan-client`（React + pnpm workspace）+ `/work/chayuan-server`（FastAPI）。新功能默认增量进 `chayuan-client/packages/{api,app}` 与 `chayuan-server/.../server/`。

---

## 1. 战略判断（一句话）

**不重写 Coze / Dify**，而是把"察元"既有的 KB / Tool / MCP / Office / Model-Platform 资产**重新编排**为应用产物，做一个**轻量、深度复用现有 LangGraph 的应用平台**——核心新增物只有三件：**应用（App）/ 编排画布（Flow）/ 运行时（Runtime）**。其余绝大部分模块都已落地，需要做的是**按 App 维度聚合、加发布与隔离层**。

### 1.1 私有化部署前提（与 Coze/Dify SaaS 形态的关键差异）

本项目落点是**企业内网 / 客户机房 / 桌面 Tauri**多形态私有化部署。规划必须显式剔除 SaaS 心智、保留私有化必备能力：

| 维度 | 不做（SaaS 心智） | 必做（私有化心智） |
| --- | --- | --- |
| 经济 | **不做计费、不做 token 账单、不出账户报表** | tokens / latency 仅作**容量规划**信号 |
| 限流 | 不按用户分钱限速 | 默认放开；管理员可启用 QPS / 并发上限保护后端 |
| 多租户 | 不做"购买席位 / 订阅" | 沿用现有 user / group / grants，组织内 RBAC |
| 模型 | 不绑定外部商业 API 计费视图 | 走 `model_platform`（已支持 yaml→DB 三层叠加）；离线 / 私有模型为一等公民 |
| 渠道 | 不做公网 SaaS 嵌入分发 | 优先**飞书 / 钉钉 / 企业微信**等内部 IM；公网嵌入仅作可选 |
| 资源 | 不依赖云对象存储计价 | 复用现有 `FileStorage`（MinIO / FastDFS / 本地目录） |
| 网络 | 不假设公网出网 | `http` 节点出网 allowlist 必须；离线安装包能用 |
| 日志 | 不上报到第三方分析平台 | 沿用 `observability/`，trace 落本机 / 私有 Jaeger |

**结论**：所有"按 token 计费 / 按月成本 / 跨租户结算"相关条目**整体不做**；省下的复杂度优先转给**离线模型支持、IM 渠道、内网安全**。

---

## 2. 竞品对照与差距盘点

### 2.1 Coze（字节跳动 / 扣子）核心能力

| 维度 | Coze 做法 | 我们现状 | 差距 |
| --- | --- | --- | --- |
| **Bot / 智能体** | 一站式智能体（Persona + Skill + Memory + Workflow） | `agent_type` 仅在 chat 路由内做枚举调度 | 缺统一 Agent 配置实体 |
| **Workflow（节点画布）** | 拖拽节点：LLM、知识库、代码、HTTP、条件、循环、子流程 | 无可视化编排；`graph/nodes.py` 只支持固定流 | **核心空白** |
| **Plugin（工具）** | 官方 + 自定义 + OpenAPI 导入 | `/tools`、`/mcp_connections` 已具备，但只能挂在对话级 | 缺"绑定到 App"的归属层 |
| **知识库** | 文档 / 表格 / 图片 / API 多模态 | KU 已支持 4 种 kind | 基本对齐 |
| **Memory / Variables** | 持久变量、用户档案 | 仅 `conversation_id` 隐式记忆 | 需要 KV 变量 + 长期记忆 |
| **多渠道发布** | 飞书 / 微信 / API / Web SDK | 仅自家 chat 端 | 至少需要 OpenAPI 接入点 |
| **Multi-Agent** | 主 Agent 调度子 Agent | 单 agent 模式 | 暂可简化 |

### 2.2 Dify 核心能力

| 维度 | Dify 做法 | 我们现状 | 差距 |
| --- | --- | --- | --- |
| **App 类型** | Chatbot / Agent / Workflow / Text-Generation | 全是 Chatbot 形态 | 需 App 类型枚举 |
| **Studio（应用工作台）** | Prompt / Variables / Context / Tools / Datasets 全在一个表单 | 设置散落在多处 | 需聚合到 App Detail 页 |
| **Workflow DSL** | 自研 DSL（YAML 可导出导入） | 无 DSL | 自研轻 DSL（基于 LangGraph） |
| **数据集（Datasets）** | 与 App 解耦，可被多个 App 引用 | KU 已是中心化资产 | **架构相通** |
| **Endpoint API** | 每个 App 有独立 `app_id + token` API | `/chat/v2/chat` 单端点 | 需要多租户 token |
| **Logs / Annotations** | 调用日志、人工标注、回归测试集 | 仅 conversation 级日志 | 二期补 |

### 2.3 结论

- **画布编排（Workflow）** + **App 实体** + **多租户发布** 是三大空白；
- 知识库 / 工具 / MCP / Office / Model 已是 Coze / Dify 同档资产，**不重做**，做"被 App 引用"的归属层即可；
- 核心引擎可以**直接复用现有 LangGraph**（`chayuan/server/graph/`、`agents_registry/`），自己只新增节点类型与编译器。

---

## 3. 总体架构（架构师视角）

```
┌──────────────────────────────────────────────────────────────────┐
│                          Frontend  (React)                       │
│  features/space/                                                  │
│   ├─ AppGalleryPage      ← 现有 SpacePage 升级（应用商店 + 我的应用）│
│   ├─ AppStudioPage       ← Coze/Dify 风格的应用工作台              │
│   │    ├─ BasicForm       (名称/头像/描述/类型)                     │
│   │    ├─ PromptForm      (system/persona/variables)              │
│   │    ├─ ContextForm     (KB 多选 / 召回参数)                      │
│   │    ├─ ToolsForm       (Tool / MCP 选择)                        │
│   │    ├─ FlowCanvas      ← 重头戏：节点画布 (React Flow)           │
│   │    └─ DebugPanel      (Run / Trace / Variables)               │
│   ├─ AppRuntimePage      ← 端用户使用 App 的对话/表单页            │
│   └─ MarketPage          ← 公共应用市场（已有商店复用）             │
│                                                                   │
│  packages/api/src/aiSpace.ts          (CRUD + run + stream)       │
│  packages/api/src/flow.ts             (Flow DSL 类型 + validate)  │
│  packages/ui/src/flow-canvas/         (节点组件复用)               │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────┴─────────────────────────────────────┐
│                       Backend (FastAPI)                          │
│  api_server/                                                      │
│   ├─ ai_space_routes.py         (App CRUD / publish / token)      │
│   ├─ flow_routes.py             (Flow DSL save / validate / run)  │
│   └─ ai_space_runtime_routes.py (POST /apps/{id}/run  SSE)        │
│                                                                   │
│  ai_space/                       ← 新模块                          │
│   ├─ models.py                   App / Flow / AppVersion / AppKey │
│   ├─ repository.py               存储层（PG/SQLite，现有 db 体系）  │
│   ├─ runtime/                                                      │
│   │   ├─ compiler.py             Flow DSL → LangGraph CompiledGraph│
│   │   ├─ nodes/                  LLM / KB / Tool / Code / IF / Loop│
│   │   ├─ executor.py             并发 + 幂等 + 中断恢复            │
│   │   ├─ trace.py                节点级 trace（接 observability）   │
│   │   └─ sandbox.py              Code / HTTP 节点沙箱              │
│   ├─ rbac.py                     App owner / grant / public        │
│   └─ publish/                                                      │
│       ├─ openapi.py              生成应用专属 OpenAPI               │
│       └─ apikey.py               app_key / app_secret 校验          │
└──────────────────────────────────────────────────────────────────┘
                                  │
   ┌──────────────────────────────┼──────────────────────────────┐
   │                              │                              │
 KB（KU）                      Tools / MCP                   Office
（已落地）                    （已落地）                  （已落地）
```

设计要点：

1. **App 是聚合根**。一个 `App` 由 `Flow + ResourceGrants(KB/Tool/MCP/Office/Model) + Persona + Variables + ApiKey` 构成。所有现有模块作为"被引用的资源"，**不被搬动**。
2. **Flow 引擎复用 LangGraph**：DSL → `compiler.py` 翻译为 `StateGraph` + 节点函数；现有 `graph/nodes.py` 的 KB / Tool 节点直接被新节点封装包一层，**零重写**。
3. **运行时 = 现有 chat handler 的"广义版"**：`/chat/v2/chat` 是 App 类型为 `agent` 时的一个特例；新 `/apps/{id}/run` 内部最终也是一份 `CompiledGraph.astream()`。
4. **Frontend 复用 `ConversationView`**：当 App 类型 = chatbot/agent 时，运行页就是带 Flow 上下文的对话页；workflow 类型才走表单 / 流程展示。
5. **多租户 / 隔离**：每个 App 一对 `app_key + app_secret`，进入 `Authorization: Bearer ak_xxx` → 中间件解出 `app_id`，限定只能访问该 App 授权的资源（KB / Tool / MCP）。

---

## 4. 数据模型（落 PG，沿用 `db/models/` 规范）

```python
# db/models/ai_app_model.py
class AIApp:
    id: UUID
    owner_id: int                  # 与 auth user FK
    name: str
    icon: str | None
    description: str
    type: Literal["chatbot", "agent", "workflow", "text_generation"]
    visibility: Literal["private", "team", "public"]
    current_version_id: UUID | None
    created_at, updated_at: datetime

class AIAppVersion:
    id: UUID
    app_id: UUID                   # FK
    version: str                   # semver-ish, e.g. v1.3.0
    persona: dict                  # {system_prompt, model, params}
    variables: list[dict]          # [{key, type, default, required}]
    flow_dsl: dict                 # JSON DSL（见 §5）
    resource_grants: dict          # {kb_ids, tool_ids, mcp_ids, office_ids}
    status: Literal["draft", "published"]
    published_at: datetime | None

class AIAppKey:
    id: UUID
    app_id: UUID
    name: str                      # e.g. "production", "feishu-bot"
    ak: str                        # ak_xxx
    sk_hash: str                   # bcrypt
    scopes: list[str]              # ["chat:invoke", "flow:run"]
    rate_limit: dict               # {qps, daily_quota}
    revoked_at: datetime | None

class AIAppRunLog:                 # 二期；先按 conversation_id 走老路
    id, app_id, version_id, user_id, latency_ms, tokens, status
    trace_blob_uri: str | None     # 私有化部署不计费，保留 tokens 与 latency 仅用于容量规划
```

迁移：新增 `0005_ai_app.py`（沿用 alembic/`db/migrations/`）。

---

## 5. Flow DSL（自研、最小化、可前端编辑器化）

> 设计原则：**贴 LangGraph 抽象**（StateGraph + Node + Edge），**避开 Dify 那种过度自由的运行时反射**，节点白名单 + 参数 schema 化。

```jsonc
{
  "version": "1",
  "entry": "n_input",
  "state": {                        // 全局变量初值（与 AIAppVersion.variables 合并）
    "user_input": "",
    "history": []
  },
  "nodes": [
    { "id": "n_input",  "type": "input",      "next": "n_kb" },
    { "id": "n_kb",     "type": "kb_retrieve",
      "params": { "kb_ids": ["doc:law_kb"], "top_k": 6, "score_threshold": 0.3 },
      "outputs": { "context": "$.kb_context" }, "next": "n_llm" },
    { "id": "n_llm",    "type": "llm",
      "params": { "model": "qwen2.5-72b", "system": "{{persona.system}}",
                  "prompt": "Use context: {{state.kb_context}}\nQ: {{state.user_input}}" },
      "outputs": { "answer": "$.answer" }, "next": "n_branch" },
    { "id": "n_branch", "type": "if",
      "params": { "condition": "{{state.answer.length > 0}}" },
      "branches": { "true": "n_output", "false": "n_fallback" } },
    { "id": "n_output", "type": "output", "params": { "value": "{{state.answer}}" } },
    { "id": "n_fallback","type": "tool_call",
      "params": { "tool_id": "search_engine", "args": { "q": "{{state.user_input}}" } },
      "outputs": { "answer": "$.answer" }, "next": "n_output" }
  ]
}
```

**节点白名单（v1）**：

| Node | 用途 | 复用现有 |
| --- | --- | --- |
| `input` / `output` | I/O 信号 | -- |
| `llm` | OpenAI-兼容调用 | `chat/handlers/agent.py` 抽出 client |
| `kb_retrieve` | 知识库召回 | `knowledge_universe/ask` 后端逻辑 |
| `tool_call` | 调用 Tool / MCP | `/tools/call` + `mcp_connections` |
| `http` | 外部 API | 新增（沙箱 + allowlist） |
| `code` | Python 代码片段 | 新增（沙箱 + 资源限额） |
| `if` / `switch` | 条件分支 | 新增 |
| `loop` | for-each / while | 新增（带步数上限） |
| `subflow` | 调用其他 App | `aiSpace.invoke(app_id, ...)` |
| `office_op` | 渲染/写入 Office | `office_routes` 现有 |

**编译器**（`runtime/compiler.py`）：DSL → `StateGraph[AppState]`；每种节点类型一份 `compile_<type>()` 工厂；`StateGraph.compile()` 缓存到内存 LRU（key = `(app_id, version_id)`）。版本发布时 invalidate。

**校验**：保存草稿前在前端跑 `validateFlow(dsl)`（zod schema），后端再用 `pydantic` 校一遍；环检测、未连接节点、变量未声明都给出可定位错误。

---

## 6. 运行时 / 性能 / 并发

1. **执行模型**：`async def run(app, version, inputs) -> AsyncIterator[Event]`，事件类型 `node_start/node_end/token/tool_call/log/error`。前端走 SSE，与现有 `/chat/v2/chat` 同形。
2. **并发**：
   - 单次 run 内部，`asyncio.gather` 并发无依赖节点（编译期由 DAG 拓扑决定，不在 DSL 暴露并发原语）；
   - 节点级超时（默认 30s/LLM，60s/HTTP，10s/code），`asyncio.wait_for`；
   - 全局通过现有 `resilience.py` 的限流器跑（按 `app_id` + `user_id` 双维度令牌桶）。
3. **幂等 / 中断恢复**：长 Flow 入 `ingest_queue`（已在仓库中）异步执行，`run_id` → 状态机 `queued/running/succeeded/failed/cancelled`；重连 SSE 用现有 `JobManager` 的 backlog 模式（参考 KB 远端同步）。
4. **沙箱**：
   - `code` 节点 → `restrictedpython` + 子进程 `subprocess` + `seccomp`（仅 Linux）+ 内存上限 `resource.setrlimit`；
   - `http` 节点 → 出网域名 allowlist（管理员配置），避免 SSRF。
5. **缓存**：
   - LLM 节点支持 `cache: { key, ttl }`（按 prompt 哈希），落 Redis（项目现有）；
   - KB 节点本身已 60s TTL；编译产物 LRU 32 个。
6. **可观测性**：复用 `observability/`（OpenTelemetry）。每个节点一个 span，`trace_id = run_id`，可挂到 Grafana / Jaeger。

---

## 7. 安全 / 权限 / 多租户

- **App 拥有者** = creator；可 grant 到指定 user / group（沿用 KB 的 grants 模型）。
- **App 资源授权**：保存 Flow 时校验所引用的 `kb_ids / tool_ids / mcp_ids` 是否对 owner **可见**，否则拒绝；运行时再校验一次（防发布后授权被收回）。
- **App API Key**：参考 OpenAI key 风格 `ak_<25位 base32>` + `sk_<bcrypt>`；前端只在创建瞬间显示一次原文。
- **限流**（容量保护，不做计费）：`qps_per_key`、`daily_tokens_per_key`、`max_concurrent_runs_per_user`；接 `governance/` 已有的限流体系。私有化部署默认放开，仅在管理员配置时启用。
- **沙箱**与 **secret**：Flow DSL 中不允许明文 secret，必须引 `vault://name/key` 形式占位符，后端在执行期注入。

---

## 8. 复用清单（"不要重写什么"）

| 已有资产 | 在 AI Space 中的角色 | 改动 |
| --- | --- | --- |
| `knowledge_universe` 路由与服务 | `kb_retrieve` 节点的下游 | 0 改 |
| `/tools` / `mcp_connections` | `tool_call` 节点的执行体 | 加 `app_id` scope 校验中间件 |
| `office_routes` | `office_op` 节点 | 抽 `office.write_block(file_id, ...)` 单方法 |
| `model_platform` | LLM 节点的模型选择源 | 0 改 |
| `agents_registry` | `agent` 类 App 的执行回退 | 不动；新 runtime 与之并存 |
| `graph/nodes.py` | 给新 `compiler.py` 抽公共方法 | 抽不抽都行 |
| `auth` + JWT | 控制台权限 | 0 改 |
| `resilience.py` / `governance/` | 限流 / 配额 | 加按 `app_id` 维度 |
| `observability/` | trace | 加 `run_id` 上下文 |
| 前端 `ConversationView` | chatbot 类 App 运行页 | props 加 `appContext` |
| 前端 `CapabilityCards` / `ModelLogo` | Studio 配置面板 | 直接复用 |

---

## 9. 前端模块拆分（包级 / 组件级）

```
packages/api/src/
  aiSpace.ts                  # CRUD: app, version, key
  flow.ts                     # Flow DSL types + zod schema + validate
  aiSpaceRun.ts               # invoke + SSE 解析（复用 chatStream 抽象）

packages/ui/src/
  flow-canvas/                # 通用画布（复用至 sub-flow / debug 视图）
    FlowCanvas.tsx            # React Flow 包装
    nodes/{LlmNode,KbNode,...}.tsx
    palettes/NodePalette.tsx

packages/app/src/features/space/
  AppGalleryPage.tsx          # 取代现有 SpacePage（"我的"+"市场"双 tab）
  AppStudioPage.tsx
    BasicSection.tsx
    PromptSection.tsx
    ContextSection.tsx
    ToolsSection.tsx
    FlowSection.tsx           # 包 flow-canvas
    DebugPanel.tsx            # Run + Variables + Trace
    PublishDialog.tsx         # 版本号 + apikey 生成
  AppRuntimePage.tsx
    ChatRuntime.tsx           # 复用 ConversationView
    WorkflowRuntime.tsx       # 表单 → SSE → 结果
  hooks/useFlowEditor.ts      # 画布内 state + undo/redo
```

**画布选型**：`reactflow`（MIT，已被 Dify / coze-loop 等使用），自定义节点组件按 `packages/ui` 设计 token；avoid 自研 SVG，工作量减半。

---

## 10. API 契约（增量）

```http
# CRUD（scope: mine | shared | public | all；详见 §21.5）
GET    /api/ai/space/apps?scope=mine&keyword=&type=&lifecycle=draft|private|team|public
POST   /api/ai/space/apps
GET    /api/ai/space/apps/{id}
PATCH  /api/ai/space/apps/{id}
DELETE /api/ai/space/apps/{id}                   # 仅 draft / private 可直接删

# 版本 / 草稿 / 发布
POST   /api/ai/space/apps/{id}/versions          # 保存草稿（含 If-Match etag）
POST   /api/ai/space/apps/{id}/versions/{vid}/publish
GET    /api/ai/space/apps/{id}/versions

# 生命周期（visibility 迁移）—— 详见 §21
POST   /api/ai/space/apps/{id}/lifecycle/make-private    # team/public → private
POST   /api/ai/space/apps/{id}/lifecycle/make-team
POST   /api/ai/space/apps/{id}/lifecycle/make-public     # 走审核（管理员可一键过）
POST   /api/ai/space/apps/{id}/lifecycle/unpublish       # 撤回所有发布版（current_version_id=null）
GET    /api/ai/space/apps/{id}/audit-log

# 分享设置（仅 public/team 可写）
PUT    /api/ai/space/apps/{id}/share-settings    # slug / require_login / access_code / expires_at / referer_allowlist

# 运行
POST   /api/ai/space/apps/{id}/run               # SSE，与 chat 同形
POST   /api/ai/space/apps/{id}/debug-run         # 仅 owner；支持 breakpoints / mock
POST   /api/ai/space/apps/{id}/runs/{run_id}/cancel
POST   /api/ai/space/apps/{id}/runs/{run_id}/resume   # 断点 / 人工节点续跑
GET    /api/ai/space/apps/{id}/runs/{run_id}/trace

# 资源授权
POST   /api/ai/space/apps/{id}/grants            # 加 KB/Tool/MCP/Office
DELETE /api/ai/space/apps/{id}/grants/{type}/{rid}

# Key 管理
POST   /api/ai/space/apps/{id}/keys
DELETE /api/ai/space/apps/{id}/keys/{kid}

# 公共调用入口（外部系统）
POST   /api/v1/ai-app/{app_id}/invoke            # Authorization: Bearer ak_xxx (含 publishable_key 模式)

# 公开页 / 嵌入（仅 public）
GET    /ai-app/{slug}                            # 渲染公开运行页；按 share_settings 决定登录与密码
GET    /embed/ai-space.js                        # 静态 SDK；query 带 publishable_key
```

兼容性：保留 `listAIApps / installAIApp` 现有客户端 fallback，**M5 后端落地前不破坏 fixture 通路**。

---

## 11. 分阶段交付计划

> 估算口径：1 个全栈工程师 = 1 周 ≈ 5 工程日；包含联调与 e2e，但**不包含**产品 review 与 UI redesign 周期。

| 里程碑 | 内容 | 工程量 | 关键产物 | 风险 |
| --- | --- | --- | --- | --- |
| **M0 蓝图与契约**（已写） | 本文档 + ADR-0007 + Flow DSL schema | 0.5w | `docs/plans/ai-space-orchestration.md`, `docs/adr/0007-ai-app-flow-dsl.md` | 低 |
| **M1 数据层 & CRUD** | App / Version / Key 表与路由；前端 Gallery/Studio 骨架 | 2w | `0005_ai_app.py`、`ai_space_routes.py`、`AppGalleryPage`、`AppStudioPage` 表单 tab | 低 |
| **M2 Flow 引擎 v1** | 编译器 + 6 个节点（input/output/llm/kb/tool/if）；非画布、JSON 编辑 | 2.5w | `runtime/compiler.py`、`nodes/`、`/run` SSE、Studio Debug Panel | **中**：节点抽象设计影响后续所有节点 |
| **M3 画布 UI** | React Flow 集成 + 6 节点 UI + 拖拽连接 + zod 校验 | 2w | `packages/ui/flow-canvas`、Studio FlowSection | 中：reactflow 与现有设计 token 整合 |
| **M4 资源授权 & 运行时打通** | KB/Tool/MCP/Office grants；runtime 走 grants 校验；ChatRuntime 接入 | 1.5w | `rbac.py`、`AppRuntimePage` | 低（现有 KB grants 可复制） |
| **M5 应用市场 & 模板** | 公共应用 + Fork（复制为我的）+ 5 个内置模板（客服/翻译/SQL/图问图/邮件助手） | 1.5w | 内置模板 seed、Market 页 | 低 |
| **M6 发布与 OpenAPI** | App Key、限流、`/v1/ai-app/{id}/invoke`、生成 OpenAPI 文档 | 1.5w | `publish/`、`apikey.py`、自动文档 | 中（限流接入 governance 需联调） |
| **M7 高级节点 & 沙箱** | `code`、`http`、`loop`、`subflow`；沙箱 + allowlist | 2w | `sandbox.py`、新节点 | **高**：沙箱安全 |
| **M8 可观测 & 日志** | 节点 trace、Run Log、Annotation、回归用例 | 1.5w | trace UI、`AIAppRunLog` | 中 |
| **M9 IM 渠道适配**（可选） | 飞书 / 钉钉 / 企业微信 webhook 适配器（私有化场景的核心触达） | 1.5w | 渠道 SDK 增量 | 中 |

**合计**：核心可上线（M0–M6）≈ **11.5 人周**；含高级编排与沙箱（到 M7） ≈ **13.5 人周**；含可观测与 IM 渠道（M0–M9） ≈ **16.5 人周**。

**两人并行加速**：前后端按 `frontend(M1.UI / M3 / M5)` × `backend(M1.DB / M2 / M4 / M6 / M7)` 切，可压到 **≈ 9 周**自然时间到 M6。

---

## 12. 技术选型与决策（待 ADR 化）

| 议题 | 推荐 | 理由 / 备择 |
| --- | --- | --- |
| 画布库 | **reactflow** | 行业事实标准；备 `xyflow/svelte-flow`（不适用），`g6`（学习成本高） |
| Flow 存储 | **JSONB（PG）** | 直接版本快照；备 YAML 文件（不利多租户） |
| 编排引擎 | **复用 LangGraph** | 与现 `agents_registry`/`graph/` 对齐；备 self-rolled DAG（重复造轮子） |
| 节点扩展 | **白名单 + 参数 schema** | 防止任意 RCE；不学 Dify 的开放运行时反射 |
| 沙箱 | **进程隔离 + seccomp + RestrictedPython** | 比 `eval` 安全；备 WASI（编译开销 + 体积大，二期再说） |
| App Key 校验 | **中间件**（FastAPI dependency） | 与 JWT 中间件并列；备路由内手写（重复 boilerplate） |
| 状态管理 | **TanStack Query + Zustand**（前端已有）| 不引新 |
| Run 异步 | **既有 ingest_queue 复用** | 不引 Celery |

---

## 13. 风险与缓解

1. **编排引擎抽象抓不准** → M2 先只做 6 节点，DSL 版本号 `version: "1"`，后续兼容靠 migrate。
2. **沙箱安全** → M7 必须代码审计；`code` 节点默认禁用，需管理员开关；保持 `http` 域名 allowlist。
3. **Flow 性能** → 节点级 timeout + 全局 step 上限（默认 200 步）+ 编译缓存；早期用 OpenTelemetry 看 P95 即可。
4. **与现有 chat 路由的概念混淆** → 文档强调"chatbot 类 App 的 `run()` 等价于 `/chat/v2/chat`"；后期可让 `/chat/v2/chat` 内部走 `app_id = "default-chatbot"` 的隐式 App，最终统一。
5. **多租户隔离回归** → 引入 e2e 用例：A 用户的 App 不能 grant B 用户的私有 KB；run 时拒绝。

---

## 14. 与既有计划文档的关系

- `marketplace-phase4.md` 关心**模型平台**市场，正交，不冲突。
- `chayuan-office-redesign.md` 提供 office 资产；本计划只引用其 API。
- `kb-create-and-upload.md` 提供 KB 资产；本计划只引用 KU 接口。
- 本文档将通过 ADR-0007（待写）固化"Flow DSL v1"的语义边界。

---

## 15. 立即可执行的下一步（Definition of Ready）

1. **写 ADR-0007**：Flow DSL v1 + 节点白名单 + 兼容策略。
2. **后端**：新建 `chayuan/server/ai_space/` 包骨架 + `0005_ai_app.py` migration。
3. **前端**：把现有 `SpacePage.tsx` 改名为 `AppGalleryPage.tsx`，新增"我的应用 / 市场" Tab，原有 install/uninstall fixture 通路保留。
4. **契约**：在 `docs/contracts.md` 增 §9 AI Space，列出 §10 全部端点。
5. **看板**：建 epic `AI-SPACE-M1..M6`，按本文档 §11 拆票。

---

## 16. 应用创建（New App）全流程

> 创建动作覆盖四种入口与四种 App 类型，UX 必须**对齐 Coze 的"模板 → 一步到草稿"**与 **Dify 的"类型选择 → Studio"**两种思路；最终我们的形态是 `Drawer 选类型 → 模板（可选）→ 直接进 Studio`。

### 16.1 入口

| 入口 | 触发位置 | 行为 |
| --- | --- | --- |
| **空白创建** | Gallery 右上角"创建应用" | 选类型 → 注入默认 DSL → 进 Studio Draft |
| **从模板** | Market 卡片 / Gallery 模板 Tab | Fork 模板（DSL + 资源占位）→ 进 Studio |
| **Fork 已有应用** | 任何 public app 详情"复制为我的" | 复制 DSL；KB/Tool/MCP **以引用方式带入**，无权访问的资源标红需手工替换 |
| **从 OpenAPI 导入**（二期） | Studio 工具区"导入 API" | 解析 OpenAPI 3.x → 生成对应 `tool_call` 或 `http` 节点 |

### 16.2 App 类型与默认 DSL

四种类型对应四份 seed DSL，落到 `chayuan/server/ai_space/seeds/`：

| 类型 | 典型形态 | 默认节点链 | 运行页 |
| --- | --- | --- | --- |
| **chatbot** | 多轮问答 + 知识 | `input → kb_retrieve(可选) → llm → output` | `ChatRuntime`（复用 ConversationView） |
| **agent** | 工具调用智能体 | `input → llm(tools=[...]) → output`（内部 ReAct 循环） | `ChatRuntime` + 工具调用气泡 |
| **workflow** | 结构化输入产结构化输出 | `input(form) → ... → output(json)` | `WorkflowRuntime`（表单页） |
| **text_generation** | 单轮生成（翻译 / 总结） | `input → llm → output` | 单输入框 + 结果区 |

### 16.3 创建向导（Drawer，三步到底）

1. **Step 1 选类型**：四张大卡片 + 一段说明；右下角"我已经知道"折叠。
2. **Step 2 命名 + 模型**：name / 头像 emoji / 描述 / 默认模型（拉 `/v1/models`） / 语言。
3. **Step 3 模板**：可跳过；选模板则用模板 DSL 覆盖默认 seed。

提交 → 后端 `POST /api/ai/space/apps` 创建草稿 → 前端 `replace('/space/{id}/studio?tab=basic')`。

### 16.4 Fork 时的资源处理（重要）

Fork 的关键问题是**资源所有权**：

- **复制策略（Coze 默认）**：连 KB 一起复制 → 工作量大、KB 体积可能很大，**不采用**。
- **引用 + 占位策略（推荐）**：DSL 中保留 `kb_ids: ["doc:law_kb"]` 引用；导入时校验当前 owner 是否对该 KB 有 `viewer` 以上权限：
  - 有权限 → 直接绑定；
  - 无权限 → 节点标红 + 提示"请替换为你的 KB 或申请授权"，**保存草稿可放过、发布时强制校验**。
- Tool / MCP 同理；模型 ID 不存在时回退到当前 owner 默认模型并 warning。

---

## 17. 编排（Orchestration）画布与语义详解

### 17.1 画布交互（reactflow 之上的二次封装）

| 交互 | 行为 |
| --- | --- |
| **拖入节点** | 左侧 NodePalette 拖到画布；落点自动吸附 16px 网格；插入 `entry → 新节点` 默认连边（若上游有 hover 高亮） |
| **连线** | 出端口拖到入端口；类型不匹配（如 `string` → `array` 入参）连线变红、提示 |
| **多选 / 框选** | Shift / 框选；批量删除 / 复制 / 移动 |
| **复制粘贴** | Ctrl+C/V，跨 App 也支持（剪贴板 = `application/json`） |
| **撤销 / 重做** | `useFlowEditor.ts` 维护 100 步环形 buffer |
| **小地图 / 缩放** | reactflow 内置；保存时持久化 viewport |
| **自动布局** | `dagre` lib，一键整理 |
| **对齐辅助线** | reactflow 插件 `AlignmentGuide` |

### 17.2 变量系统（DSL 灵魂）

三层变量空间：

```
{{ inputs.x }}        ← 来自外部调用 / 用户输入
{{ state.x }}         ← 全局可写，节点 outputs 写入
{{ persona.x }}       ← App 级别只读（system prompt、模型参数）
{{ env.x }}           ← 平台只读（user.id、now、locale）
```

- **节点 output 引用**：`outputs: { answer: "$.answer" }` 把节点返回的 `answer` 字段写到 `state.answer`；下游 `{{ state.answer }}` 引用。
- **类型推断**：每个节点声明 `outputSchema`（zod / pydantic），编译期把 `state` 总 schema 推出来；引用 `{{ state.foo }}` 但 `foo` 不存在 / 类型不符 → 画布上该节点头部出现"类型告警"。
- **Variables Panel**：右侧"变量"侧栏列出当前 `state` 树（结构化），鼠标悬停可"插入变量"到当前编辑的 prompt / 参数。

### 17.3 高级控制流

| 节点 | 语义 | 备注 |
| --- | --- | --- |
| **if** | 单条件分支 `true/false` | 条件用 mini-表达式语言（白名单：`==/!=/>/</&&/\|\|/in/length`） |
| **switch** | 多分支 `case_*` | 默认 `default` 分支 |
| **loop** | for-each 数组 / while 条件 | 默认上限 50 次；可调（管理员可放开） |
| **parallel** | 显式并行 fan-out（其余无依赖节点编译期自动并行，此节点用于"显式标注 + 控制 max_concurrency"） | 收敛节点必须存在（join） |
| **try / catch** | 错误捕获 | `try` 失败走 `catch` 边；`catch` 节点可访问 `{{ state.error }}` |
| **subflow** | 调用另一个 App | 输入参数 = 子 App 的 inputs；输出 = 子 App 的 outputs |
| **human_task** | 人机协同节点：通知用户 → 表单提交 → 续跑（详见 §22） | 持久化挂起（LangGraph Checkpointer）+ 通知 fan-out + Tasklist |

### 17.4 错误处理与重试

每个节点支持：

```jsonc
"retry":   { "max": 3, "backoff": "exponential", "on": ["timeout", "5xx"] }
"timeout": 30000
"fallback":{ "next": "n_safe_default" }   // 重试耗尽后的兜底边
```

LLM / HTTP / Tool 节点默认 `retry.max=2 timeout=30s`；Code / KB 默认不重试。

---

## 18. 调试（Debug）体系：三层通用

> 调试是 Coze / Dify 上**最影响留存**的功能，必须做透。三层各有侧重，互为补充。

### 18.1 节点级调试（"Run This Node"）

每个节点右上角一个 `▶` 按钮；点击 → 弹 RHS 抽屉：

| 节点类型 | 调试形态 |
| --- | --- |
| **llm** | 显示已渲染的 prompt（变量已填）、模型 / 参数；可手改 inputs 跑一发；输出 token、耗时、首 token 延迟 |
| **kb_retrieve** | 输入 query；表格展示召回 chunk + score + source；可调 top_k / threshold 实时预览 |
| **tool_call** | 表单按 Tool schema 渲染；走真实 `/tools/call`；右侧显示 raw response + 解析后字段 |
| **mcp**（特例 tool） | 先显示 MCP 连接状态（健康检查）；能力清单 + 入参表单 |
| **http** | Postman 风格：method / URL / headers / body / auth；保存为 collection；支持 `{{}}` 变量 |
| **code** | 内嵌 Monaco Editor；左侧 inputs 表单（注入预览的 state 子集） + 右侧 output / stdout / stderr |
| **if / switch** | 给定 state 模拟值，显示哪条分支命中 |

**Tool 调试器是独立产物**：除 Studio 内嵌外，在 `/tools` 平台页也提供"调试 Tool"按钮，复用同一组件（`packages/ui/src/tool-debugger/`）。MCP 同理。

实现要点：
- 后端新增 `POST /api/ai/space/nodes/{node_type}/run`（**绕开 Flow，仅跑单节点**），统一鉴权与限额；
- 凭证注入：`vault://...` 占位符在调试时显示为 `••••`，仍由后端注入真实值；
- 调试历史：每个节点保留最近 20 次调试记录（localStorage），方便参数对比。

### 18.2 Flow 级调试（DebugPanel）

Studio 顶部"调试运行"按钮 → 切到 DebugPanel：

```
┌───────────────────────────────────────────────────────┐
│ Inputs (form, 由 inputs schema 渲染)                  │
│   user_input: "查一下 2024 年限购政策"                │
│   [▶ 运行]   [⏸ 暂停]   [⏹ 停止]   [↺ 重放]          │
├───────────────────────────────────────────────────────┤
│ 时间线（按 SSE 事件实时插入）                         │
│   ▸ n_input        12ms                               │
│   ▾ n_kb_retrieve  240ms   召回 6 条 ▸               │
│       └─ chunk #1  score 0.82  …                     │
│   ▾ n_llm          1.4s    324 tokens  ttft 280ms    │
│       └─ prompt（已渲染） / output                    │
│   ▾ n_output       2ms                                │
├───────────────────────────────────────────────────────┤
│ Variables Watch（实时 state JSON 树）                 │
│ Trace / Logs / Errors（tab 切换）                     │
└───────────────────────────────────────────────────────┘
```

特性：

- **逐节点展开**：点开看 input/output/duration/tokens/ttft；每个节点末端有"复制为节点测试用例"。
- **断点**：在画布上右键节点 → "设为断点"；DSL 写 `breakpoint: true`；调试运行到断点暂停 → 用户可改变量后 `继续`。
- **单步**：从入口逐节点 step；适合排查"为什么走到了 false 分支"。
- **变量 Watch**：右侧 JSON 树实时刷新（diff 高亮）；可固定字段到 watch list。
- **重放**：从历史 Run 列表选一条 → 回填 inputs + 完全重跑（用相同模型版本）。
- **A/B 对比**：选两个版本（draft vs v1.2.0）+ 同一组 inputs → 并排时间线。
- **Mock 模式**：LLM / HTTP / Tool 节点可勾选 "使用 mock 响应"，绕开外部依赖；mock 内容来自上一次成功 run（自动捕获）。

### 18.3 回归测试集（Test Suite）

- 每个 App 维护一个测试集：`[{name, inputs, expect: {output_match | path_match | assertion_code}}]`。
- 来源：手动新建 / 从历史 run 一键加入 / 从 Annotation 转化。
- **CI**：发布前自动跑测试集；fail 阻断 publish（可强制覆盖，记录强制原因）。
- **Annotation**：Runtime 页对话气泡上点"标注" → 进入 review 队列 → 转测试用例 / 反例。

后端表：`AIAppTestCase`、`AIAppTestRun`，挂在 `ai_space/eval/`，复用项目现有 `eval/` 子模块的评分体系。

---

## 19. 分享（Share / Publish）四通道

| 通道 | 形态 | 适用 |
| --- | --- | --- |
| **平台公开页** | `https://<host>/ai-app/{slug}`（`visibility=public` 时免登录；`team` 需登录；`private` 仅自己） | 体验 / Demo / 营销 |
| **嵌入式** | `<iframe src="...">` 或 `<script async src=".../embed.js?app_id=xxx&theme=light">` 注入悬浮球 | 嵌官网 / 客户系统 |
| **OpenAPI Key** | `Authorization: Bearer ak_...` 调用 `/api/v1/ai-app/{id}/invoke`（OpenAI 兼容 SSE） | 后端服务集成 |
| **第三方渠道**（M9 二期） | 飞书 / 钉钉 / 企业微信 / Slack 适配器 | 即时通讯触达 |

### 19.1 公开页（Web Runtime）

- 路由：`/ai-app/:slug`（slug 默认 = `app_id` 短哈希；可自定义且全局唯一）。
- 鉴权：visibility 决定；可选项 `requireLogin: true | false`、`accessCode` 访问密码（PBKDF2 校验）、`expiresAt` 过期时间。
- 嵌入信息：`og:title / og:description / og:image`（=app icon），方便链接预览。
- 端用户能看的：对话框 + 头像 + welcome 消息 + 推荐问题（在 Persona 中配置）；**看不到** Flow 内部、变量、模型名、token 数。
- 历史会话：登录用户落库；游客会话 24h localStorage。

### 19.2 嵌入 SDK

- `embed.js` 单文件 < 30KB（gzip）；从 `chayuan-server` 静态目录提供。
- 用法：

```html
<script async src="https://host/embed/ai-space.js"
        data-app="ak_xxx_publishable"
        data-position="bottom-right"
        data-theme="auto"></script>
```

- 通信：iframe + `postMessage`；样式可注入主题变量与 logo。
- 安全：`publishable_key`（前端可暴露） vs `secret_key`（仅服务端用），区别于 §7 的 `ak/sk`，**前端 key 仅允许 chat:invoke + 域名白名单 referer 校验**。

### 19.3 OpenAPI 调用

- 标准 OpenAI 兼容：`POST /api/v1/ai-app/{id}/invoke` body 同 `chat/completions`，`stream=true` 返回 SSE。
- 自动生成文档：每个 app 有 `/api/ai/space/apps/{id}/openapi.json`，前端打开 Swagger UI。
- 限流：app_key 维度（QPS + 日 token 上限），与 §7 限流体系合并。

### 19.4 分享面板（PublishDialog）

```
┌───────────────────────────────────────┐
│ 发布到                                │
│ ☑ 平台公开页    [生成链接][复制][二维码]│
│ ☐ 嵌入到网站    [复制 <script>]      │
│ ☑ 提供 API      [创建 Key][列出 Keys] │
│ ☐ 应用市场      [提交审核]           │
├───────────────────────────────────────┤
│ 版本：v1.2.0（含 changelog 编辑框）   │
│ 高级：访问密码 / 过期 / 域名白名单    │
└───────────────────────────────────────┘
```

发布动作 = `POST /apps/{id}/versions/{vid}/publish` + 写 `share_settings` 子表。

### 19.5 应用市场（Market）

- `visibility=public` + `submit_to_market=true` 进市场审核队列；
- 审核维度：内容合规 / Prompt 是否泄露密钥 / 资源依赖是否安全；
- 通过后出现在 `MarketPage`，可被任何用户 Fork（见 §16.4）；
- 内置 5 个种子模板（客服 / 翻译 / SQL 助手 / 图问图 / 邮件助手）随 server 一起发布。

---

## 20. 协作与版本管理

### 20.1 草稿 vs 已发布版本

```
[App]
  ├─ draft  ← Studio 编辑、Debug 跑的对象（永远存在；不计入"版本"）
  └─ versions: [ v1.0.0, v1.1.0, v1.2.0 (current_version_id) ]

外部 invoke:
  - 不带 version → 走 current_version_id（latest published）
  - ?version=v1.1.0 → pin 到指定版本（灰度回滚必备）
Debug 运行:
  - 走 draft（除非显式选 "运行已发布版本"）
```

> **注意**：本节只讲 **version 维度**（草稿 / published）。App **可见性维度**（私有 / 团队 / 公开）以及合成的"草稿态 / 私有态 / 公开态"用户感知见 **§21**。两套概念正交：一个公开 App 也持续有 draft，owner 改 draft 不影响线上 v1.2.0。

### 20.2 版本历史

- 列表 + 元信息（version、publisher、changelog、resource grants 快照）；
- **Diff**：DSL JSON diff（用 `json-diff` 库着色） + persona/grants/key-scopes 三块单独 diff；
- **回滚**：选历史版本 → "复制为草稿"（不删现有 published）；用户人工 publish 才生效。

### 20.3 多人编辑（v1：乐观锁）

- 每次 GET draft 带 `etag`；保存时 `If-Match: etag` → 服务端比对：
  - 一致 → 接受、`etag` 自增；
  - 冲突 → 返回 409 + 服务端最新 DSL；前端弹 dialog "另一人已编辑：放弃我的 / 覆盖对方 / 比较 diff"。
- 在 Studio 顶部展示"当前编辑者：xxx 30 秒前"心跳（轻量 polling）。
- v2 实时协同（Yjs CRDT）属于 M9 之后的事，不在本期。

### 20.4 节点级评论 / 审阅

- 任意节点右键"评论" → 锚点固定到 node id；
- 评论列表面板（侧栏）+ 状态（open/resolved）；
- 与版本绑定：发布时 open 评论提示"还有 N 条未解决"。

---

## 21. 应用生命周期与三态（草稿 / 私有 / 公开）

> **核心模型**：一个 App 同时持有"**编辑态**"（draft / published）与"**可见态**"（visibility）两个正交维度；UI 上把它们合成**三个对外展示的标签**——`草稿（Draft）` / `私有（Private）` / `公开（Public）`，对应用户可感知的状态。

### 21.1 状态合成规则

| 用户感知状态 | `has_published_version` | `visibility` | 含义 |
| --- | --- | --- | --- |
| **草稿（Draft）** | false | private（强制） | 从未发布过；只有 owner 能看 / 编辑 / 调试 |
| **私有（Private）** | true | private | 已发布但不公开；owner + 显式 grants 用户可运行；外部 invoke 需 ApiKey |
| **团队（Team）** | true | team | 已发布到 owner 所在 group；group 内成员可见且可运行 |
| **公开（Public）** | true | public | 已发布到全域；登录用户均可见 / 运行；游客视 `share_settings.requireLogin` 决定 |

> "**团队（Team）**"是私有→公开的中间态；UI 上若组织未开 group，可隐藏此选项，仅展示三态。本章后续以**草稿 / 私有 / 公开**三态作为主线讲解。

### 21.2 状态机（带护栏的合法迁移）

```
            ┌──────────┐
            │  草稿    │   每次 publish 都生成新 version
   create → │  Draft   │ ──────────────────────────────┐
            └──────────┘                                │
                  ▲                                     ▼
                  │                              ┌──────────┐
                  │ 任意已发布态 → "继续编辑"    │  私有    │
                  │（仅修改 draft，不影响线上） │ Private  │
                  │                              └──────────┘
                  │                                ▲     │
                  │                                │     │
                  │                       "撤回到私有"  │ "公开"
                  │                                │     ▼
                  │                              ┌──────────┐
                  │                              │  公开    │
                  │                              │  Public  │
                  │                              └──────────┘
                  │                                     │
                  │              「下线（Unpublish）」  │
                  └─────────────────────────────────────┘
```

合法迁移与守护：

| 迁移 | 守护条件 | 副作用 |
| --- | --- | --- |
| `Draft → Private`（首次发布） | DSL 校验通过；grants 全有权限；测试集 pass（或强制） | 创建 `version` + 设 `current_version_id`；ApiKey 仍需手工创建 |
| `Private → Team` | owner 拥有 group 写权 | `visibility=team`，更新索引；不复制资源 |
| `Team → Public` | 内容审核通过（管理员） | `visibility=public`；进入 Market；`embed.js` 与公开 slug 生效 |
| `Public/Team → Private` | owner 触发 | 立即下市场；公开页 404；已分发的 ApiKey **保持有效**（私有也可被 Key 调用） |
| 任意发布态 → `Draft`（"撤回所有发布版"） | 二次确认 | 删除所有 version？❌ **不删**；仅 `current_version_id=null`，公开通道全部失效；历史 run 保留 |
| `Draft → Public` 直发 | 等价于 `Draft → Private → Public` | 内部走两步；UI 提供一键直发 |

> **关键不变量**：`Draft` 永远 `visibility=private`（即使数据库里写 public 也按 private 处理）；公开 / 团队必须有 `current_version_id`。

### 21.3 各状态下的能力矩阵

| 行为 | Draft | Private | Team | Public |
| --- | --- | --- | --- | --- |
| owner Studio 编辑 / 调试 | ✅ | ✅（改 draft，不影响线上） | ✅ | ✅ |
| owner 运行 draft | ✅ | ✅ | ✅ | ✅ |
| 被 grants 用户运行 published | ❌（无 published） | ✅ | ✅ | ✅ |
| 同组用户在 Gallery 可见 | ❌ | ❌（除非显式 grant） | ✅ | ✅ |
| 出现在应用市场 | ❌ | ❌ | ❌ | ✅ |
| `/ai-app/{slug}` 公开页 | 404 | 404 | 404 | ✅ |
| `embed.js` 嵌入 | ❌ | ❌ | ❌ | ✅（按 publishable_key + referer） |
| OpenAPI `Bearer ak_` 调用 | ❌（无 published） | ✅ | ✅ | ✅ |
| 被 Fork | ❌ | ❌ | ✅（owner 配置允许时） | ✅ |
| 被搜索引擎索引 | ❌ | ❌ | ❌ | ✅（默认；可关 `noindex`） |
| 出现在用户"我的应用 / 历史 run" | owner 看到 | owner + grantees | group 全员 | 所有运行过的用户 |

### 21.4 数据模型增量（覆盖三态语义）

```python
# db/models/ai_app_model.py（增量字段；其他保持 §4 原样）
class AIApp:
    visibility: Literal["private", "team", "public"]   # 默认 private
    current_version_id: UUID | None                    # NULL ↔ "草稿态"
    discoverable: bool = True                          # public 时是否进市场（管理员审核结果）
    allow_fork: bool = False                           # team/public 是否允许被 Fork
    noindex: bool = False                              # public 时是否在 robots/前端拒绝索引

class AIAppShareSettings:           # 新表，可选；不存在视同默认值
    app_id: UUID  PK
    slug: str    UNIQUE NULLABLE    # public 时必填，私有时 NULL
    require_login: bool = True       # public 时游客是否需登录
    access_code_hash: str | None     # 公开但加密码访问
    expires_at: datetime | None      # 公开链接过期时间
    referer_allowlist: list[str]     # embed.js 限制域名
    publishable_key: str | None      # 仅 chat:invoke 的弱 key

class AIAppAuditLog:                 # 复用现有审计基础设施；可塞 governance 表
    app_id, actor_id, action: Literal["publish","unpublish","make_public",
                                      "make_team","make_private","approve_market",
                                      "reject_market","force_publish_with_failed_tests"],
    from_state: str, to_state: str, payload: dict, created_at
```

`current_version_id IS NULL` 即"草稿态"——前端不需要单独的 `status` 字段，**用 `current_version_id` + `visibility` 推导前端三态标签**：

```ts
// packages/api/src/aiSpace.ts
export function deriveAppLifecycle(app: AIApp): "draft" | "private" | "team" | "public" {
  if (!app.current_version_id) return "draft";
  return app.visibility;  // private | team | public
}
```

### 21.5 列表 / 搜索 / 权限过滤

后端 `GET /api/ai/space/apps?scope=...` 的 `scope` 取值与对应 SQL 谓词：

| scope | 含义 | 过滤 |
| --- | --- | --- |
| `mine` | 我的（含草稿） | `owner_id = me` |
| `shared` | 别人共享给我的 | `EXISTS grants(app_id, user_id=me) OR (visibility='team' AND group_id IN my_groups)` |
| `public` | 应用市场 | `visibility='public' AND discoverable=true AND current_version_id IS NOT NULL` |
| `all` | 我能看见的全部（默认 Gallery） | `mine UNION shared UNION public` |

**草稿不会出现在 `shared` 或 `public`**——硬性约束，由 `current_version_id IS NOT NULL` 兜底。

### 21.6 运行时鉴权（路由维度）

| 路由 | 草稿 | 私有 | 公开 |
| --- | --- | --- | --- |
| `POST /apps/{id}/debug-run` | owner only | owner only | owner only |
| `POST /apps/{id}/run`（前端运行页用） | owner only | owner + grantees | 所有登录用户（require_login=true 时） |
| `POST /api/v1/ai-app/{id}/invoke`（OpenAPI） | 403（无 current_version） | 凭 ApiKey | 凭 ApiKey 或 publishable_key |
| `GET /ai-app/{slug}`（公开页路由） | 404 | 404 | 200（按 share_settings 决定登录与密码） |
| `GET /embed.js?app=...` | 403 | 403 | 200（仅 public + publishable_key） |

中间件实现：在 `ai_space_runtime_routes.py` 入口处统一判断 `(visibility, current_version_id, caller)`，**返回拒绝原因 enum 化**（`NOT_PUBLISHED / VISIBILITY_PRIVATE / NEED_LOGIN / NEED_ACCESS_CODE / EXPIRED / REFERER_BLOCKED`），前端按 enum 显示中文提示。

### 21.7 状态切换的副作用清单（实现时不能漏）

`make_public`：
- [ ] 写 `audit_log` 一条
- [ ] 创建 / 更新 `slug`（与 `app.id` 短哈希冲突时 +1 后缀）
- [ ] 通知 Market 索引（即便是同步内存 index）
- [ ] 提示用户分享面板 §19 各通道现在可用
- [ ] 反向：撤回到 private 时 `slug` 仍**保留**（避免 SEO 死链回流后又变 404 不一致；二次公开同 slug）

`make_private` / `unpublish`：
- [ ] `current_version_id = null`（unpublish）或仅修改 visibility（make_private）
- [ ] 公开页 404；嵌入 script 命中后回 410 Gone
- [ ] **正在进行中的 SSE run 不强制中断**，但新 run 拒绝
- [ ] ApiKey 不撤销（私有可调）；publishable_key 失效
- [ ] 历史 run / Trace 保留（合规可审计）

`publish 新版本`：
- [ ] `versions` 追加；`current_version_id` 切到新版本
- [ ] **进行中的 SSE run 走老 version 跑完**（已绑定 `version_id`）
- [ ] 编译缓存 invalidate
- [ ] 发布前自动跑测试集；fail 阻断（可强制 + 写审计 `force_publish_with_failed_tests`）
- [ ] 老版本不删，可回滚

`delete app`：
- [ ] 仅 Draft 或 Private 可直接删；Team / Public 必须先 unpublish
- [ ] 软删除（`deleted_at`）30 天保留，可恢复；30 天后清 versions / runs / trace blobs

### 21.8 UI 表达（前端必做项）

- **Gallery 卡片角标**：`草稿（灰）` / `私有（蓝）` / `团队（紫）` / `公开（绿）`，统一 `packages/ui/src/lifecycle-badge/`。
- **Studio 顶栏**：左侧应用名 + 当前 lifecycle badge + "正在编辑：草稿（基于 v1.2.0）"；右侧 `保存草稿 / 调试运行 / 发布`。
- **PublishDialog**：单个对话框完成"私有发布 / 升级到团队 / 升级到公开"三选；不要做成三个不同入口。
- **Lifecycle Stepper**：在发布对话框顶端展示 `草稿 → 私有 → 团队 → 公开` 四态阶梯，当前节点高亮，可单步前进 / 后退。
- **状态切换前 Confirm**：从 Public → Private 时弹"将下市场，公开页失效"；从 Private → Public 时弹"将进入审核队列 / 立即生效"。
- **Runtime 页提示**：私有 App 用户首次访问，顶部一条 banner "这是 owner 共享给你的私有应用"。

### 21.9 与 §20 版本管理的衔接（避免概念混淆）

| 概念 | 隶属维度 | 例子 |
| --- | --- | --- |
| 草稿 / 私有 / 公开 | App 级别（生命周期） | "我的客服 Bot 是公开的" |
| draft / published version | Version 级别（编辑态） | "v1.2.0 已发布；我正在改 draft 准备发 v1.3.0" |
| 当前生效版本 | Version 指针（运行时） | `app.current_version_id = v1.2.0.id` |

> 一个**公开**的 App 也有自己的 **draft**——owner 可以一边编辑 draft 一边对外保持 v1.2.0 提供服务，互不影响。这是与 Notion / Figma 的 "Publish" 心智一致的关键。

### 21.10 边界 / 异常案例

1. **公开 App 的 owner 失去某 KB 权限怎么办？** 已发布 version 中的 grants 是**值快照**而非引用——发布时把 `kb_ids` 当时的可访问性记录到 `version.resource_grants`；运行时按 version 走，避免运行时校验失败。重新 publish 时再重新校验。
2. **公开 App 被举报违规** → 管理员强制 `make_private`（带审计原因） + 写通知给 owner；`slug` 保留但路由返回 410 + 原因。
3. **草稿写到一半模型平台被删** → Studio 顶部红条"当前模型已下线，请切换"；保存草稿不阻塞，发布会校验失败。
4. **被 Fork 后原 App 删除** → Fork 是值快照；不级联删除。
5. **team 作用域含义** → 沿用现有 `auth/groups` 表；用户属于多 group 时取并集；group 删除时该 App 自动降级为 private。
6. **公开但要求登录** → `share_settings.require_login=true`，未登录访问跳登录回跳；这是私有化场景默认值（只对内网用户开放）。

### 21.11 清单落点

本节工作已落到 **§27 中的两个里程碑**：

- 部分前置（数据模型 + Gallery 三 Tab + lifecycle badge）合入 **M1**；
- 完整状态机 + 路由 + 审计 + UI Stepper 集中于 **M5.7 生命周期与三态（1.2w）**——位于 M5.5 与 M6 之间，作为发布功能的前置依赖；
- M6 / M6.5 / M8.5 都消费此模块（grants 值快照、`/ai-app/{slug}` 鉴权、audit log diff）。

---

## 22. 人机协同（Human-in-the-Loop）：编排中等待用户输入

> **场景描述**：流程跑到某个节点 → 系统**通知**指定用户（站内 / 飞书 / 邮件） → 用户打开**表单或对话**填写资料 / 上传附件 / 选择审批 → 提交后流程**继续**到下一节点。这是企业流程类 App（请假审批、合同会签、客户尽调、定制化客服转人工）的核心刚需。

### 22.1 行业最佳实践对照

| 系统 | 模式 | 状态保存 | 等待恢复机制 | 表单 | 借鉴点 |
| --- | --- | --- | --- | --- | --- |
| **BPMN 引擎（Camunda / Activiti / Flowable）** | **User Task** + Form Key | 流程实例持久化到 DB | Tasklist 拉取待办 → 用户提交 → `complete` 事件 | XML / JSON Form schema | **Tasklist 心智 + Form Key 解耦表单与节点** |
| **AWS Step Functions** | `waitForTaskToken` | DynamoDB 持久化 | 外部回调 `SendTaskSuccess(token, output)` 唤醒 | 任意（自定义） | **Task Token 模式**，token 唯一标识"挂起态" |
| **Temporal** | `Signal` / `Update` | 事件溯源（持久化） | Workflow 内 `await Workflow.signal('approve')` | 由调用方决定 | **代码即流程**，挂起天然支持 |
| **n8n** | Wait + Webhook | 数据库 | Webhook 回调或定时唤醒 | n8n 内置表单页 | **简单 webhook 回调** |
| **Slack / Teams 审批** | Interactive Message | 应用方 DB | 按钮回调 → action_id + response_url | Block Kit | **IM 内联表单 + 按钮** |
| **Coze** | 暂无原生（用对话上下文模拟） | -- | -- | -- | 反例：纯对话不够 |
| **Dify** | 起始 Form Input；不支持中途 wait | -- | -- | -- | 反例：起步可借鉴，中途空白 |

**结论**：**Camunda 的 User Task + AWS 的 Task Token 是最对路的两条主线**——前者给我们 Tasklist 心智，后者给我们"流程挂起→外部唤醒"的工程实现。

### 22.2 我们的设计：`human_task` 节点

DSL 增加节点类型：

```jsonc
{
  "id": "n_collect",
  "type": "human_task",
  "params": {
    "title": "请提交合同附件",
    "description": "请上传 PDF 并填写客户公司全称",

    "assignee": {                       // 谁来做这件事
      "strategy": "expression",         // expression | static | role | requester
      "value": "{{ state.customer_owner_id }}"   // 或 "user:42"、"group:legal" 等
    },

    "form": {                           // JSON Schema（zod 校验）
      "type": "object",
      "required": ["company_name", "contract_file"],
      "properties": {
        "company_name": { "type": "string", "title": "客户公司全称" },
        "contract_file": { "type": "string", "format": "file",
                           "x-accept": ".pdf", "x-max-size": "10MB" },
        "comment":      { "type": "string", "title": "备注", "format": "textarea" }
      }
    },

    "notify": {                         // 通知通道，多选
      "channels": ["inapp", "feishu", "email"],
      "template": "tpl_contract_request"
    },

    "deadline": "PT24H",                // ISO-8601 时长，超时走 fallback
    "reminder": ["PT1H", "PT4H"],       // 多次提醒
    "allow_reassign": true,             // 当前 assignee 可转派
    "allow_cancel_by_owner": true       // App owner 可强制取消
  },
  "outputs": {
    "company_name":  "$.form.company_name",
    "contract_file": "$.form.contract_file",
    "submitter_id":  "$.submitter_id",
    "submitted_at":  "$.submitted_at"
  },
  "next": "n_verify",
  "fallback": { "on": "deadline_exceeded", "next": "n_escalate" }
}
```

### 22.3 运行时实现：Task Token + 持久化挂起

**核心难点**：HTTP / SSE 不可能挂连接等用户 24 小时；需要**把 run 状态序列化到 DB**，等回调再唤醒。

实现选型**复用现有 LangGraph 的 checkpoint 机制**（LangGraph 原生支持 `Checkpointer` 接口，配合 PostgresSaver 可以把 StateGraph 的中间状态完整 dump 到 PG）：

```
跑到 human_task 节点：
  1. 编译器生成节点函数 → 函数体内做：
     a) 解析 assignee → user_id（或 group → 派单到任意一人）
     b) 渲染表单 schema、通知模板
     c) 写一条 ai_app_task 记录（含 task_token = uuid7）
     d) 派发通知（in-app push + 飞书/邮件 fan-out）
     e) raise PauseSignal(token)   ← 自定义异常
  2. PauseSignal 被 executor 捕获 →
     a) checkpoint 当前 StateGraph 到 PG（沿用 LangGraph PostgresSaver）
     b) run.status = "waiting_human"
     c) SSE 推 `node_paused` 事件 + task_token，关闭流（前端不挂连接）
  3. ──────── 进程可以重启、连接可以断 ────────
  4. 用户在 Tasklist / 通知打开表单 → 提交 →
     POST /api/ai/space/tasks/{token}/complete  body=form_data
     a) 鉴权（assignee）+ 表单 zod 校验
     b) ai_app_task.status = "completed"，存 form_data
     c) 投递 ResumeJob 到 ingest_queue（已存在）
  5. Worker 取出 ResumeJob → 用 token 找 checkpoint → restore →
     把 task 的 form_data 注入 state → 继续 StateGraph 执行
  6. 客户端通过 GET /apps/{id}/runs/{run_id}/stream（SSE 重连）继续接事件
```

**关键不变量**：

- `task_token` 是流程挂起的唯一句柄；**只能 resolve 一次**（DB 唯一索引 + 行锁）。
- `run_id` ↔ `task_token` 一对多（一个 run 可串多个 human_task）。
- Resume 时**checkpoint 内的所有 state、上游节点 outputs、persona 全部还原**——包括重启进程后。
- 重连：客户端用 `GET /apps/{id}/runs/{run_id}/stream` 拿 backlog + live 事件（沿用 KB 远端同步的 backlog 机制 §39 同形）。

### 22.4 数据模型增量

```python
# db/models/ai_app_task_model.py
class AIAppTask:
    id: UUID
    task_token: str                   # uuid7，DB UNIQUE，外露给前端 / IM
    app_id: UUID
    version_id: UUID
    run_id: UUID                      # 流程实例
    node_id: str                      # DSL 节点 id
    title, description: str
    form_schema: dict                 # 节点定义时拍快照
    assignee_user_id: int | None      # 单人指派
    assignee_group_id: int | None     # 组指派（取任一人即结案）
    requester_user_id: int            # 触发流程的人
    status: Literal["pending", "claimed", "completed",
                    "cancelled", "expired", "reassigned"]
    claimed_by: int | None            # group 指派被认领后落 user_id
    claimed_at, completed_at: datetime | None
    submission: dict | None           # 提交的 form_data
    deadline_at: datetime | None
    reminders_sent: list[datetime]
    created_at, updated_at: datetime

class AIAppTaskEvent:                 # 审计 + Tasklist 时间线
    task_id, actor_id,
    type: Literal["created","notified","claimed","reassigned",
                  "submitted","cancelled","expired","reminded"],
    payload: dict, at: datetime
```

migration `0008_ai_app_human_task`。

`AIAppRunLog` 增 `paused_count`、`total_pause_duration_ms` 字段，便于看流程被人卡了多久。

### 22.5 通知通道（Fan-out）

通知用一个统一 `Notifier` 抽象，下分 4 实现，与 §M9 IM 渠道适配共享代码：

| Channel | 实现要点 | 落点 |
| --- | --- | --- |
| **inapp** | 写 `notification` 表（已有 user inbox 基础设施可复用）+ WebSocket / SSE 推送 | `chayuan/server/notify/inapp.py` |
| **feishu / 钉钉 / 企微** | 卡片消息含 `打开任务` 按钮（深链 `/space/tasks/{token}`），按钮回调 = "已读" 标记 | M9 渠道适配复用 |
| **email** | 模板渲染 + 任务深链；私有化部署默认 SMTP | `notify/email.py` |
| **sms**（可选） | 短信网关；私有化默认关 | `notify/sms.py` |

通知模板支持 `{{state.x}}` 变量，与 DSL 模板语法同形。**通知失败不阻塞流程**（异步重试 + 写 `task_event` 标 `notify_failed`），任务依然处于 `pending`，用户可主动从 Tasklist 进入。

### 22.6 Assignee 解析策略

| strategy | value 形式 | 解析 |
| --- | --- | --- |
| `static` | `"user:42"` / `"group:legal"` | 直接取 |
| `expression` | `"{{state.x}}"` 等 | 变量替换后再按 static 解析 |
| `role` | `"role:approver"` | 在 App `roles` 配置中查（如"客户负责人"），最终得到 user_id |
| `requester` | -- | 取触发本次 run 的 user（适合"自己填表"场景） |
| `dynamic_lookup`（二期） | `lookup://hr.api/manager_of?user_id={{x}}` | 调外部 HTTP 查上级 |

> 解析失败（用户已离职 / 组为空）→ 节点标 `error`，走 `fallback`，避免无人认领。

### 22.7 Tasklist（待办中心）：用户侧主入口

新建一级页面 **Tasks（我的待办）**，挂在主导航与全局 CMD-K：

- 列表：**待我处理 / 我发起的 / 我组里待认领 / 已完成**四 Tab。
- 每条卡片：App icon + 标题 + 截止时间 + 来自哪个 run + `去处理` 按钮。
- 进入详情：渲染 form schema → 用户填写 → 提交 → toast + 关闭。
- 实时性：WebSocket / SSE 单连接订阅 `notification:user_id={me}`；新任务实时插入。
- 移动端 / Tauri：原生通知（OS toast）+ 点击拉起到任务详情。
- 批量操作：多选 → 批量审批（仅"是/否"型简单任务）。

PC / 移动端复用同一 form 渲染器：基于 JSON Schema → 直接出 `Input / Textarea / Select / Upload / DatePicker / Checkbox`，组件库 `packages/ui/src/json-schema-form/`（大量库可选：`@rjsf/core`、`uniforms`，**首选 @rjsf/core + 自定义 widget 适配设计 token**）。

### 22.8 Studio 中的 Human Task 编辑体验

画布拖入节点后，右栏配置：

1. **基础**：title / description（支持 `{{state.x}}`）
2. **Assignee**：下拉 strategy + 对应 value 输入控件（人员选择器 / 组选择器 / 表达式编辑器）
3. **Form Builder**：可视化 schema 编辑器（拖拽字段 / 配置必填 / 校验规则）；高级用户可切到 JSON 直写模式
4. **Notify**：勾选通道 + 模板下拉；预览
5. **Deadline / Reminder**：时长输入 + 多次提醒
6. **Fallback / Escalate**：连一条 `deadline_exceeded` 边到另一节点

调试器：节点 ▶ → 模拟挂起 → 列出当前 assignee → 自动以模拟身份打开表单 → 提交 → 看下一节点参数。**调试模式下 deadline / 通知都走 mock**，不真发飞书。

### 22.9 与既有能力的衔接

| 关注点 | 实现方式 |
| --- | --- |
| **持久化挂起** | LangGraph `Checkpointer` + PostgresSaver；StateGraph compile 时注入 |
| **唤醒队列** | 现有 `ingest_queue` 异步消费 `ResumeJob` |
| **重连** | `GET /runs/{id}/stream` 复用 `JobManager` backlog（KB 同步同款） |
| **通知 fan-out** | 与 M9 IM 渠道适配共享 `Channel` 抽象；可独立先做 inapp + email |
| **鉴权** | 提交时校验 caller 是 assignee（user / group 成员）；owner 可强制 cancel |
| **运行时拒绝** | 公开 App 也可有 human_task；assignee 解析失败的 fallback 必填校验 |
| **审计** | `AIAppTaskEvent` 全量；run trace 中插入 `task_paused` / `task_resumed` 事件 |

### 22.10 边界与失败模式

| 情况 | 处理 |
| --- | --- |
| 用户长时间不处理 | reminder 多次提醒 → deadline 触发 fallback 边；fallback 无配置则 run 标 `failed` |
| assignee 离职 / 组为空 | 解析期错误 → 节点 `error` 状态 → fallback；可在 retry 内换"上级"重试 |
| 同 run 内同一用户被分派多次 | 允许（不同 task_token 相互独立） |
| 用户重复提交同一 token | DB 行锁 + 状态机：`pending → completed` 单向，重复提交回 409 |
| 任务被发送到 IM 后用户在 IM 直接回复 | M9 渠道适配里把"消息卡片按钮回调"路由到 `tasks/{token}/complete` |
| App 被 unpublish / 删除时还有 pending tasks | 默认级联 cancel 所有 pending tasks；可配置"完成后再删" |
| 跨版本：流程挂起时 owner 发布了新 version | 已挂起 run **保留绑定的 version_id**，按老 version 完成；不影响新 version |
| 安全：表单上传文件 | 走现有 `FileStorage` + 类型 / 大小白名单；与 KB 上传同链路 |
| 隐私：通知模板里渲染了敏感变量 | 模板编辑器支持 `{{state.x | redact}}` 过滤器 |
| 集群部署多实例 | 依赖现有 `ingest_queue` 已有的分布式 worker；同 token 行锁兜底 |

### 22.11 API 契约（增量）

```http
# Tasklist
GET    /api/ai/space/tasks?scope=assigned_to_me|created_by_me|group_inbox|done
GET    /api/ai/space/tasks/{token}
POST   /api/ai/space/tasks/{token}/claim          # group 指派认领
POST   /api/ai/space/tasks/{token}/reassign       # body: {to_user_id, reason}
POST   /api/ai/space/tasks/{token}/complete       # body: form_data
POST   /api/ai/space/tasks/{token}/cancel         # 仅 owner / requester

# Run 维度
GET    /api/ai/space/apps/{id}/runs/{run_id}/stream    # SSE 重连，含 backlog
POST   /api/ai/space/apps/{id}/runs/{run_id}/cancel    # 取消 run + 所有 pending tasks

# 节点调试器（M2.5 增量）
POST   /api/ai/space/nodes/human_task/debug-run        # 模拟挂起 + 自动以模拟身份提交
```

通知卡片回调（IM 渠道）走 `/api/ai/space/tasks/{token}/complete` 同一端点（带 `X-Channel: feishu` 头部）。

### 22.12 工作量与里程碑落点

合计 **3 人周**（后端 ~1.8w + 前端 ~1.2w），按依赖关系拆到：

- **M2.7 Human-Task 引擎（1.5w，新增）**——LangGraph Checkpointer 集成 + PauseSignal + ResumeJob + Tasklist API + Notifier 抽象（先 inapp + email）。**前置 M2 完成**。
- **M3.7 Human-Task UI（1w，新增）**——Studio Form Builder + Tasklist 页 + json-schema-form。**前置 M3 完成**。
- **M9 IM 渠道**额外 **+0.5w** 把通知卡片按钮回调对接到 Tasklist。

### 22.13 清单增量（追加到 §27）

- [ ] B/ migration `0008_ai_app_human_task`：`ai_app_task` + `ai_app_task_event` 表
- [ ] B/ LangGraph `PostgresSaver` 接入；StateGraph compile 时注入 checkpointer
- [ ] B/ `runtime/nodes/human_task.py`：raise PauseSignal；executor 捕获并写 checkpoint
- [ ] B/ `tasks_routes.py`：list / claim / reassign / complete / cancel
- [ ] B/ `notify/`：`Notifier` 抽象 + inapp + email 实现 + 模板渲染
- [ ] B/ Assignee resolver（static / expression / role / requester；`dynamic_lookup` 二期）
- [ ] B/ Resume worker：`ingest_queue` 消费 ResumeJob → restore checkpoint → 继续执行
- [ ] B/ `GET /runs/{id}/stream` SSE 重连 + backlog（复用 JobManager）
- [ ] B/ Deadline / Reminder 调度器（用现有调度组件或简单 PG `next_fire_at` 轮询）
- [ ] B/ 任务级鉴权 + 唯一性提交（行锁）
- [ ] F/ `packages/ui/src/json-schema-form/`：基于 @rjsf/core，对齐设计 token
- [ ] F/ Studio Human-Task 节点配置面板（Form Builder + Assignee + Notify + Deadline）
- [ ] F/ Tasklist 页面（4 Tab + 实时刷新 + 表单详情）
- [ ] F/ 全局通知中心 / OS 原生通知（Tauri）
- [ ] F/ Runtime 页"流程暂停中，等待 xxx 提交"占位 + 自动续连
- [ ] F/ DebugPanel 支持 human_task：模拟身份 + 模拟提交
- [ ] X/ M9 IM 卡片回调路由到 `/tasks/{token}/complete`
- [ ] X/ e2e：A 触发 → B 收通知 → 提交 → A 看到结果；超时 fallback；reassign；cancel

---

## 23. 导入 / 导出 / 跨环境迁移

### 23.1 DSL 导出

- `Studio → 更多 → 导出`：下载 `app-<slug>-<version>.json`，结构：

```jsonc
{
  "manifest": { "schema": "ai-app/v1", "exported_at": "...", "source_host": "..." },
  "app":      { "name": "...", "type": "...", "icon": "...", "description": "..." },
  "version":  { "version": "v1.2.0", "persona": {...}, "variables": [...], "flow_dsl": {...} },
  "grants":   { "kb": [{ "ref": "doc:law_kb", "display_name": "法规库" }],
                "tools": [...], "mcp": [...], "office": [...] },
  "tests":    [ /* 测试用例（不含敏感答案） */ ],
  "secrets":  []     // 空数组：vault 引用按 key 名导出，值不导出
}
```

### 23.2 跨环境迁移

- Dev/Staging/Prod 三套部署，导入时弹 **Resource Mapping** 抽屉：
  - 列出 grants 中所有外部资源 ID；
  - 在目标环境查名同名资源，自动建议映射；
  - 缺失资源标红、必须替换或撤销引用才能完成导入。
- 导入 = 创建草稿，不直接 publish；用户 review 后发布。

### 23.3 模板沉淀

- 管理员后台可把任意 public app 标记为 `template`；
- 模板有 `category / tags / preview_image`；
- 创建向导 §16.3 Step 3 的来源就是模板池。

---

## 24. 工作量修订（覆盖创建 / 调试 / 分享 / 人机协同 细节）

把 §11 的里程碑细化更新（**新增项加粗**）：

| 里程碑 | 内容 | 工程量 | 备注 |
| --- | --- | --- | --- |
| M0 蓝图 | ADR + DSL schema | 0.5w | 不变 |
| M1 数据层 + CRUD + Gallery | App / Version / Key 表与基础 UI | 2w | 不变 |
| **M1.5 创建向导 + 模板 seed** | 4 类型 seed DSL + 三步向导 + Fork 资源占位策略 | **1w** | 新增 |
| M2 Flow 引擎 v1 | 编译器 + 6 节点 + `/run` SSE | 2.5w | 不变 |
| **M2.5 节点级调试器** | 单节点 Run + Tool/MCP/HTTP/Code 调试器（独立组件） | **1.5w** | 新增；可与 M3 部分并行 |
| **M2.7 Human-Task 引擎** | LangGraph Checkpointer + PauseSignal + Tasklist API + Notifier(inapp+email) + Resume worker（详见 §22） | **1.5w** | 新增；前置 M2 |
| M3 画布 UI | reactflow + 6 节点视图 + 变量面板 + 类型推断 | 2.5w | 原 2w → 2.5w（含变量 / 类型 UX） |
| **M3.5 Flow Debug Panel** | 时间线 / Watch / 重放 / 单步 + 断点 + Mock 模式 | **1.5w** | 新增 |
| **M3.7 Human-Task UI** | Studio Form Builder + Tasklist 页 + json-schema-form + Runtime 暂停占位 | **1w** | 新增；前置 M3 |
| M4 资源授权 + 运行时 | grants 校验 + ChatRuntime / WorkflowRuntime | 1.5w | 不变 |
| M5 应用市场 + 内置模板 | Market 页 + 5 模板 + Fork 流程联动 | 1.5w | 不变 |
| **M5.5 测试集 / Annotation** | TestCase CRUD + 批量跑 + Annotation 入队 | **1.5w** | 新增 |
| **M5.7 生命周期与三态** | 状态机（草稿/私有/团队/公开）+ Stepper UI + 审计 + slug + share-settings | **1.2w** | 新增 |
| M6 发布与 OpenAPI | App Key、限流、`/v1/ai-app/{id}/invoke` + Swagger；走 `Draft → Private` | 1.5w | 不变 |
| **M6.5 分享四通道** | 公开页 + iframe + embed.js + 二维码 + 分享面板 | **1.5w** | 新增 |
| M7 高级节点 + 沙箱 | code/http/loop/subflow/parallel/try-catch + 沙箱 | 2.5w | 原 2w → 2.5w（多了 parallel/try-catch） |
| M8 可观测 + 日志 | 节点 trace + Run Log + Cost report | 1.5w | 不变 |
| **M8.5 协作 v1（乐观锁 + 评论 + 版本 diff）** | Etag 锁 + 节点评论 + DSL JSON diff UI | **1w** | 新增 |
| **M8.7 导入导出 / 资源映射** | 导出包 + 跨环境导入向导 | **0.7w** | 新增 |
| M9 IM 渠道适配（飞书 / 钉钉 / 企微） | webhook 接入 + 消息卡片渲染 + 用户身份映射 + Human-Task 卡片回调 | 2w | 含 +0.5w 把通知卡片回调对接到 Tasklist；不做计费 |

**新合计**：

| 范围 | 工程量 |
| --- | --- |
| 核心可上线（M0–M6.5：含创建 / 调试 / 测试 / 生命周期 / 人机协同 / 分享） | **21.2 人周** |
| 含高级编排 + 沙箱（到 M7） | **23.7 人周** |
| 含协作 + 导入导出（到 M8.7） | **26.7 人周** |
| 含 IM 渠道（到 M9） | **28.7 人周** |

**两人并行**（前端 / 后端）压缩到自然时间：到 M6.5 ≈ **13 周**；全功能 ≈ **17–18 周**。

**三人并行**（前端 / 后端 / 调试 + 人机协同 专项）：到 M6.5 ≈ **10 周**。

---

## 25. 关键 UX 速查表（一眼看懂入口）

| 场景 | 入口 | 后端调用 |
| --- | --- | --- |
| 创建空白应用 | Gallery → 创建应用 → 选类型 | `POST /apps` + 写 seed DSL |
| 从模板创建 | Market → 卡片"使用此模板" | `POST /apps?from_template=...` |
| Fork 别人的 | 公开 App 详情 → 复制为我的 | `POST /apps?fork_from=...` |
| 调试单节点 | Studio 画布 → 节点 ▶ 按钮 | `POST /nodes/{type}/run` |
| 调试整个 Flow | Studio → 调试运行 | `POST /apps/{id}/debug-run` (SSE) |
| 重放历史 run | DebugPanel → 历史 → 重放 | `POST /apps/{id}/runs/{run_id}/replay` |
| 创建测试用例 | DebugPanel → "存为测试用例" | `POST /apps/{id}/tests` |
| 跑回归 | Studio → 测试 Tab → 全部运行 | `POST /apps/{id}/tests/run` |
| 发布版本 | Studio → 发布 → 版本号 + changelog | `POST /apps/{id}/versions/{vid}/publish` |
| 生成 API Key | 发布面板 → 创建 Key | `POST /apps/{id}/keys` |
| 嵌入网站 | 发布面板 → 嵌入 → 复制 script | 静态 `embed.js` 拉 publishable key |
| 跨环境迁移 | Studio → 导出 / 目标环境 → 导入 | `GET /apps/{id}/export`、`POST /apps/import` |
| 多人冲突 | 保存草稿 409 → 比较 / 覆盖 | If-Match etag |

---

## 26. 一图总结

```
现有：    KB | Tool | MCP | Office | Models  ──┐
                                              ▼
新增：              AI App ◄── Flow DSL ──► Runtime (LangGraph + Checkpointer)
                       │                       │
                       ├─ Versions              ├─ Nodes (6 → 10+)
                       ├─ Grants (RBAC)         ├─ Sandbox / HTTP allowlist
                       ├─ Keys (ak/sk)          ├─ Trace / Quota
                       ├─ Publish               └─ Pause/Resume (Human Task)
                       │   └─► /v1/ai-app/{id}/invoke (外部系统)
                       └─ Human Tasks ◄── Tasklist + 通知 fan-out
                                          (inapp / 飞书 / 钉钉 / 企微 / 邮件)
```

> **一句话总结**：察元的资产已是 Coze / Dify 同档，缺的是把它们"装订成应用"的那本封皮——`AI App + Flow + Runtime + Lifecycle(草稿/私有/公开) + Human-in-the-Loop`。私有化场景剔除计费心智，按本计划走，**核心可上线 ≈ 21.2 人周（2 人并行 13 周）**，关键风险点收敛在 M2（编排引擎）、M2.7（人机协同 / 流程挂起）、M5.7（生命周期 / 鉴权矩阵）与 M7（沙箱），其余皆为聚合与 UI 工作。

---

## 27. 开发计划清单（可直接拆票）

> 每条 = 一个可独立提 PR 的工作项；前缀 `B/` = 后端，`F/` = 前端，`X/` = 跨端 / 文档 / 联调。复选框留空，跟随实施进度勾选。

### M0 蓝图（0.5w）
- [ ] X/ ADR-0007：Flow DSL v1（节点白名单 + 兼容策略 + 表达式语法子集）
- [ ] X/ `docs/contracts.md` 增 §9 AI Space，列出全部端点
- [ ] X/ 看板 epic `AI-SPACE-M1..M9` 建立，按本清单拆票

### M1 数据层 + CRUD + Gallery（2w）
- [ ] B/ `chayuan/server/ai_space/` 包骨架（models / repository / routes 占位）
- [ ] B/ migration `0005_ai_app`（`ai_app / ai_app_version / ai_app_key` 三表，含 `current_version_id / visibility / discoverable / allow_fork / noindex` 字段）
- [ ] B/ `ai_space_routes.py`：App CRUD + Version CRUD + `?scope=mine|shared|public|all` 过滤（详见 §21.5）
- [ ] B/ 鉴权 dependency：仅 owner 可写；`scope=public` 仅返回 `current_version_id IS NOT NULL AND visibility='public' AND discoverable=true`
- [ ] B/ `deriveAppLifecycle()` 后端等价物（视图层）：`(current_version_id, visibility) → 'draft'|'private'|'team'|'public'`
- [ ] F/ `packages/api/src/aiSpace.ts`：CRUD 封装 + `deriveAppLifecycle()` helper（取代现有 fixture-fallback 接管）
- [ ] F/ `AppGalleryPage.tsx`（替换现有 `SpacePage.tsx`）：三 Tab `我的（含草稿）/ 共享给我 / 市场`
- [ ] F/ `lifecycle-badge` UI 组件（草稿灰 / 私有蓝 / 团队紫 / 公开绿）
- [ ] F/ `AppStudioPage.tsx` 骨架 + 五个 Section 占位 + 顶栏 lifecycle badge
- [ ] X/ 路由 `/space`、`/space/:id/studio`、`/space/:id/runtime` 注册
- [ ] X/ e2e：创建空白应用 → 默认草稿态 → 列表可见 → 删除

### M1.5 创建向导 + 模板 seed（1w）
- [ ] B/ `ai_space/seeds/`：四类型默认 DSL（chatbot / agent / workflow / text_generation）
- [ ] B/ `POST /apps?from_template=<id>` 与 `?fork_from=<id>` 分支：复制 DSL，资源走"引用 + 占位"
- [ ] B/ Fork 资源校验：发布时 grants 必须全有权限
- [ ] F/ 创建向导 Drawer（三步：类型 → 命名+模型 → 模板）
- [ ] F/ Fork 时无权限资源标红 UI
- [ ] X/ 5 个内置模板 seed JSON 入库（客服 / 翻译 / SQL / 图问图 / 邮件助手）

### M2 Flow 引擎 v1（2.5w）
- [ ] B/ `runtime/compiler.py`：DSL → `StateGraph[AppState]` 编译；LRU(32) 缓存
- [ ] B/ 6 节点实现：`input / output / llm / kb_retrieve / tool_call / if`
- [ ] B/ 节点统一接口：`async def run(ctx, params) -> NodeResult`
- [ ] B/ DSL pydantic 校验（环检测 / 未连接 / 变量未声明）
- [ ] B/ `POST /apps/{id}/run` SSE：复用现有 chat SSE 帧形态（meta / token / node_event / done / error）
- [ ] B/ 节点级超时（LLM 30s / 其他 10s）+ 全局 step 上限 200
- [ ] B/ 编译版本失效：发布或删除 version 时 invalidate
- [ ] X/ 单测：DSL 校验、编译器、6 节点 happy/error 路径

### M2.5 节点级调试器（1.5w）
- [ ] B/ `POST /api/ai/space/nodes/{node_type}/run`（绕过 Flow，单节点跑）
- [ ] B/ `vault://` 占位符：调试时显示掩码、运行时注入真值
- [ ] F/ `packages/ui/src/tool-debugger/`：通用单节点调试组件
- [ ] F/ 节点右上角 ▶ 按钮 + RHS 抽屉
- [ ] F/ KB / HTTP / Code / Tool / MCP 各自的调试视图
- [ ] F/ Tool 平台页 (`/tools`) 复用同一调试器
- [ ] F/ 调试历史：localStorage 最近 20 次

### M2.7 Human-Task 引擎（1.5w，新增；前置 M2，详见 §22）
- [ ] B/ migration `0008_ai_app_human_task`：`ai_app_task` + `ai_app_task_event` 表
- [ ] B/ LangGraph `PostgresSaver` 接入；StateGraph compile 注入 checkpointer
- [ ] B/ `runtime/nodes/human_task.py`：raise PauseSignal；executor 捕获写 checkpoint
- [ ] B/ Assignee resolver（static / expression / role / requester）
- [ ] B/ `tasks_routes.py`：list / claim / reassign / complete / cancel（行锁 + 唯一性）
- [ ] B/ `notify/`：`Notifier` 抽象 + inapp + email；模板渲染（支持 redact 过滤器）
- [ ] B/ Resume worker：`ingest_queue` 消费 ResumeJob → restore checkpoint → 续跑
- [ ] B/ `GET /runs/{id}/stream` SSE 重连 + backlog（复用 JobManager）
- [ ] B/ Deadline / Reminder 调度器（PG `next_fire_at` 轮询）
- [ ] X/ e2e：A 触发 → B 收通知 → 提交 → A 看到结果；超时 fallback；reassign；cancel

### M3 画布 UI（2.5w）
- [ ] F/ 引入 `reactflow` + 设计 token 适配
- [ ] F/ 6 节点视图组件 + NodePalette 拖拽
- [ ] F/ 连线类型校验（不匹配变红 + 提示）
- [ ] F/ `useFlowEditor.ts`：state + 100 步 undo/redo + 复制粘贴
- [ ] F/ 自动布局（dagre）+ 小地图 + 缩放 + 网格吸附
- [ ] F/ 变量面板（state JSON 树 + 悬停插入）
- [ ] F/ 输出 schema 推断 + 引用错变量画布告警
- [ ] F/ 保存草稿（Etag 乐观锁占位，409 提示先走基础提示，多人协作放 M8.5）

### M3.5 Flow Debug Panel（1.5w）
- [ ] B/ `POST /apps/{id}/debug-run`：与 `/run` 同形，但允许指定 `breakpoints / step_mode / mock_overrides`
- [ ] B/ 暂停 / 继续 / 单步：服务端暂停态 + `POST /runs/{id}/resume`
- [ ] F/ DebugPanel 时间线（按 SSE 节点事件展开）
- [ ] F/ 变量 Watch（实时 JSON diff 高亮）
- [ ] F/ 断点（画布右键 → DSL `breakpoint:true`）+ 单步
- [ ] F/ Mock 模式（自动捕获上次成功 run 作为回放源）
- [ ] F/ A/B 对比并排面板（两 version × 同 inputs）

### M3.7 Human-Task UI（1w，新增；前置 M3，详见 §22）
- [ ] F/ `packages/ui/src/json-schema-form/`：基于 @rjsf/core，对齐设计 token；File widget 接 FileStorage
- [ ] F/ Studio Human-Task 节点配置面板：Form Builder（拖拽字段）+ Assignee 选择器 + Notify + Deadline / Reminder
- [ ] F/ Tasklist 页面：4 Tab（待我处理 / 我发起 / 组待认领 / 已完成）+ 实时刷新 + 表单详情
- [ ] F/ 全局通知中心 + OS 原生通知（Tauri `notification` permission）
- [ ] F/ Runtime 页"流程暂停中，等待 xxx 提交"占位 + SSE 自动续连
- [ ] F/ DebugPanel 支持 human_task：模拟身份 + 模拟提交（不真发通知）

### M4 资源授权 + 运行时打通（1.5w）
- [ ] B/ `rbac.py`：grants 校验中间件（保存 + 运行双重）
- [ ] B/ `POST /apps/{id}/grants` / `DELETE /apps/{id}/grants/{type}/{rid}`
- [ ] F/ Studio Context / Tools Section：选 KB / Tool / MCP / Office（复用 CapabilityCards）
- [ ] F/ `AppRuntimePage`：chatbot/agent → `ChatRuntime`（包 ConversationView），workflow → `WorkflowRuntime`（表单 + SSE）
- [ ] X/ e2e：跨用户授权拒绝、grants 撤回后运行失败

### M5 应用市场（1.5w）
- [ ] B/ Market 列表过滤：`visibility=public AND status=approved`
- [ ] B/ 提交审核字段（管理员通过 / 拒绝）
- [ ] F/ MarketPage：分类 SegmentedTabs + 卡片网格
- [ ] F/ "使用此模板 / Fork 为我的" 入口接 §M1.5
- [ ] X/ 5 模板 seed 提交流水跑通

### M5.5 测试集 + Annotation（1.5w）
- [ ] B/ `AIAppTestCase / AIAppTestRun` 表 + migration `0006_ai_app_eval`
- [ ] B/ `POST /apps/{id}/tests` / `POST /apps/{id}/tests/run`（批量）
- [ ] B/ 评估 hook：复用 `eval/` 子模块的评分体系
- [ ] B/ Annotation 入队：runtime 页"标注"按钮 → 队列 → 转用例
- [ ] F/ Studio Test Tab：列表 + 批量运行 + 报告（pass/fail）
- [ ] F/ Annotation review 队列页

### M5.7 生命周期与三态（1.2w，新增；插在 M5.5 与 M6 之间）
- [ ] B/ migration `0007_ai_app_lifecycle`：`ai_app_share_settings` + `ai_app_audit_log` 表；`ai_app` 增 `discoverable / allow_fork / noindex`
- [ ] B/ `lifecycle.py` 状态机：迁移守护（DSL 校验、grants 全权限、测试集 pass）+ 副作用清单（§21.7）
- [ ] B/ 路由：`make-private / make-team / make-public / unpublish` + `audit-log` 查询
- [ ] B/ 路由级 `(visibility, current_version_id, caller) → reason enum` 中间件
- [ ] B/ Public→Private 保留 slug；删除走软删 30 天
- [ ] B/ Audit hook：所有迁移落 `ai_app_audit_log`
- [ ] F/ PublishDialog 升级为四态阶梯 Stepper（草稿 → 私有 → 团队 → 公开），可前进 / 后退
- [ ] F/ Confirm 弹窗：Public→Private 提示"将下市场" / Private→Public 提示"将进入审核"
- [ ] F/ Runtime 页 banner："这是 owner 共享给你的私有应用"
- [ ] F/ Studio 顶栏拒绝原因 enum 中文映射
- [ ] X/ e2e：8 条主迁移路径 + 3 条非法迁移被拒

### M6 发布 + OpenAPI（1.5w）
- [ ] B/ `POST /apps/{id}/versions/{vid}/publish`：写 `current_version_id`，触发 lifecycle `Draft → Private`（首次）
- [ ] B/ `AIAppKey` CRUD + 中间件（`Authorization: Bearer ak_...` 解出 app_id）
- [ ] B/ `POST /api/v1/ai-app/{id}/invoke`（OpenAI 兼容 SSE）；publishable_key 仅 chat:invoke
- [ ] B/ `/apps/{id}/openapi.json` 自动生成
- [ ] B/ 限流：`qps_per_key + daily_tokens_per_key + max_concurrent_runs_per_user`，默认放开
- [ ] B/ 测试集 fail 阻断 publish（可强制覆盖记录原因）
- [ ] B/ 已发布 grants = 值快照（资源 ID 写入 `version.resource_grants`，运行时不再校验上游权限）
- [ ] F/ PublishDialog：版本号 + changelog + 资源校验提示
- [ ] F/ Key 管理：创建 / 列表 / 撤销 / 一次性显示原文

### M6.5 分享四通道（1.5w）
- [ ] B/ 公开页路由 `/ai-app/{slug}`（lifecycle != public 返回 404；按 share_settings 处理 require_login / access_code / expires_at）
- [ ] B/ `embed.js` 静态产物（< 30KB），仅在 lifecycle=public + publishable_key + referer 命中 allowlist 时返回 200
- [ ] B/ `PUT /apps/{id}/share-settings`（仅 team/public 可写）
- [ ] F/ 公开 Web Runtime 页（按 require_login 决定免登录 / 跳登录回跳）
- [ ] F/ 嵌入代码生成（iframe + 浮动球 script）
- [ ] F/ 二维码 + 短链
- [ ] F/ PublishDialog 增"分享"区（仅 public 时启用对应 tab）
- [ ] X/ 公网部署形态可选；私有内网默认 `require_login=true`

### M7 高级节点 + 沙箱（2.5w）
- [ ] B/ 新节点：`switch / loop(上限 50) / parallel(显式 fan-out + join) / try-catch / subflow`
- [ ] B/ `code` 节点：进程隔离 + seccomp + RestrictedPython + `resource.setrlimit`；管理员开关
- [ ] B/ `http` 节点：出网域名 allowlist（管理员配置）+ SSRF 防护
- [ ] B/ 节点 retry / timeout / fallback 通用语义
- [ ] B/ 表达式 mini-语言（白名单运算符）+ 单测
- [ ] F/ 5 类新节点的画布视图 + 调试视图
- [ ] X/ 安全审计 checklist 走完（沙箱 / SSRF / RCE）

### M8 可观测 + 运行日志（1.5w）
- [ ] B/ 每节点一个 OTel span，`trace_id = run_id`
- [ ] B/ `AIAppRunLog` 持久化 + `GET /apps/{id}/runs?from=...`
- [ ] B/ Trace blob 入 `FileStorage`，按 owner 自动清理（保留期可配）
- [ ] F/ Run 历史列表 + Trace 详情（节点级时间线复用 DebugPanel）
- [ ] F/ 容量看板：tokens / latency / error rate（**不展示金额**）

### M8.5 协作 + 版本 diff（1w）
- [ ] B/ Etag 乐观锁：`If-Match` 不一致返回 409 + 服务端最新 DSL
- [ ] B/ 草稿心跳 `PUT /apps/{id}/draft/heartbeat`（30s 一次）
- [ ] F/ Studio 顶部"当前编辑者"展示
- [ ] F/ 409 冲突 dialog（覆盖 / 放弃 / 比较 diff）
- [ ] F/ DSL JSON diff 视图（`json-diff` + 着色）
- [ ] F/ 节点级评论锚点 + 侧栏列表

### M8.7 导入导出 / 跨环境迁移（0.7w）
- [ ] B/ `GET /apps/{id}/export` 输出 `manifest + app + version + grants(ref) + tests`
- [ ] B/ `POST /apps/import` 接收包 + 返回 `resource_mapping_required`
- [ ] B/ secrets 导出仅留 vault key 名，不导值
- [ ] F/ Studio "导出 / 导入" 入口
- [ ] F/ 资源映射抽屉（缺失资源标红、强制替换）

### M9 IM 渠道适配（2w，私有化优先；含 Human-Task 卡片回调）
- [ ] B/ 飞书 / 钉钉 / 企业微信 webhook 接入框架（统一 `Channel` 抽象，与 M2.7 `Notifier` 共用）
- [ ] B/ 用户身份映射（IM uid ↔ 平台 user）
- [ ] B/ 消息卡片渲染（按渠道 Schema）；Human-Task 卡片含"打开任务 / 一键审批"按钮
- [ ] B/ 卡片按钮回调路由到 `/tasks/{token}/complete`（含 `X-Channel: feishu` 头）
- [ ] F/ 渠道配置页（每个 App 可绑多个渠道）
- [ ] X/ 内网部署文档：穿透 / 反向代理建议

### 横切（贯穿全期，不计入里程碑总时）
- [ ] X/ i18n：所有新增字符串入 `packages/i18n`
- [ ] X/ Storybook：新增节点 / 调试组件至少 2 条 story
- [ ] X/ Biome / TS strict 通过
- [ ] X/ Tauri / Web 双端冒烟（特别是 SSE / 文件操作）
- [ ] X/ 私有化部署文档：离线模型接入 / vault 配置 / `embed.js` 内网域名替换

### 工程量统计

| 范围 | 工程量 | 自然时间（2 人并行） |
| --- | --- | --- |
| MVP（M0 + M1 + M1.5 + M2 + M3 + M4 + 简化版 Runtime） | 10w | 5–6 周 |
| 核心可上线（到 M6.5，含创建 / 调试 / 测试 / 生命周期 / 人机协同 / 分享） | 21.2w | 13 周 |
| 含沙箱（到 M7） | 23.7w | 14–15 周 |
| 含协作 + 迁移（到 M8.7） | 26.7w | 16–17 周 |
| 全功能（到 M9） | 28.7w | 17–18 周 |

---

## 28. 收口建议（非技术）

1. **MVP 先发**：M0–M4 + 简化分享（仅 OpenAPI Key，不做 embed.js / 二维码）→ 让客户先用上 chatbot / 简单 workflow，反馈再投入 M5–M7。
2. **沙箱上线节奏**：M7 不阻塞 MVP；`code` 节点先以"管理员开关 + 仅特权角色可见"灰度。
3. **IM 渠道按客户拉单**：飞书 / 钉钉 / 企微三选一先做，看主力客户。
4. **不做的事（再次确认）**：计费 / 月度账单 / 跨租户结算 / 公网 SaaS 嵌入分发 / 任意代码运行（必须沙箱）。
5. **能复用的就不重写**：每个 PR 检视是否能直接挪用已有组件 / 路由（KB grants / FileStorage / model_platform / observability / resilience）。
