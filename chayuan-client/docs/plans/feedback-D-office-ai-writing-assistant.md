# Block D — 察元办公 · AI 写作助手（重头戏）

> 关联反馈点：12（左助手清单 / 中文档 / 下输入 / 右消息）+ 7 大链路 + 自定义助手 + 报告输出
>
> 参考项目：`/work/chayuan/src/utils/assistant/*` + `documentActions.js` + `assistantStructuredPipeline.js`
>
> **核心目标**：让"对话"不只是聊天，而是**精准、可定位、可重做的文档操作**。

## 0. 现状 + 参考项目要点

### 0.1 现状

`packages/app/src/features/office/OfficeEditorPage.tsx` 当前布局：左侧大纲、中间 OnlyOffice iframe、右侧无（或简单 chat）。**没有助手清单 / 助手注册表 / 文档操作动作 / 自定义助手**。

### 0.2 参考项目（`/work/chayuan`）的成熟模型

```
src/utils/assistant/
  ├─ builtinAssistantsP5.js / P5Plus.js / Extra.js   ← 内置助手数据
  ├─ externalAssistants.js                           ← 用户自定义
  ├─ runtimeAssistantsInstaller.js                   ← 运行时安装
  ├─ marketplaceManager.js / Crypto signer           ← 助手市场分发 + 签名
  ├─ ragIndex.js                                     ← 助手内嵌的小 RAG
  └─ anchorAutoRegister.js                           ← 锚点自动注册
src/utils/
  ├─ documentActions.js              ← 25+ 文档操作原子（replace/insert/comment/...）
  ├─ assistantStructuredPipeline.js  ← 结构化助手三类管道
  ├─ dialogTextDisplay.js
  └─ structuredCommentPolicy.js      ← 批注落点策略
src/components/
  ├─ AIAssistantDialog.vue           ← 助手对话框
  ├─ DocumentDeclassifyDialog.vue    ← 涉密检查
  ├─ TemplateFieldExtractDialog.vue  ← 表单字段提取
  └─ FeatureTourPage.vue             ← 引导
```

**助手数据结构**（提炼自 builtin*）：
```js
{
  id: 'spell-check',
  name: '拼写与语法检查',
  category: 'analysis',
  systemPrompt: '...',                         // LLM 角色
  defaultInputSource: 'fulltext|selection|paragraph',
  defaultOutputFormat: 'comments|replace|append|json',
  allowedActions: ['replace', 'prepend', 'insert-after', 'comment'],
  iconKey: 'sparkles',
  pipeline: 'structured|transform|plain',      // 三类管道
}
```

**三类管道**（`assistantStructuredPipeline.js`）：
- `structured` —— 输出 JSON 列表，每条带定位锚点（用于"批注 / 风险点"等需要逐条落地的）
- `transform` —— 输入文本 → 输出全新文本（用于"翻译 / 改写 / 扩写 / 缩写"）
- `plain` —— 普通对话，直接展示 markdown

**文档操作原子**（`documentActions.js` 提炼）：
| 动作 | 语义 |
| --- | --- |
| `replace` | 替换选中或匹配范围 |
| `prepend` | 在选中之前插 |
| `append` | 在选中之后插 |
| `insert-after` | 在指定段落后插 |
| `insert-before` | 在指定段落前插 |
| `insert-at-cursor` | 当前光标位置插 |
| `comment` | 添加批注 |
| `highlight` | 高亮区间 |
| `delete-range` | 删除区间 |
| `format` | 改格式（粗/斜/标题级别）|
| `apply-style` | 应用样式（标题 / 代码块）|
| `find-replace-all` | 全局替换 |
| `bulk-format` | 批量格式（如所有图片居中） |
| `outline-from-text` | 用大纲改写正文 |

