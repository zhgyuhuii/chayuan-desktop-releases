# AI Space —— 下一阶段计划（M10 起）

> 编写时间：2026-04-28  
> 编写背景：M0–M9 主体已落地；本周新增 **ChatRuntime starters + 对话持久化**。  
> 上游计划：[`ai-space-orchestration.md` §27](./ai-space-orchestration.md)、[`ai-space-ui-design.md`](./ai-space-ui-design.md)。  
> 本文档目的：给"已经把 v1 大盘走完一遍"的 AI Space 排下一程的优先级 —— 既补上原计划没做完的洞，也把现有技术栈能立刻吃掉的红利列出来。

---

## 0. 新近完成（2026-04 末第十轮）

| 项目 | 位置 | 说明 |
| --- | --- | --- |
| ChatRuntime 推荐问题 (starters) | `packages/app/src/features/space/ChatRuntime.tsx` `+` `PromptSection.tsx` | 从 `version.persona.starters: string[]` 拉，欢迎卡片下显示快捷启动；Studio 提示词 tab 有专门编辑器（最多 5 条）。无 schema 迁移（persona 是 JSON 列）。 |
| ChatRuntime 对话持久化 | 同上 | localStorage `cy:ai-chat-runtime:<appId>` 存最近 200 条；流式期间不写；新对话按钮清空。 |

→ **本轮没动 schema、没碰后端、没引新依赖**。

---

## 1. 计划中已落地 / 还差的

下表把上游 §27 清单按"实际代码状态"重新打勾。✅ = 已可用、🟡 = 半成品 / 简化、❌ = 未落地。

| 里程碑 | 关键工作项 | 状态 | 备注 / 缺口 |
| --- | --- | --- | --- |
| M0 蓝图 / ADR | DSL v1 文档 | ✅ | |
| M1 数据 + CRUD + Gallery | App / Version / Key 表 + 路由 + 三 Tab Gallery | ✅ | `ai_space_routes.py` 42 条路由全在 |
| M1.5 创建向导 + 模板 | 5 个内置模板 seed | ✅ | 客服 / 翻译 / SQL / 邮件 / 审批已 seed |
| M2 Flow 引擎 v1 | 6 节点 + SSE + 编译缓存 | ✅ | 实际节点数 = **13**（多于计划） |
| M2.5 节点级调试器 | `POST /nodes/{type}/run` + Drawer | ✅ | `vault://` 占位符 ❌ 未做 |
| M2.7 Human-Task 引擎 | task 表 + 路由 + Resume | 🟡 | **未用 LangGraph PostgresSaver checkpointer**；Resume 走自研 `state_blob` + `current_node_id` 续跑（实际等价，但偏离 ADR） |
| M3 画布 UI | reactflow + 节点 + 拖拽 + DSL 校验 | ✅ | `flowValidate.ts` 自研，未引 zod |
| M3.5 Flow Debug Panel | 时间线 + 断点真断停 + Resume + Variables Watch | ✅ | **单步模式（step over）❌**、**Mock 模式（历史回放）❌**、**A/B 对比并排 ❌** |
| M3.7 Human-Task UI | Tasklist + 表单 + 文件上传 | ✅ | OS 原生通知 (Tauri) ❌ 未接 |
| M4 资源授权 + Runtime | grants + ChatRuntime + WorkflowRuntime | ✅ | |
| M5 应用市场 | MarketPage + Fork | ✅ | **管理员审核流（pending_review / approved / rejected）❌ 未做**——目前 public 直接进市场 |
| M5.5 测试集 + Annotation | TestSuiteSection + AnnotateDialog | ✅ | **测试 fail 阻断 publish ❌**；评估器只有 JSON 严格匹配 |
| M5.7 生命周期 + 三态 | 显式 lifecycle 路由 + audit_log | ✅ | |
| M6 发布 + OpenAPI | API Key + OpenAI 兼容 invoke + openapi.json | ✅ | **限流 (qps_per_key / daily_tokens / max_concurrent) ❌** —— `rate_limit` 字段已落表但中间件没接 |
| M6.5 分享四通道 | 公开页 + embed.js + access_code | ✅ | **二维码 + 短链 ❌**；access_code 后端校验有，前端入口未拉通 |
| M7 高级节点 + 沙箱 | switch / loop / parallel / try_catch / subflow / http | ✅ | **`code` 节点（沙箱执行）❌**；http 已有出网 allowlist |
| M8 可观测 + 运行日志 | Run 历史 + Cancel + Replay | 🟡 | **OTel span / trace_id 注入 ❌**；**容量看板（tokens / latency / error rate）❌** |
| M8.5 协作 + 版本 diff | Etag 冲突 dialog | 🟡 | **草稿心跳 ❌**、**当前编辑者 presence ❌**、**JSON diff 视图 ❌**（只有占位）、**节点级评论 ❌** |
| M8.7 导入 / 导出 | export + import dry-run + force | ✅ | 资源映射 UI 已有 |
| M9 IM 渠道适配 | 飞书 / 钉钉 / 企微 webhook | ❌ | 完全未落地 |

