# 架构说明

## 1. 分层 / 依赖

```
┌────────────────────────────────────────────────────────────────┐
│  apps/desktop (Tauri 壳)         apps/web (Vite 静态)          │
│       ▲ 注入 platformImpl          ▲ 注入 platformImpl         │
└───────┼──────────────────────────────┼─────────────────────────┘
        │                              │
┌───────┴──────────────────────────────┴────────────────────────┐
│ packages/app (业务大脑：routes / features / stores / Shell)    │
│   ▲             ▲              ▲                ▲              │
│   │             │              │                │              │
│   ui          api          transport       observability       │
│   │             │              │                │              │
│   └─────────────┴──────────────┴──────  platform-shared (PAL)  │
└────────────────────────────────────────────────────────────────┘
                ▲                                ▲
                │                                │
       platform-tauri (桌面)              platform-web (Web)
```

**只许向下依赖**，反向依赖以接口反转（PAL）解决。

## 2. 模块职责

| 包 | 职责 | 不可越界 |
|---|---|---|
| `platform-shared` | 定义 PAL 接口；通用工具（uuid/traceparent） | 不允许 import 任何运行时实现 |
| `platform-tauri` | 桌面实现：Stronghold/SQLite/Tauri http/全局快捷键/托盘 | 不允许 import platform-web |
| `platform-web` | Web 实现：Dexie/sessionStorage/window.fetch | 不允许 import platform-tauri |
| `api` | HTTP 客户端 + 401 刷新 + BizError + 业务封装 | 不允许 import 平台实现 |
| `transport` | SSE 解析（worker 可选）+ ai SDK transport + chayuan ChatGraph 适配 | 不允许 import ui |
| `observability` | Langfuse 客户端 + trace/event/score + 离线 outbox | 不允许 import ui |
| `ui` | 纯样式 / Radix 组件，无业务逻辑 | 不允许 import api/transport/observability |
| `app` | 业务大脑；唯一允许整合上述全部包的层 | 不允许 import 平台实现 |
| `apps/*` | 仅 main.tsx 注入；不写业务 | 不允许重复 app 的逻辑 |

## 3. 高性能 & 并发要点

### 3.1 网络
- 所有 REST 走 `@chayuan/api` 单例 client；401 刷新有**单飞锁**（auth-store.refreshAccessToken），N 个并发请求只刷新 1 次。
- SSE 走 PAL.net.sse；与 REST 共享 token / 401 处理。
- TanStack Query 默认 `staleTime=30s + retry=1`，避免重渲染抖动 / 重试风暴。
- 抓取流式 body 后立即把 `ReadableStream` postMessage 给 worker（transferable），主线程不解码。

### 3.2 状态
- 服务端态走 query；客户端态用 Zustand。**严禁** server data 进 store。
- Zustand selector 写法：`useStore((s) => s.draft)`，组件只在该 slice 变更时重渲。
- composer 草稿 + 模型 + 选择 通过 `persist` 中间件持久到 localStorage（小且非敏感）。

### 3.3 渲染
- 流式 markdown：稳定段 React.memo 缓存、尾段每 token 重渲；
  浏览器空闲时（streaming=false）一次性 reset 缓存。
- Thread 滚动：`stickRef` 仅在用户处于底部时跟随；用户向上翻不打断阅读。
- ToolCall 卡片用 key=tc.id，避免增量参数 re-render 整个气泡。

### 3.4 持久化
- 桌面 SQLite，开 WAL；conversation/message/lf_outbox 表 + FTS5 全文索引。
- Web Dexie schema 与上同构；用极简 SQL 适配器把业务 SQL 翻译到 IDB 操作。
- outbox 容量上限 10k；MAX_ATTEMPTS=6，超出按 FIFO drop + 上报 Sentry（可选）。

## 4. Trace 与 Score 闭环

```
1. composer 提交 → uuid traceId
2. transport.chat 注入 X-Trace-Id / traceparent
3. 后端 chayuan-server 在该 traceId 下：
     langfuse.trace(...)
     langfuse.generation(model, input, output, usage)
     langfuse.span(tool / kb / interrupt)
4. 前端逐事件打点：
     event: chat.send / chat.first_token / chat.complete
     event: tool.approved / rejected / modified
     event: error.*
5. 用户反馈：thumbs up/down → POST /chat/feedback → 后端写 langfuse.score
6. admin 路由"在 Langfuse 中查看" → shell.openExternal(deepLinkTrace)
```

## 5. HIL 流程

```
chat → assistant message 出现 interrupt → MessageBubble 渲染审批卡
      ↓
      用户点 [批准/拒绝]
      ↓
      transport.resumeAndContinue(convId, decision, nextRequest, opts)
      ├─ POST /chat/v2/chat/resume {approved, patch}    ← REST，非流
      └─ 重发 POST /chat/v2/chat   {conversation_id同}  ← LangGraph 续跑
```

`useChayuanChat` 已经实现了这条链路（`resumeWithDecision`）；UI 通过
`Thread.onResume` 暴露。

## 6. 测试策略

- 单测（vitest）：
  - `parseStructuredSSE`：覆盖 OpenAI delta / AG-UI named events / [DONE] / keep-alive / CRLF / error 中断。
  - `auth-store.refreshAccessToken`：并发刷新单飞 lock。
  - `outbox`：入队 / flush / 重试 / 容量 cap。
  - `markdown`：sanitize 关键攻击向量（javascript:/onerror）。
- e2e（playwright）：
  - 登录 → 发消息 → 流式 → 复制 → 反馈
  - 网络中断 → 重连 → outbox flush
  - HIL 工具批准 → 续跑

## 7. 部署

| 端 | 产物 | 部署 |
|---|---|---|
| Web | `apps/web/dist/*` | nginx 反代 chayuan-server；`try_files $uri /index.html` |
| Linux 桌面 | `apps/desktop/src-tauri/target/release/bundle/{deb,appimage}/*` | 自托管下载 |
| Windows 桌面 | `*.msi` / `*.exe` | 自托管 / 公司分发 |
| macOS 桌面 | `*.dmg` / `*.app` | 公证后分发 |
| Langfuse | docker-compose | 同 chayuan-server stack 或独立机 |