> 这些已经覆盖反馈 12 中的所有"加粗 / 替换 / 批注 / 插段首段尾 / 全文处理 / 大纲生成"。本块不重新发明，而是**抽象化 + 落到我们自己的注册表**。

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Studio TopBar (返回 / 文档名 / Save / Share / 全屏)                        │
├──────────┬───────────────────────────────────┬─────────────────────────────┤
│ 助手     │  Office Editor (OnlyOffice / 本地编辑)                          │
│ 清单     │  - 左大纲（可折叠）                                              │
│ (LHS)    │  - 中编辑区（DOC / XLS / PPT）                                   │
│          │  - 右侧顶栏：当前选中文本预览（透传给助手）                     │
│          │                                                                 │
│ 内置 +   │                                                                 │
│ 自定义   │                                                                 │
│ + 我的   │                                                                 │
│          │                                                                 │
│ [+ 创建] │                                                                 │
│          │                                                                 │
├──────────┴───────────────────────────────────┬─────────────────────────────┤
│  Composer (输入框 + 输入源切换 + 发送)        │ 消息列表 (RHS, 可折叠)     │
│  📎 文件 / 🌐 全文 / ✂ 选中 / ¶ 当前段       │  user → assistant 流式      │
│  [输入要做的事…]                              │  操作 trace + Apply 按钮    │
│                                              │  报告 / 结果 输出           │
└──────────────────────────────────────────────┴─────────────────────────────┘
```

## 2. 7 大链路（关键）

> 反馈 12 列了若干助手；归纳为 **7 类操作链路**，每类都是一个稳定 pipeline。

### 链路 1：纯对话（plain chat）
- 用户问"如何写..."
- 不操作文档；只在 RHS 消息列表显示流式回答
- 输入源：`none` / `fulltext`（自动塞为参考）
- 用例：通用写作助手 / 一次性问答

### 链路 2：选中改写（transform-selection）
- 选中一段 → 选助手「换种方式重写 / 缩写 / 扩写 / 润色 / 正式化 / 通俗化」
- 输出格式：新文本，准备替换原文
- **UX**：差异面板（原文 vs 新文 + 接受 / 拒绝按钮）；接受 = `replace` 动作
- 输入源：`selection`

### 链路 3：插入式生成（insert-at-cursor / paragraph-end）
- 用户写到一半，"在这里插一段背景介绍"
- 输出格式：新文本
- **UX**：流式插入到光标 / 段尾（Apple Pages 智能写作风格），可撤销
- 输入源：`paragraph` 或 `cursor-context`

### 链路 4：结构化批注（structured-comments）
- 选「拼写与语法检查 / 涉密检查 / 风险审查 / 术语统一」
- 输出格式：JSON 列表 `[{anchor: {start,end}, severity, message}]`
- **UX**：每条 issue 在原文加圆形批注角标；点开看说明 + Accept / Ignore
- 输入源：`fulltext`

### 链路 5：摘要 / 报告生成（report-output）
- 选「生成摘要 / 提炼关键词 / 生成会议纪要 / 提取行动项」
- 输出格式：独立"报告卡片"附在助手右侧消息列表，含复制 / 下载 / 插入到文档末尾选项
- 输入源：`fulltext`

### 链路 6：批量 / 全文处理（bulk-transform）
- 选「翻译全文 / 文档脱密 / 删除空白行 / 表格批量格式化」
- 输出格式：替换全文（可预览 diff）/ 落到副本
- **UX**：进度条 + diff 预览 + 一键应用
- 输入源：`fulltext`

### 链路 7：大纲 → 正文（outline-to-body）
- 用户给一个大纲（或用助手"生成标题与大纲"）
- 助手按大纲展开成正文（保留章节层级）
- 输出格式：完整文档结构 → 替换 / 新建文档
- 输入源：`outline`

### 链路速查表

| 链路 | 输入源 | 输出格式 | 文档动作 | 典型助手 |
| --- | --- | --- | --- | --- |
| 1 纯对话 | none / fulltext | markdown | （无） | 通用写作 |
| 2 选中改写 | selection | text | replace | 缩写 / 扩写 / 润色 / 通俗化 |
| 3 插入生成 | paragraph / cursor | text | insert-at-cursor / append | 续写 / 插入示例 |
| 4 结构化批注 | fulltext | JSON list | comment + highlight | 拼写检查 / 风险审查 / 涉密 |
| 5 报告输出 | fulltext | markdown report | (sidebar 卡片) | 摘要 / 关键词 / 行动项 / 会议纪要 |
| 6 批量改写 | fulltext | full text | replace-all | 翻译 / 脱密 / 格式化 |
| 7 大纲 → 正文 | outline | structured doc | replace-doc / new-doc | 写正文 / 生成报告 |

## 3. 核心数据模型

### 3.1 Assistant 注册表

```ts
// packages/app/src/features/office/assistant/types.ts
export interface Assistant {
  id: string;
  name: string;
  description?: string;
  iconKey?: string;
  category: 'analysis' | 'rewrite' | 'translate' | 'extract' | 'security' | 'format' | 'custom';
  pipeline: 'plain' | 'transform' | 'structured' | 'report' | 'bulk' | 'outline';
  systemPrompt: string;
  defaultInputSource: InputSource;
  defaultOutputFormat: OutputFormat;
  allowedActions: ActionType[];
  /** 用户额外参数表单（如翻译目标语言）*/
  paramSchema?: JSONSchema;
  /** 仅 custom 助手：来自哪个用户 */
  ownerId?: number;
  /** 内置标识 */
  builtin: boolean;
  /** 启用状态 */
  enabled?: boolean;
}