---

## 2. 下阶段优先级建议（深度分析）

把"未落地 / 半成品 + 现技术栈红利"按 **价值 ÷ 成本** 重排，得到下表。每条都给「为什么现在做」和「靠什么现成能力做」。

### 🔴 P0：立刻能做 + 用户感知最强（≤ 1 周/项）

#### P0-1 ChatRuntime 富文本渲染（Markdown / 代码块 / 引用）
- **现状**：`ChatRuntime.tsx:367` 直接渲染 `m.content` 纯文本。LLM 输出大量 markdown / 代码块 / 引用编号都是平文展示，体感极差。
- **靠现有能力**：`packages/app/package.json` 已经引入 `marked@^15` + `dompurify@^3`（KB 对话/Office 都在用）。
- **要做**：抽 `<MessageMarkdown>` 子组件（已有可参考 `features/chat/MessageBubble.tsx`）；接入代码块复制按钮；引用 `[出处 N]` 标记和 KB 那边对齐。
- **额外收益**：`PublicAppPage` / `WorkflowRuntime` 同样能复用。

#### P0-2 ChatRuntime / RunPanel 输出操作集（复制 / 重答 / 导出）
- **现状**：每条 assistant 气泡只有 hover 显示「标注」按钮。
- **要做**：
  - 复制内容（Clipboard API）
  - 重答（重发上一次 user 输入；abort 当前流后再 send）
  - 「导出对话」（JSON / Markdown）—— 复用持久化的 `PersistedMessage`
- **成本**：< 0.5 周；纯前端。

#### P0-3 LLM 节点提示词预览（变量插值即时回显）
- **现状**：`NodeConfigPanel` LLM 节点只有 textarea；`{{state.x}}` 写错了运行时才报错。
- **要做**：在 `NodeConfigPanel` 加只读"展开预览"折叠区，使用当前 `state` 默认值 + `persona` 做一次 `{{x}}` 替换演示；变量未声明红色高亮。
- **靠现有能力**：`flow/flowValidate.ts` 已经枚举 state schema；`packages/app` 可直接在前端做 mustache 演示。
- **价值**：减少 50% 的"Oops 写错变量名"反复调试。

#### P0-4 模板 Marketplace「试用按钮」（无须 fork 即可玩）
- **现状**：MarketPage 上模板必须 Fork → 打开 Studio → 切到 runtime 才能试。门槛高，发现成本高。
- **要做**：模板卡片新增「试一试」→ 弹出 modal 内嵌 `ChatRuntime`，用模板 DSL 临时跑一个无副作用的 ephemeral run（后端可以接受 `runApp({ inline_dsl })` 或者临时 app id）。
- **靠现有能力**：`POST /apps/{id}/runs` 已是 SSE；只需新增 `POST /apps/_preview/runs?template_id=xxx` 路由。
- **价值**：是降低市场转化最大的一步。

#### P0-5 Variables 提示器 / Mention 选择器
- **现状**：写 `{{state.xxx}}` 是手敲；没有任何 IDE 体验。
- **要做**：textarea 内输入 `{{` 触发 popover，从 `flowValidate.ts` 的 `inferStateSchema()` 列出可选路径；选中插入。
- **靠现有能力**：`VariablesPanel.tsx` 已经能展开整个 state 树，复用其 `path` 收集逻辑。
- **价值**：把现有"事后红框报错"提升为"事前不犯错"。

