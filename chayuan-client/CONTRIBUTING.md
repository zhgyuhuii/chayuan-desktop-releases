# 贡献指南

## 包边界
- 业务代码只允许 import `@chayuan/*` workspace 包，不准跨阶层（见 ARCHITECTURE.md §1）。
- 新增能力：先在 `packages/platform-shared` 加接口；再在两份实现里补；最后业务层调用。

## 提交
- Conventional commits: `feat(chat): ...` / `fix(api): ...` / `refactor(transport): ...`
- 一个 PR 一个意图；> 600 行需要拆分。

## 开发循环
```bash
pnpm install
pnpm gen:api          # 后端类型同步
pnpm typecheck
pnpm lint
pnpm test
pnpm dev:web
```

## 调试 SSE
- 把 `useChayuanChat` 中 `useWorker: false` 临时关掉 worker；事件流就跑在主线程，方便断点。
- Network → EventStream 看后端原始 frame；与 `parseStructuredSSE` 单测固定的 case 比对。

## 调试 Langfuse
- Langfuse UI: `http://127.0.0.1:3000`
- 单条 trace 深链 = `${LF_HOST}/project/${LF_PROJECT_ID}/traces/${traceId}`
- `MessageBubble` 右下角 ExternalLink icon 可一键打开（Tauri shell.open / Web window.open）

## 添加一个工具卡

1. 后端确认该 tool 在 `/tools?enabled=true` 中可见。
2. 在 `packages/app/src/features/chat/toolcards/` 新建 `<ToolName>Card.tsx`，接收 `tc: ChatToolCall`。
3. 在 `MessageBubble.tsx` 的工具卡渲染分支按 `tc.name` 路由到具体卡片。
4. 写一个 vitest 测试覆盖 input parse → render snapshot。

## 添加一个新事件名

1. `packages/observability/src/events.ts` 增加常量；
2. 业务调用 `logEvent(Events.YourName, ...)`；
3. 在 ARCHITECTURE.md §4 的事件表里登记；
4. 与后端 / Langfuse 看板拉齐。