export type InputSource = 'none' | 'selection' | 'paragraph' | 'cursor' | 'fulltext' | 'outline';

export type OutputFormat = 'markdown' | 'text' | 'json-comments' | 'json-report' | 'doc-replace' | 'doc-structure';

export type ActionType =
  | 'replace' | 'prepend' | 'append'
  | 'insert-after' | 'insert-before' | 'insert-at-cursor'
  | 'comment' | 'highlight' | 'delete-range'
  | 'format' | 'apply-style' | 'find-replace-all'
  | 'bulk-format' | 'replace-doc' | 'new-doc'
  | 'sidebar-report';
```

### 3.2 Action 数据契约（LLM 输出 / 后端校验）

LLM 在 transform / structured / report / bulk pipeline 中**输出 JSON Action 列表**，前端按动作执行：

```jsonc
[
  { "type": "replace", "range": {"start": 1280, "end": 1340}, "text": "新文本" },
  { "type": "comment", "range": {"start": 980, "end": 1020}, "message": "建议改用主动语态", "severity": "info" },
  { "type": "highlight", "range": {"start": 980, "end": 1020}, "color": "#FFF59D" },
  { "type": "insert-at-cursor", "text": "在此插入内容" },
  { "type": "sidebar-report", "title": "摘要", "markdown": "..." }
]
```

**校验**：JSON Schema 在前后端共享（`packages/api/src/officeAssistant.ts`），LLM 输出非法 action 时降级为 plain 文本展示。

### 3.3 助手运行 Job

```ts
interface AssistantJob {
  id: string;
  assistantId: string;
  assistant: Assistant;
  docId: number;
  inputSource: InputSource;
  inputRange?: { start: number; end: number };  // selection / paragraph 用
  inputText: string;                              // 实际抓到的文本
  userPrompt?: string;                            // 用户额外说话
  status: 'pending' | 'running' | 'review' | 'applied' | 'rejected' | 'failed';
  output?: string | unknown[];
  actions?: Action[];
  error?: string;
  startedAt?: number;
  finishedAt?: number;
}
```

Job 流：

```
pending → running → review (用户审 diff)
                → applied (动作落地到文档)
                → rejected (用户取消)
                → failed (LLM / 网络错)
