# 用户反馈 17 项需求 — 总览与索引

> 本文档汇总 2026-04-29 用户反馈的 17 条改进点，按问题域归并为 **5 个工作块**，每块独立 MD 供逐块审查。
>
> 改进总目标：**架构师视角**（模块化 / 复用 / 高性能 / 可扩展 / 可移植） × **UE 视角**（顺手 / 美观 / 有趣味）×
> **行业最佳实践**（KB 检索准确率 > 95%、文档型操作成熟链路、自定义助手生态）。

## 0. 17 项需求归并表

| # | 反馈点 | 归属计划 | 优先级 |
| --- | --- | --- | --- |
| 1 | Sidebar 上下占满 / 顶部 Tab 从 Sidebar 右侧开始 | A. Shell 布局 | 🔴 P0 |
| 2 | 顶部 Tab 可点切换 | A | 🔴 P0 |
| 13 | 编辑页支持全屏单独打开 + 回退 | A | 🟠 P1 |
| 11 | 文档/表格/演示稿在新 Tab 打开 + 可拖出独立窗口 | A | 🟠 P1 |
| --- | --- | --- | --- |
| 3 | 知识中心命中不到上传文件 | B. KB 检索重构 | 🔴 P0 |
| 4 | 搜索按 KB 配置的嵌入模型，禁止换模型 | B | 🔴 P0 |
| 5 | 对话框去掉嵌入模型选择 | B | 🔴 P0 |
| 6 | "精准 / 全面 / 速度" 仅在知识/办公对话出现 | B | 🟠 P1 |
| 7 | 行业最佳实践：文件名 / 标题 / 章节多路召回 | B | 🟠 P1 |
| 16 | 检索 > 95%；进度条；折叠总结；点击文件预览 + 高亮跳转 | B | 🟠 P1 |
| --- | --- | --- | --- |
| 8 | 我的文档右键菜单 / inline 重命名 | C. 办公 - 我的文档 UX | 🟠 P1 |
| 9 | 我的文档顶部搜索（名 + 内容） | C | 🟠 P1 |
| 10 | 右侧栏右键菜单（新建分组 / 重命名 / 拖拽） | C | 🟠 P1 |
| --- | --- | --- | --- |
| 12 | AI 写作助手（左助手 / 中文档 / 下输入 / 右消息）+ 7 大链路 | D. AI 写作助手 | 🔴 P0 |
| --- | --- | --- | --- |
| 14 | 知识中心可新建文档/表格/演示稿，保存自动入库 | E. 知识 ↔ 办公一体化 | 🟠 P1 |
| 15 | 知识中心文件可调用办公编辑器编辑 | E | 🟠 P1 |
| --- | --- | --- | --- |
| 17 | 每块独立 MD 供审查 | （本文档 + 5 份分块）| 🔴 P0 |

> 第 16 条的"架构 / UE / 最佳实践"是**横切原则**，写进每块计划的开头。

## 1. 计划文件清单

| 编号 | 文件 | 主题 | 涉及反馈点 | 工程量 |
| --- | --- | --- | --- | --- |
| **A** | `feedback-A-shell-layout.md` | Shell 布局 + Tab 路由 + 全屏 + 拖出独立窗 | 1, 2, 11, 13 | 1.5w |
| **B** | `feedback-B-kb-search-rebuild.md` | 知识中心检索重构（绑定嵌入模型 + 多路召回 + 进度 + 高亮跳转）| 3, 4, 5, 6, 7, 16 | 3w |
| **C** | `feedback-C-office-my-docs-ux.md` | 我的文档右键菜单 + inline 重命名 + 搜索 | 8, 9, 10 | 1w |
| **D** | `feedback-D-office-ai-writing-assistant.md` | AI 写作助手（**重头戏**）：助手清单 + 7 链路 + 自定义助手 | 12 | 5w |
| **E** | `feedback-E-kb-office-integration.md` | 知识 ↔ 办公双向打通（KB 内创建 + KB 文件用办公编辑器编辑）| 14, 15 | 1w |

**合计 ≈ 11.5 工程周**（双人并行 ≈ 6 周自然时间）。

## 2. 横切原则（每块复用）

### 2.1 架构

- **单一聚合入口**：每个模块对外 1 个 store + 1 个 hooks + 1 个 routes 文件
- **表驱动 + 注册表**：助手 / 节点 / 操作类型 / 路由 用注册表，不写大 switch
- **能力分层**：`UI 组件 → use* hooks → store → API client → 后端 router → service → repository` 七层；跨层禁直连
- **代码复用**：把"内嵌 SSE 解析器""乐观锁冲突处理""文档操作""KB 多路召回"做成 `packages/{api,ui,app}/src/lib/` 公共原子
- **可移植性**：所有需要文件系统 / 通知 / 窗口的能力走 `@chayuan/platform-shared` 接口；web/tauri 各自实现

