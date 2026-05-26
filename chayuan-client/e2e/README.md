# e2e 黄金路径

5 条 Playwright 用例覆盖核心交互；后端流量全部通过 `helpers/mockBackend.ts` 拦截，
**无需依赖真实 chayuan-server 与 Langfuse**。

| # | 文件 | 覆盖 |
|---|---|---|
| 1 | `01-login-and-send.spec.ts` | 登录 → 流式发消息 → 用户反馈 score 闭环 |
| 2 | `02-tool-hil.spec.ts` | 工具选择 → 收到 `interrupt` → 批准 → resume → 续跑 |
| 3 | `03-edit-and-regenerate.spec.ts` | 重新生成（hook regenerate 路径） |
| 4 | `04-network-failure.spec.ts` | 503 报错 → online 事件触发 auto-retry |
| 5 | `05-artifact-promote.spec.ts` | mermaid 代码块自动 promote 到 Artifact 面板 |

## 启动

```bash
# 安装浏览器（一次）
pnpm exec playwright install chromium

# 开起来 web dev server 并跑（默认）
pnpm test:e2e

# 复用已运行的 vite dev server
E2E_NO_SERVER=1 pnpm test:e2e

# 指定其他 baseURL（例如生产构建预览）
E2E_BASE_URL=http://127.0.0.1:4173 pnpm test:e2e
```

## Mock 内置规则

- 默认账号 `tester / test1234`，role=admin（用例 4-5 需要 admin 看 artifact 触发）
- `/v1/models` 返回 `claude-opus-4-7`
- `/tools?enabled=true` 返回 `web_search`
- KB / MCP / conversations 均空数组
- `/chat/v2/chat` 默认返回 `Hello from mock`；可在每个 spec 用 `state.chatStreamBody = ...` 覆盖
- `/chat/feedback` 与 `/chat/v2/chat/resume` 抓取请求体到 `state.captured*` 供断言
- Langfuse `/api/public/ingestion**` 返回 207，避免遥测干扰

## 桌面端 e2e

桌面 Tauri 通过 `tauri-driver`（基于 WebDriver） 跑，与 Web 平行；
本仓未配置，建议另起 `e2e-desktop/` 子目录维护。