```

## 4. UI 组件清单

### 4.1 LHS 助手清单（Sidebar）

```
┌─助手 (37)──────────────┐
│ 🔍 搜索助手…           │
├────────────────────────┤
│ ▼ 内置（22）            │
│   🌐 翻译               │
│   ✏️  拼写与语法检查    │
│   📝 生成摘要           │
│   🔍 文本分析 ▾         │
│       ├─ 缩写           │
│       ├─ 扩写           │
│       ├─ 换种方式重写   │
│       ├─ 提炼关键词     │
│       ├─ 批注解释       │
│       ├─ 超链接解释     │
│   🛡️  涉密检查         │
│   📃 文档脱密           │
│   🔄 脱密复原           │
│   ⌫  删除空白行         │
│   📊 表格批量           │
│   🖼️  图像批量          │
│   ...                   │
├────────────────────────┤
│ ▼ 自定义（8）           │
│   ⚙ 政策风格改写        │
│   ...                   │
│   [+ 创建助手]          │
├────────────────────────┤
│ ▼ 我的（最近用 5）       │
└────────────────────────┘
```

- 每条助手悬停显示 systemPrompt 摘要 tooltip
- 单击助手 = 打开"运行确认"小弹窗（确认输入源 + 额外参数 + 运行）
- 拖拽助手到选中文本上 = 一键执行（高级动线）

### 4.2 Composer（底部输入区）

```
┌────────────────────────────────────────────────────────────────┐
│ 输入源: ◯ 全文  ◯ 选中(286 字)  ◉ 当前段(54 字)               │
│ ┌────────────────────────────────────────────────────────────┐ │
│ │  请扩写这一段，加 2 个示例和 1 个反例                       │ │
│ │                                                            │ │
│ └────────────────────────────────────────────────────────────┘ │
│ 助手: 🚀 自动判定（按提示语智能选） / 或选 [扩写]              │
│                                              [运行 ⏎]         │
└────────────────────────────────────────────────────────────────┘
```

**自动判定** = 用本地小模型 / 关键词规则把用户提示语映射到 7 类管道之一：
- 出现"加粗 / 改格式" → 链路 6 bulk-transform
- 出现"扩 / 缩 / 改写 / 润色" → 链路 2 transform-selection
- 出现"批注 / 检查" → 链路 4 structured-comments
- 出现"摘要 / 关键词 / 报告" → 链路 5 report-output
- 出现"翻译" → 链路 6 bulk-transform
- 出现"在这里 / 插入" → 链路 3 insert-at-cursor
- 默认 → 链路 1 plain chat

### 4.3 RHS 消息列表

每个 Job 一张卡片：

```
┌─────────────────────────────────────────────┐
│ 🌐 翻译 · 全文                              │
│ 输入: 1240 字                               │
│ ─────                                       │
│ ▶ 流式 token (markdown 渲染)                │
│ ─────                                       │
│ 共 14 条修改：                              │
│   [✓] §1 标题翻译为 "Chapter 1: ..."        │
│   [✓] §2 段落 1 翻译                        │
│   ...                                       │
│ [全部接受] [拒绝并丢弃] [部分应用]          │
└─────────────────────────────────────────────┘
```

折叠按钮：把整个 RHS 收到右侧 36px 的胶囊条。

### 4.4 自定义助手创建器

入口：LHS 底部"+ 创建助手"。

形态：3 步 wizard：

**Step 1 — 描述意图**

```
告诉我你想要的助手，我帮你生成提示词与动作配置。

[ 我想要一个能把会议记录变成行动项清单的助手，并加批注标注谁负责… ]
                                                   [ 用 AI 生成 ]