### 2.2 性能与并发

- **检索**：多路召回并发（`asyncio.gather`）；BM25 + 向量 + 关键词同时跑，时延 ≤ 最慢一路
- **流式**：所有长流程走 SSE，token 级 `requestAnimationFrame` 缓冲
- **缓存**：60s TTL + ETag + 失败 fallback（不污染缓存）
- **乐观锁**：所有可写资源用 `etag`；冲突弹 EtagConflictDialog
- **DB**：高频写入 JSON 列要拆 `b-tree GIN index`；`ai_app_run.state_blob` 单行 < 256KB

### 2.3 UE / 视觉

- 沿用现有 design token；禁止新色板
- 状态机化的 toast / banner / dialog；禁止 alert
- 所有交互有 30ms / 150ms / 240ms 三段 transition
- "好玩"细节：节点拖入有 fly-to-canvas、删除有 puff、命中有 yellow 高亮 800ms 后渐隐
- **绝不破坏键盘焦点**；所有对话框 ESC + Cmd-Enter

### 2.4 可观测

- 每个新模块自带 `record_feature_use(name)` 调用，落 `governance` 的指标桶
- 关键操作自带 OpenTelemetry span（`trace_id` 串前后端）
- 错误 banner 必须显示"请将这段错误编码 (e.code) 发给管理员" → 复制按钮

## 3. 推荐落地顺序

```
Week 1   ──> A（Shell 布局）          → 释放所有后续工作的物理空间
Week 2-3 ──> B（KB 检索重构）         → 解决最大用户痛点（命中失败）
Week 3-4 ──> C（我的文档 UX）+ E      → 小改动；C 与 E 互不冲突可并行
Week 4-8 ──> D（AI 写作助手）         → 重头戏；放最后做让基础设施稳定
```

**风险提示**：D 的工程量是其他四块之和；前期建议拆 D-1（架子 + 内置 5 助手）和 D-2（自定义 + 报告）两段交付。

## 4. 决策记录（待用户确认的关键岔路）

每份分块 MD 末尾会有「待你确认」清单。汇总几个关键决策：

| 决策 | 推荐 | 替代 |
| --- | --- | --- |
| 顶部 Tab 是否替代 Sidebar 二级导航？ | 共存；Tab = 已打开的工作区，Sidebar = 全局导航 | 二选一（更激进）|
| 拖出独立窗口的实现 | Tauri webview API；web 退化为 `window.open` | 仅 web 同窗口 |
| AI 写作助手的运行时 | 复用 AI Space `flow/runtime`，每个助手 = 一个 Flow | 单独写一套 |
| KB 多路召回融合算法 | RRF（Reciprocal Rank Fusion） + Rerank | 仅向量 + 阈值 |
| 自定义助手的"操作动作"由谁生成 | LLM 接收提示语 → 输出 JSON Action 列表 | 表单手填 |

> 默认按推荐执行；任何替代选项请在分块 MD 上标注。

## 5. 与既有 ai-space 计划的关系

| 既有计划 | 关系 |
| --- | --- |
| `ai-space-orchestration.md` | D 的"自定义助手"复用 §22 Human-Task 节点 + Flow DSL |
| `ai-space-ui-design.md` | A 的 Shell 布局对应 §1.IA 的更新；本次需要 amend |
| `ai-space-private-deployment.md` | B 的嵌入模型策略需要补"管理员配额 / 模型一致性"章节 |
| `ai-space-audit-and-fix-plan.md` | 本次新缺陷另立专门 MD；上份审计的 P0 修复（vite proxy）是本批工作的前置 |

## 6. 状态条

- [x] 总览索引（本文）
- [x] A. Shell 布局 → `feedback-A-shell-layout.md`
- [x] B. KB 检索重构 → `feedback-B-kb-search-rebuild.md`
- [x] C. 我的文档 UX → `feedback-C-office-my-docs-ux.md`
- [x] D. AI 写作助手（重头戏）→ `feedback-D-office-ai-writing-assistant.md`
- [x] E. 知识 ↔ 办公双向打通 → `feedback-E-kb-office-integration.md`

每份 MD 末尾的"待你确认"清单合计 **30+ 决策点**——审完后我会按你的决定一次性收口动工。