---

### 🟠 P1：补完 v1 计划的明显缺口（1–2 周/项）

#### P1-1 `code` 沙箱节点（M7 唯一缺项）
- **缺失原因**：M7 工作量 2.5w 中沙箱占 1w；当时为节奏让步推迟。
- **方案选择**：
  1. **进程隔离 + RestrictedPython**（ADR-0007 推荐）—— 可立即做但 Python-only；
  2. **WASI / Pyodide in-worker**（前端跑）—— 安全但 fork 成本高；
  3. **容器单次任务 (Firecracker / Nsjail)** —— 强隔离但运维成本高。
- **建议**：先走 1，环境变量 `CHAYUAN_AI_SPACE_CODE_ENABLED=false` 默认关；admin 显式开启。把 `node_code` 加到 `runtime/nodes.py`，Palette 加一格灰色「需管理员开启」。
- **风险**：Sandbox CVE 是高敏，必须有审计 checklist + 测试用例。

#### P1-2 限流中间件（QPS / 每日 token / 并发 run）
- **现状**：`AIAppKey.rate_limit` 字段已有 JSON 列，但运行时没读。
- **方案**：
  - 复用现有 Redis（如部署有）做令牌桶；私有化无 Redis 时用 PG 计数表。
  - 中间件挂在 `/api/v1/ai-app/{id}/invoke`、`/apps/{id}/runs` 两条路径。
  - 命中时返回 `429 Too Many Requests` + `Retry-After`。
- **价值**：私有化场景"防止单 Key 失控"是合规硬要求。

#### P1-3 公开页 access_code + 二维码 + 短链
- **现状**：后端 `share_settings.access_code_hash` / `verify_access_code` 已有；前端公开页未实现"输入 access_code"门槛；`/ai-app/{slug}` 没有 QR 生成。
- **要做**：
  - `PublicAppPage`：检测到 `requires_access_code:true` 时弹一次性输入框，POST 校验 → cookie 短期 token。
  - `ShareDialog`：embed/QR 区新增 `<canvas>` 二维码（用 `qrcode.react` 或动态 import `qrcode`）；生成 `chayuan.cn/s/<slug-hash>` 短链（私有化可省，留位）。
- **成本**：< 1 周；纯接线 + 一个新依赖。

#### P1-4 测试集 fail 阻断 publish + 评估器扩展
- **现状**：`POST /apps/{id}/versions/publish` 不读测试结果；评估只能 JSON 严格匹配。
- **要做**：
  - publish payload 增 `force_with_failing_tests: bool`，false 且 `tests/run` 最近一次失败 ≥ 1 → 拒绝 + 列失败用例。
  - 评估器扩展：`exact_match / contains / regex / json_path / llm_judge`（最后一种走小模型 `chayuan-eval`）。
- **价值**：把测试集从"摆设"升级为"质量门禁"。

#### P1-5 OTel trace + 容量看板
- **方案**：
  - 后端：`runtime/executor.py` 已经能 emit 节点事件；在 `_walk` 进出节点时 wrap `tracer.start_as_current_span(node_id)`，attribute = `app_id / run_id / node_type / token_in / token_out / latency_ms`。
  - `RunsSection.RunDetail` 已经有时间线，扩展为「跨节点 P95 latency / 累计 token」饼图（`recharts` 或 `@chayuan/ui` 现有图表组件）。
  - 容量看板独立页 `/space/:id/observe`（可挂在 RunsSection 顶部）。
- **价值**：私有化客户上线后必须能看自己 token 用量，否则计费 / 容量都不可解释。

---

### 🟡 P2：协作 / 多人编辑能力补完（M8.5 v2）

#### P2-1 草稿 ETag 心跳 + 当前编辑者 Presence
- **现状**：冲突时弹 dialog 已能解，但用户**冲突发生后**才知道有别人在编辑。
- **方案**：
  - 后端新增 `PUT /apps/{id}/draft/heartbeat` （30s 一次，写入 in-memory `presence_map: {app_id → {user_id: last_beat}}`，TTL 60s）；
  - 通过现有 `ai_space_router` SSE 通道（或新开 `/apps/{id}/presence/stream`）下发 `editing_users` 列表；
  - Studio 顶栏右上：彩色头像堆叠 + tooltip "张三 1 分钟前在编辑提示词区"。