```

→ 调小模型生成 systemPrompt + 推理出 pipeline + actions + paramSchema → 填入 Step 2

**Step 2 — 复核 / 微调**

| 字段 | 值 |
| --- | --- |
| 名称 | 会议行动项提取器 |
| 图标 | ✅ |
| 提示词（system） | （多行编辑器，预览 + 编辑） |
| Pipeline | structured（自动选） |
| 输入源 | 全文 |
| 输出格式 | json-comments + sidebar-report |
| 允许的动作 | comment, sidebar-report |
| 额外参数 | （无） |

**Step 3 — 测试 + 保存**

- 内置 fixture 文档（一段会议记录）
- 实时跑一遍，预览 actions 输出
- "保存到我的助手" / "提交到团队市场（如已配置）"

### 4.5 助手市场（参考 chayuan/marketplaceManager）

仅团队 / 公开部署有意义；私有化默认关。开了后：
- LHS"创建助手"旁多一个"逛市场"
- 列出团队成员发布的助手 + 内置精选
- 安装 = 加进我的助手列表（带签名校验，可选）

## 5. 后端契约

| 端点 | 用途 |
| --- | --- |
| `GET /office/assistants` | 列内置 + 我的自定义（分组返回） |
| `POST /office/assistants` | 保存自定义助手 |
| `PATCH /office/assistants/{id}` | 更新自定义助手 |
| `DELETE /office/assistants/{id}` | 删除（仅 owner）|
| `POST /office/assistants/generate` | 用 LLM 生成 systemPrompt + 配置（Step 1）|
| `POST /office/jobs` | 启动一个 Job，返回 SSE |
| `POST /office/jobs/{id}/apply` | 把 actions 应用到文档（行锁 + version） |
| `POST /office/jobs/{id}/reject` | 拒绝 |
| `GET /office/jobs?doc_id=` | 历史 Job 列表 |

**SSE 帧形态**（与 ai-space 同 family）：

```jsonc
{ "type": "meta",        "job_id", "assistant_id", "pipeline" }
{ "type": "input",       "source", "char_count", "preview" }
{ "type": "token",       "delta": "..." }                 // 流式输出
{ "type": "action",      "action": { ... } }              // 一条 action 到位
{ "type": "report",      "title", "markdown" }
{ "type": "done",        "actions_count", "duration_ms" }
{ "type": "error",       "error", "kind" }
```

## 6. 文档操作引擎（前端 - 编辑器无关层）

```
packages/app/src/features/office/doc-engine/
  ├─ adapter.ts        ← 适配 OnlyOffice / Quill / 本地编辑器
  ├─ actions.ts        ← 14 个 action 实现（applyReplace / applyComment / ...）
  ├─ position.ts       ← 字符 offset ↔ DOM range 映射
  ├─ diff.ts           ← 文本 diff（用于 review 面板）
  └─ usePosition.ts    ← hooks
