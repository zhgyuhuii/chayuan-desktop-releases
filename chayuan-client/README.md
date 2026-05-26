# 察元 AI 客户端

> Tauri 2 + React 19 + Langfuse · 一码双发（桌面 + Web）  
> 后端 **chayuan-server**（FastAPI）源码已并入本仓 `server/`，与前端同一仓库维护。

## 仓库结构

```
chayuan-client/
├── apps/
│   ├── desktop/          # Tauri 2 + React renderer
│   └── web/              # 纯浏览器构建（Vite）
├── packages/
│   ├── platform-shared/  # PAL 接口契约
│   ├── platform-tauri/   # 桌面平台实现
│   ├── platform-web/     # Web 平台实现
│   ├── api/              # 类型化 HTTP 客户端 + 401 刷新 + BizError
│   ├── transport/        # SSE 解析（worker）+ ai SDK custom transport
│   ├── observability/    # Langfuse 客户端 + 离线 outbox
│   ├── ui/               # shadcn-style 原子组件
│   └── app/              # 业务大脑：bootstrap、routes、features、stores
├── server/               # 察元 AI 助手服务（Python / Poetry，原 chayuan-server）
│   ├── libs/chayuan-server/   # 可安装包与 `chayuan` CLI
│   ├── docker/           # compose 与生产模板
│   └── docs/             # 后端开发 / 部署文档
├── tools/
│   └── api-codegen.mjs   # 从运行中的 FastAPI `/openapi.json` 生成类型
└── e2e/                  # Playwright 黄金路径
```

依赖方向：`apps/* → packages/app → ui / api / transport / observability / platform-shared → platform-{tauri,web}`。前端与 `server/` 仅通过 HTTP / OpenAPI 契约耦合，无 Node↔Python 代码引用。

## 第一次跑起来

```bash
# 0. Node 22+ / pnpm 9+
corepack enable
corepack prepare pnpm@9.15.0 --activate

# 1. 装依赖
pnpm install

# 2. 准备环境变量
cp .env.example .env
cp apps/web/.env.example apps/web/.env
cp apps/desktop/.env.example apps/desktop/.env

# 3. 启动后端（默认 127.0.0.1:62581；需 Python 3.10–3.12 + Poetry）
cd server
poetry -C libs/chayuan-server install --with lint,test
# 首次：poetry -C libs/chayuan-server run chayuan init
poetry -C libs/chayuan-server run chayuan start -a
cd ..
#    详见 server/docs/contributing/README_dev.md
#    可选：在 server/docker 下 docker compose 启 Langfuse / 全栈

# 4. 拉 OpenAPI 类型
API_BASE=http://127.0.0.1:62581 pnpm gen:api

# 5. Web 端（浏览器）
pnpm dev:web
# → http://localhost:5173

# 6. 桌面端（Tauri）— 需要 rustup + 平台依赖
pnpm dev:desktop
```

## 关键脚本

| 命令 | 作用 |
|---|---|
| `pnpm dev:web` | Web 端开发服务器 |
| `pnpm dev:desktop` | Tauri 桌面端开发模式 |
| `pnpm build:web` | Web 静态构建 → `apps/web/dist` |
| `pnpm build:desktop` | Tauri 桌面打包 |
| `pnpm gen:api` | 从后端 OpenAPI 生成 types |
| `pnpm typecheck` | 全仓 TS 类型检查 |
| `pnpm lint` / `pnpm lint:fix` | Biome 代码检查 |
| `pnpm test` | Vitest 单测 |
| `pnpm test:e2e` | Playwright 端到端 |

## 架构关键点

### Platform Abstraction Layer (PAL)

业务代码 **不直接** import `@tauri-apps/*` 或 browser API；
全部走 `getPlatform().*`。两份实现（`platform-tauri` / `platform-web`）由
`apps/{desktop,web}/src/main.tsx` 在启动时 `setPlatform()` 注入。

- `secure`：桌面 = Stronghold（OS keychain）；Web = sessionStorage 兜底
- `db`：桌面 = SQLite + FTS5；Web = Dexie/IndexedDB（带 SQL 子集适配）
- `net.fetch / sse`：桌面 = Tauri http plugin（绕 CORS）；Web = window.fetch
- `shortcut/tray/capture/updater`：仅桌面；UI 必须 capability check

### 链路追踪

每发一条消息：

```
前端                           后端                          Langfuse
─────                         ─────                         ─────────
uuid traceId                                              
  │                                                       
  ├─ X-Trace-Id 头 ─────────► /chat/v2/chat                
  │                              │                         
  │                              ├─ langfuse.trace(id=…)   
  │                              ├─ generation             
  │                              └─ tool span / kb span    
  │                                                       
  └─ event: chat.first_token ────────────────────────────► event
  └─ event: chat.complete    ────────────────────────────► event
  └─ POST /chat/feedback  → 后端 langfuse.score ─────────► score
```

详见 `packages/observability/src/langfuse.ts`。

### 性能 / 并发

- **SSE 解析在 Worker**：`@chayuan/transport` 提供 `sse-worker.ts`，主线程通过 `runSSE({ worker })` 一行接通；流量大时 fps 不掉。
- **TanStack Query 去抖 + inflight 去重**：所有 catalog（tools/mcp/kb/models）查询自动批合。
- **Zustand selector 订阅**：composer 的 draft 改动只 re-render 输入框，不触发整个 Shell。
- **markdown 增量渲染**：稳定段 `React.memo` 化，仅尾段每 token 重渲。
- **outbox 队列**：Langfuse 上传失败入 SQLite/IndexedDB，30s 后台 flush，不阻塞业务。

### 安全

- Token 走 OS keychain（桌面）/ httpOnly cookie（Web 推荐路径）。
- DOMPurify 二次净化所有 markdown HTML；URL scheme 白名单。
- Tauri capabilities/default.json 显式开权限，最小特权原则。
- CSP 白名单后端 + Langfuse 域。

## 与 chayuan-server 的契约对接

| 前端方法 | 后端端点 |
|---|---|
| `auth.login(u, p)` | `POST /auth/login` |
| `auth.me()` | `GET /auth/me` |
| `models.list()` | `GET /v1/models` |
| `conversations.list()` | `GET /chat/conversations` |
| `tools.listEnabled()` | `GET /tools?enabled=true` |
| `mcp.list()` | `GET /api/v1/mcp_connections/?enabled=true` |
| `kb.list()` | `GET /knowledge_base/list_knowledge_bases` |
| `kb.uploadTempDocs(files)` | `POST /knowledge_base/upload_temp_docs` |
| `transport.chat(req)` | `POST /chat/v2/chat` (SSE) |
| `transport.resumeAndContinue(...)` | `POST /chat/v2/chat/resume` + 重发 v2 |
| `feedback.submit(...)` | `POST /chat/feedback` → Langfuse score |

类型由 `pnpm gen:api` 自动生成；手写包装层在 `packages/api/src/endpoints.ts`，**契约漂移会编译失败**。

## 路线图

- [x] M0 monorepo + PAL + bootstrap
- [x] M1 transport + observability + login + chat 闭环
- [ ] M2 KB / 工具 / MCP UI
- [ ] M3 HIL Interrupt 完整 UI（已有底层 API）
- [ ] M4 多模态 + Artifact 面板
- [ ] M5 Admin（trace explorer / eval / prompt）
- [ ] M6 i18n + 设置 + 隐私开关
- [ ] M7 e2e + Tauri 打包 + 自动更新