- **成本**：1 周（含前后端 + i18n）。

#### P2-2 JSON DSL Diff 视图（草稿 vs 上一发布版 / 冲突双方）
- **现状**：`EtagConflictDialog.tsx:8` 自己留了 TODO `M8.5 v2 接 json-diff 视图`。
- **方案**：动态 import `jsondiffpatch`（轻量，~30KB gz）；在冲突 dialog + 版本历史 tab 展示树状 diff，节点 / 边 / 变量分组高亮。

#### P2-3 节点级评论 / 锚点
- **方案**：新表 `ai_app_node_comment(app_id, node_id, author_id, body, created_at)`；`CustomNode` 右上加气泡角标显示评论数；点击在 `NodeConfigPanel` 末尾展开 thread。
- **价值**：发布前 Review 流的核心。

---

### 🔵 P3：渠道扩展 + 智能化（中长期）

#### P3-1 IM 渠道（M9）
- **首选**：飞书（接口最完整）+ 企业微信（中国私有化主力）。
- **抽象**：`server/notify/` 已有 `Notifier` 抽象（M2.7 时候做的）；新增 `feishu_card_notifier.py / wecom_notifier.py`。
- **Human-Task 卡片回调**：消息卡片点"通过/驳回" → 回调 `/api/v1/tasks/{token}/complete`（带 `X-Channel: feishu` 头 + 验签）。
- **配置**：`/space/:id/channels` 页面（每个 App 多个绑定）。

#### P3-2 节点市场 v0（用户分享自定义工具）
- **背景**：tools 平台目前只有内置工具；私有化客户希望写自己的 tool 给同事用。
- **方案**：复用 App 的"public + fork"心智，独立一类 entity `custom_tool`（schema + http endpoint）；先做"个人草稿 → 私有化部署内分享"，公网商店延后。

#### P3-3 RAG 质量监控 + 引用质量评估
- **背景**：KB 节点召回率 / 引用准确率 / 回答 faithfulness 现没有数字。
- **方案**：`runs` 增 `kb_retrieve_metrics: { hit, recall_at_k, top_score }` 字段；`/space/:id/observe` 多一个 RAG tab；每天定期挑样本算 LLM Judge faithfulness 分数。

#### P3-4 Multi-Agent 编排范式
- **现状**：`subflow` 节点已能调用别的 App，但缺协同 / 投票 / 聚合的标准模式。
- **方案**：在 `seeds.py` 增 3 个 multi-agent 模板（辩论 / 路由分发 / 投票聚合），不动引擎；用 parallel + subflow 组合即可。

#### P3-5 长任务异步运行（邮件 / IM 通知结果）
- **现状**：所有 run 都是同步 SSE；超过 5 分钟客户端断了就丢了。
- **方案**：runApp 新参 `mode: 'sync' | 'async'`；async 模式立刻返回 `{run_id}`，结束后通过 `Notifier` 推用户邮箱 + Tasklist 一条已完成。

---

### ⚪ P4：技术债 / 体验细节（持续清理）

#### P4-1 ChatRuntime 历史拼接 token 爆炸
- **位置**：`ChatRuntime.tsx:99 buildHistoryText`。
- **问题**：每次 send 把全部历史拼成一个字符串塞进 `user_input`；轮次多了必爆 context。
- **方案**：v1 简单截断（最近 N 轮 + 系统摘要占位）；v2 走后端 `conversation_id` + 服务端记忆（已规划）。

#### P4-2 流式 token 渲染节流
- **位置**：`ChatRuntime.tsx:135 setMessages` 每个 token 一次 setState。
- **问题**：`gpt-4o`-级速度下，约 20 tps，体感 OK；但 `vllm` 高吞吐场景 100+ tps 时主线程会卡。
- **方案**：`useDeferredValue` 或 RAF 节流缓冲，每 50ms flush 一次。