```

`actions.ts` 每条对应一个 `ActionType`，签名统一：

```ts
async function applyAction(adapter: EditorAdapter, action: Action): Promise<void>;
```

**关键**：所有动作必须**幂等**（同 action 重放结果一致）+ **可撤销**（写入文档历史栈，对应 Cmd+Z）。

### 6.1 编辑器适配（OnlyOffice）

OnlyOffice 不可直接操作底层 XML；用 `Asc.scope` API：

```js
window.Asc.plugin.callCommand((Asc.scope = { range: [start, end], text: 'new' }) => {
  const oDoc = Api.GetDocument();
  const range = oDoc.GetRangeByPos(scope.range[0], scope.range[1]);
  range.Delete();
  Api.GetActiveContent().GetParagraph(0).AddText(scope.text);
});
```

**位置映射**：以纯文本字符 offset 为契约 → adapter 内部把 offset 映射成 docx XML range。

## 7. 操作类型分类（反馈 12 关键）

按"做什么"维度归类：

| 操作族 | 子类型 | 实现路径 |
| --- | --- | --- |
| **大模型对话** | plain chat / Q&A | LLM SSE，无文档动作 |
| **文档读** | 取选中 / 取全文 / 取段落 / 取大纲 | `doc-engine/adapter` 抽 API |
| **文档写** | replace / insert / comment / highlight / format / find-replace | `doc-engine/actions` |
| **文档结构变更** | replace-doc / new-doc / outline-from-text | 高阶；最后做 |
| **执行助手** | 选助手 + 自动管道 + 用户参数 | `useAssistantJob` hooks |
| **执行工具** | 调外部 KB / 浏览 / 表单 schema 提取 | 复用 ai-space tools；助手内嵌可调 |
| **生成报告** | sidebar-report → markdown 卡片 | UI only；可复制 / 下载 / 插入文档 |
| **批量** | 全文翻译 / 全文脱密 / 表格批量 | bulk pipeline；带进度条 |

## 8. 工程量

| 子项 | 工程量 |
| --- | --- |
| **D-1 架子（架构 + 数据模型 + LHS 助手清单）** | 0.5w |
| 后端：assistants CRUD + LLM-generate | 0.3w |
| 后端：jobs SSE + apply/reject 端点 | 0.5w |
| 前端：Composer 输入源切换 + 自动判定 | 0.3w |
| 前端：RHS 消息列表 + Job 卡片 | 0.4w |
| **D-2 7 大链路逐个实现** | 1.5w |
|   - 链路 1 plain（最简） | 0.1w |
|   - 链路 2 transform-selection + diff 面板 | 0.2w |
|   - 链路 3 insert-at-cursor / paragraph | 0.2w |
|   - 链路 4 structured-comments + 锚点角标 | 0.4w |
|   - 链路 5 report-output + 卡片 | 0.2w |
|   - 链路 6 bulk-transform + 进度条 | 0.3w |
|   - 链路 7 outline-to-body | 0.1w |
| **D-3 文档操作引擎** | 1w |
| OnlyOffice adapter | 0.5w |
| 14 个 action 实现 + 测试 | 0.5w |
| **D-4 自定义助手 + 创建器** | 0.7w |
| 3 步 wizard + LLM 生成 | 0.5w |
| 测试运行 + 保存 + 编辑 | 0.2w |
| **D-5 助手清单 22 个内置** | 0.5w |
| 提示词调优 + fixture 测试 | 0.5w |
| **D-6 助手市场（可选）** | 0.5w |
| **D-7 报告输出 / 下载 / 插入文档** | 0.3w |
| **合计** | **5w** |

> 推荐拆 **D-1 + D-2(链路 1-3) + D-3 部分** 为 **第一阶段（2.5w）**先发；其余作为后续迭代。

## 9. 性能 / 并发要点

- 大文档（10w 字）full text 模式：先在前端做"摘要式压缩"再传后端（首段 + 章节标题 + 末段）
- token 流式：`requestAnimationFrame` 缓冲（已有方案）
- structured pipeline 必须分段调用：每段 ≤ 4000 tokens；跨段 anchor 用 char offset 矫正
- jobs 表分库 / 分表（用 `created_at` 月分区），避免 1 万 + 历史 Job 拖慢列表

## 10. 与 AI Space 的关系（重要复用）

D 不是另起炉灶；它**复用 AI Space 的 Flow Runtime + 节点**：

- 每个助手 = 一个**预定义 Flow**（input → llm → optional kb_retrieve → 输出）
- 助手 systemPrompt = Flow 的 `persona.system_prompt`
- 助手参数表单 = Flow 的 `variables`
- 助手 actions = LLM 节点输出 + 一个新增 `office_op` 节点（在 ai-space-orchestration §5 节点白名单里已预留）
- 自定义助手用户视角是"创建助手"，技术视角等于"创建一个 type=text_generation 的 Flow"

**收益**：
- 调试器、版本、协作评论、审计、测试集 全部白嫖
- 助手 = 一种特殊的 AI Space App；后端无新表 / 新执行器
- 助手市场 = 复用 §M5 应用市场

## 11. 待你确认

- [ ] **D-1 + D-2(部分) + D-3 部分作为第一阶段**（2.5w）发，其余迭代 → OK 吗？【全部一次性列入计划 逐项实现】
- [ ] 助手是否**真的等于一个 AI Space App**？这样数据模型 / 执行器零重复。如同意，反馈 12 改名为"AI Space App in Office Mode"。【可以 点击后进行office的专项处理 同时能够把这块的能力融入到 AI SPACE 】
- [ ] LLM 输出 actions JSON 的 schema 由前端强校验 + 失败降级为 plain；模型不稳是否需要 retry / 自我修复？【需要】
- [ ] structured pipeline 的"批注角标"在 OnlyOffice 怎么落？建议**写入 OnlyOffice 原生批注**（永久跟随文档），而不是只在前端 overlay。【写入 OnlyOffice 原生批注】
- [ ] 自动判定（提示语 → pipeline）由本地规则还是用 LLM 判？规则先行，规则未命中才走 LLM。【规则先行，规则未命中才走 LLM】
- [ ] 是否提供"批量"型助手的"灰度 / 沙箱跑"——先在副本上跑，用户确认后再覆盖原文？建议**默认副本**。【提供】
- [ ] 助手分类 `analysis / rewrite / translate / extract / security / format / custom` 是否完整？【基本完整 允许扩展】
- [ ] 新建助手的"用 AI 生成"选用哪个模型？建议默认走 owner 配置的模型，不强绑某一个。【可以配置模型 新建时能够选择】
- [ ] 22 个内置助手的提示词从 chayuan 项目迁移 vs 重写？建议**迁移为主，私有化必备的（涉密 / 政策风格）做行业定制**。【迁移为主】
