# ADR-0007: AI Space —— Flow DSL v1 与运行时模型

- 状态: Accepted（2026-04-28）
- 关联文档: `docs/plans/ai-space-orchestration.md`、`docs/plans/ai-space-ui-design.md`

## Context

为了对标 Coze / Dify 的应用编排能力，我们要把已有的 KB / Tool / MCP / Office / Model-Platform 资产**装订成"AI App"** —— 一个可编排、可发布、可分享、可承载人机协同的产物。三个新增物：**App 实体 / Flow DSL / Runtime**。本 ADR 锁定 DSL v1 的语义边界，避免后续 M2-M7 节点扩展无序。

## Decision

### 1. App 与编辑/可见两维度

- App 内部维度 = `(current_version_id, visibility)` 推导出**前端四态**：草稿 / 私有 / 团队 / 公开。
- 不再保留单独的 `lifecycle_status` 字段；视图层 `deriveAppLifecycle()` 计算。
- App 类型枚举：`chatbot / agent / workflow / text_generation`，决定默认 Flow 与运行页形态。

### 2. Flow DSL（JSON）—— v1 形态

```jsonc
{
  "version": "1",
  "entry":   "<node_id>",
  "state":   { /* 全局变量初值 */ },
  "nodes":   [ { "id": "...", "type": "...", "params": {...},
                 "outputs": {...}, "next": "...", "branches": {...},
                 "retry": {...}, "timeout": 30000, "fallback": {...},
                 "breakpoint": false } ],
  "metadata": { "viewport": {...}, "comments": [...] }
}
```

**约束**:

- DSL 是值对象，不嵌套引用；`kb_ids / tool_ids` 等用业务 ID（`doc:law_kb`、`tool:search_engine`）。
- 表达式语法子集（mini-lang）：`==/!=/>=/<=/>/</&&/\|\|/in/length/!`，禁用任意函数调用。
- 模板字符串：`{{ inputs.x }}` `{{ state.x }}` `{{ persona.x }}` `{{ env.x }}` 四层；可叠 `| filter`（仅 `redact / json / upper / lower` 白名单）。
- 编译期校验：环检测、未连接节点、未声明变量、类型不匹配；保存草稿可放过校验，发布必须通过。

### 3. 节点白名单（v1 收敛 6 + 1 节点；M7 扩展高级节点）

| 节点 | M | 作用 |
| --- | --- | --- |
| `input` | M2 | 流程入口，绑定 `inputs schema` |
| `output` | M2 | 流程出口，写最终结果 |
| `llm` | M2 | 调用 LLM（OpenAI 兼容） |
| `kb_retrieve` | M2 | 知识库召回（KU 接口） |
| `tool_call` | M2 | 调用 Tool / MCP（同接口） |
| `if` | M2 | 单条件二分支 |
| `human_task` | M2.7 | 人机协同节点（暂停 + 通知 + Tasklist） |
| `switch` | M7 | 多分支 |
| `loop` | M7 | for-each / while（默认上限 50） |
| `parallel` | M7 | 显式 fan-out + join |
| `try / catch` | M7 | 错误捕获边 |
| `subflow` | M7 | 调用其他 App |
| `code` | M7 | RestrictedPython 沙箱（管理员开关） |
| `http` | M7 | 出网域名 allowlist |
| `office_op` | M7 | Office 文档生成 / 读写 |

**任何不在白名单的 `type` 在编译期直接拒绝**，避免任意反射执行。

### 4. 运行时

- 编译器：DSL → LangGraph `StateGraph[AppState]`；LRU(32) 缓存编译结果，key = `(app_id, version_id)`。
- 执行：`async def run(...) -> AsyncIterator[Event]`，事件类型：`meta / node_start / node_end / token / tool_call / human_pause / log / error / done`，与 `/chat/v2/chat` SSE 帧形态保持 family。
- 持久化挂起：`human_task` raise `PauseSignal(task_token)` → executor 用 LangGraph PostgresSaver checkpoint。重连走 `GET /runs/{run_id}/stream` + backlog（与 KB 远端同步同款）。
- 并发：DAG 拓扑期无依赖节点 `asyncio.gather` 并行；节点超时默认 LLM 30s / Tool 10s / HTTP 30s / Code 10s；全局 step 上限 200。
- 限流（私有化默认放开）：`qps_per_key + daily_tokens_per_key + max_concurrent_runs_per_user`。

### 5. 兼容策略

- DSL 顶层 `version: "1"`；未来不兼容时升 `"2"`，老版本入库者运行时仍按 v1 编译。
- 节点新增字段必须默认值兼容旧 DSL（`retry/timeout/fallback/breakpoint` 全可选）。
- 老 `/chat/v2/chat` 路由保留，长期可视作 `app_id = "default-chatbot"` 的隐式 App。

### 6. 不做（明确边界）

- **不做计费 / token 账单 / 跨租户结算**（私有化场景剔除 SaaS 心智）。
- **不做任意代码运行**：`code` 节点必须沙箱 + 管理员显式开启。
- **不做开放运行时反射**：节点扩展必须改后端代码 + 走代码评审，不接受 DSL 内嵌动态加载逻辑。
- **不引入新执行引擎**：复用 LangGraph，绝不自研 DAG runner。

## Consequences

- 节点边界清晰，前后端可并行：前端按 `type → 配置面板` 表驱动；后端按 `type → compile_<type>()` 工厂。
- v1 一旦发布即冻结语义；后续节点扩展走 ADR-0008 / 0009...
- 风险点：DSL 表达式 mini-lang 必须严格白名单；任何"为方便"打开都可能引发 RCE。