#### P4-3 NodeConfigPanel 拆分
- **位置**：`flow/NodeConfigPanel.tsx` 单文件目前承担所有节点类型的字段编辑器（超 1000 行）。
- **方案**：拆为 `flow/configs/{LlmFields,KbFields,HttpFields,...}.tsx`；NodeConfigPanel 只做 router + footer。
- **价值**：节点新增成本下降；测试用例拆得更细。

#### P4-4 端口类型推断 / 连线类型校验
- **现状**：`FlowCanvas` 连线没有类型校验；只有 DSL 校验 `next` 引用是否存在。
- **方案**：每个节点声明 `inputs: Schema, outputs: Schema`（已经存在 outputs 映射，可补 inputs）；连线时类型不匹配画红 + tooltip。

#### P4-5 资源 vault（敏感信息占位符）
- **现状**：HTTP 节点的 Bearer token、tool 调用的 API Key 都明文写在 DSL 里。
- **方案**：新表 `ai_app_secret(app_id, key, value_encrypted)`；DSL 写 `vault://my_api_key`，运行时 executor 注入；调试 / 导出永远 mask。
- **价值**：M8.7 的 "secrets 永远空" 原本就是为它预留的口子。

#### P4-6 Studio 顶栏 Preview 子窗口
- **现状**：调试要切 `runtime` tab，丢失 flow 上下文。
- **方案**：顶栏新增「Preview」按钮，弹出 right drawer 内嵌 iframe `/space/:id/runtime?embed=1&use_draft=1`，与画布并排。
- **价值**：边改边试。

---

## 3. 推荐节奏（4 个 sprint）

> 按 1 名全栈 + 1 名前端，每 sprint = 2 周。

### Sprint 1（即刻动手 · UX 红利）
- P0-1 Markdown 渲染
- P0-2 输出操作集
- P0-3 提示词预览
- P0-5 Variables Mention
- P4-1 历史 token 截断

→ 出货：ChatRuntime 看起来明显"专业"了；提示词调试效率翻倍。

### Sprint 2（市场放量 · 漏斗优化）
- P0-4 模板试用按钮
- P1-3 access_code + 二维码
- P1-4 测试集阻断 publish + 评估器扩展
- P4-6 Studio Preview

→ 出货：MarketPage 转化率上升；publish 不再"野发布"。

### Sprint 3（合规 + 可观测）
- P1-2 限流中间件
- P1-5 OTel + 容量看板
- P2-1 心跳 + Presence
- P4-5 vault

→ 出货：私有化客户能跑生产 SLA。

### Sprint 4（编辑 + 沙箱）
- P1-1 code 节点沙箱
- P2-2 DSL diff
- P2-3 节点评论
- P4-3 NodeConfigPanel 拆分
- P4-4 端口类型校验

→ 出货：多人编辑、复杂应用编排能力齐了。

之后进入 P3 选型期：IM 渠道、节点市场、Multi-Agent、RAG 监控按客户需求拉单。

---

## 4. 不在本计划里（再次确认）

- 计费 / 月度账单 / 跨租户结算（私有化无此需求）。
- 公网 SaaS 嵌入分发（embed.js 仅给私有内网域名）。
- 任意代码运行（必须沙箱，且 admin 显式开启）。
- 自研画布库 / DSL 引擎（reactflow + 自研 JSON DSL 已够用）。
- 兼容 LangGraph PostgresSaver checkpointer（已用自研 state_blob 续跑，效果等价；除非要接 LangGraph 官方 visualize 工具再说）。

---

## 5. 立即可执行（Definition of Ready）

下列三条本周可直接拉票开工，无需更多对齐：

1. **P0-1 Markdown 渲染**：把 `features/chat/MessageBubble.tsx` 抽成 `<MessageMarkdown>` 共享组件，ChatRuntime / WorkflowRuntime / PublicAppPage 三处接入。
2. **P0-3 LLM 提示词预览**：`NodeConfigPanel` LLM 区下增"展开预览"，调用 `mustache(persona.system_prompt + params.prompt, { state: defaultsFromVariables(), persona })`。
3. **P0-5 Variables Mention**：在 `NodeConfigPanel` 所有 textarea 入参上挂 `useMentionPicker(stateSchema)`，触发字符 `{{`。

——以上三条对外不破坏任何契约、无后端 / 无 schema 改动；可作为 Sprint 1 启动票。
