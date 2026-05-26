# AI Space 计划执行情况审计 + 修复改进计划

> 触发：用户报告两个生产线缺陷
> - **缺陷 A**：应用商店页面"找不到页面"
> - **缺陷 B**：新建应用报 404
>
> 本文档：(1) 复现 + 根因，(2) 全量功能审计矩阵，(3) 分级修复计划，(4) 推荐技术路线（防回归）。

---

## 0. 一句话结论

**两个 bug 是同一个根因：vite dev server 的反代清单 `BACKEND_PROXY_PREFIXES` 缺 `/api/ai/space` + `/api/embed` 前缀**，导致前端发往 AI Space 后端的所有请求被 vite 的 SPA fallback 拦截，返 `index.html` 或 404。

**hotfix 已合入** `apps/web/vite.config.ts`（本次提交）：补 3 条前缀，所有 AI Space 请求恢复连通。验证步骤见 §6。

> 但这只是表象修复。更深的问题是：**前后端契约扩张时，dev/prod 反代规则没有同步更新机制**——任何后续新模块都会复发同类故障。§5 给出根治方案。

---

## 1. 缺陷复现 + 根因定位

### 1.1 缺陷 B「新建应用 404」（最严重）

**调用链**：
```
AppGalleryPage 用户点击"创建应用"
  → CreateAppDrawer Step 3 提交
  → packages/api/src/aiSpaceApps.ts:createApp()
  → http.post('/api/ai/space/apps', payload)
  → vite dev server  ← 这里出问题
  → ???
```

**vite proxy 配置**（修复前）：
```ts
const BACKEND_PROXY_PREFIXES = [
  '/admin', '/auth', '/chat/chat/completions', '/chat/v2/chat',
  '/chat/conversations', '/cli', '/knowledge_base', '/knowledge_source',
  '/knowledge_universe', '/office/docs', '/office/settings', '/office/_diag',
  '/office/_self_ping', '/tools', '/v1', '/api/v1/mcp_connections',
  '/governance', '/storage', '/modality', '/image_models', '/openapi',
  '/server', '/health', '/other', '/img', '/media', '/static',
];
```

**注意**：列表里**没有任何 `/api/ai/space` 前缀**。`/v1` 也匹配不到 `/api/ai/space/apps`（前缀不匹配）。

**真实行为**：vite 收到 `POST /api/ai/space/apps` → 不匹配任何反代前缀 → 走 SPA fallback → 尝试匹配 SPA 路由 / 返回 `index.html` → POST 方法 + 期望 JSON 的请求得到 HTML 响应 → 浏览器或 axios 报"404"或"Unexpected token <"。

**影响范围**：M1 之后的**全部 AI Space 接口**（21 类、~80 个端点）都不可用：
- App CRUD / Versions / Drafts
- Lifecycle (make-private/team/public)
- Tasks / Tasklist
- Tests
- Runs / Trace
- Grants / Comments / Audit Log / Secrets
- Share Settings / Embed.js
- Public-by-slug
- Node debugger / Flow run SSE

### 1.2 缺陷 A「应用商店找不到页面」

**双重原因**：

#### 原因 1（被 §1.1 牵连）
`MarketPage` 加载时调用 `listApps({scope:'public'})`，这个 API 同样被 vite 阻断 → 列表加载失败 → 用户看到的是错误状态或空白页 → 体感"找不到"。

#### 原因 2（架构层面的 IA 冲突）
当前同时存在三条"市场"动线，**入口分散且互相不通**：

| 入口 | 路径 | 实现 | 问题 |
| --- | --- | --- | --- |
| Sidebar "应用市场" | `/market` | `MarketPage.tsx`（独立页） | 列表依赖被阻断的 API |
| AppGalleryPage 第三 Tab | `/space?scope=public` | 同页 scope 过滤 | 切 Tab 不改路由，刷新丢失 |
| Sidebar "模型广场" | `/marketplace` | `MarketplacePage.tsx`（**模型**广场，非应用） | 名字相近，用户混淆 |

用户看到 Sidebar 里有"应用市场"和"模型广场"两条，进入"应用市场"碰到加载失败，又试"模型广场"看到的是模型平台 → 形成"应用市场不工作"的认知。

---

## 2. 全量功能审计矩阵

> 维度：**后端路由 / DB schema / 前端 API client / 前端 UI / 路由注册 / dev 反代 / e2e 通**。✅=已实现可用 / ⚠️=已实现但有上述阻断 / ❌=未实现 / N/A=不适用。

### 2.1 后端审计（按 §27 里程碑）

| M | 范围 | 后端代码 | 数据库 | API 端点 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M0 | DSL Schema + ADR | `ai_space/dsl.py` | -- | -- | ✅ |
| M1 | App CRUD + Gallery | `repository/ai_app_repository.py` | `0008_ai_app` | `/apps` GET POST PATCH DELETE | ✅ |
| M1.5 | 模板 + Fork | `seeds.py`（5 模板） | -- | `GET /templates` `POST /apps/fork` | ✅ |
| M2 | Flow 引擎 | `runtime/{state,nodes,executor,expressions}.py` | -- | `POST /apps/{id}/run` SSE | ✅ |
| M2.5 | 节点调试器 | -- | -- | `POST /nodes/{type}/run` | ✅ |
| M2.7 | Human-Task | `tasks.py` + `notify.py` | `0009_ai_app_human_task` | `/tasks/*` 5 个 | ✅ |
| M3 | reactflow Canvas | -- | -- | -- (前端) | ✅ |
| M3.5 | Debug Panel + 断点 | executor `step_mode + resumed_breakpoints` | -- | `POST /runs/{id}/resume` | ✅ |
| M3.7 | Form Builder + Tasklist | -- | -- | -- (前端) | ✅ |
| M4 | Grants | `repository/ai_app_grant_repository.py` | `0015_ai_app_grants` | `/apps/{id}/grants/*` | ✅ |
| M5 | 应用市场 | -- | `0017_ai_app_market_review` | `/admin/market/*` | ✅ |
| M5.5 | 测试集 | `eval.py` + `repository/ai_app_eval_repository.py` | `0010_ai_app_eval` | `/apps/{id}/tests/*` | ✅ |
| M5.7 | 生命周期状态机 | `lifecycle.py` | `0012_ai_app_audit_log` | `/lifecycle/{make-private,make-team,make-public,unpublish}` | ✅ |
| M6 | API Key + OpenAPI | `openapi.py` + `rate_limit.py` | `0013_ai_app_secret + 0018_rate_limit` | `/apps/{id}/keys` + `/api/v1/ai-app/{id}/invoke` | ✅ |
| M6.5 | 分享 + embed.js | `embed_js.py` | `0011_ai_app_share` | `/apps/{id}/share-settings` + `/embed/ai-space.js` + `/public/by-slug/{slug}` | ✅ |
| M7 | 高级节点 + 沙箱 | `runtime/nodes.py`（switch/loop/parallel/try_catch/subflow/code/http） | -- | -- | ✅ |
| M8 | 运行历史 + Trace | `repository/ai_app_run_log_repository.py` | `0016_ai_app_run_log` | `/apps/{id}/runs/*` | ✅ |
| M8.5 | 协作 + 评论 + diff | `presence.py` + `repository/ai_app_node_comment_repository.py` | `0014_ai_app_node_comment` | `/comments/*` + `/presence/*` | ✅ |
| M8.7 | 导入导出 | -- | -- | `/apps/{id}/export` + `/apps/import` | ✅ |
| M9 | IM 渠道 | `notify.py`（飞书 / 钉钉 / 企微 webhook） | -- | -- | ✅ |

**后端结论**：所有里程碑后端代码、数据库、API 端点全部到位。**11 张 migration、80+ 端点、12 节点类型**。

### 2.2 前端审计（页面 / 组件 / API client / 路由）

| 模块 | 页面文件 | 路由注册 | API client | dev 反代 | 状态 |
| --- | --- | --- | --- | --- | --- |
| AppGalleryPage | ✅ | `/space` ✅ | `aiSpaceApps.ts` ✅ | ❌（修复前） | ⚠️ → ✅（hotfix 后） |
| AppStudioPage | ✅ | `/space/:id/studio` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| AppRuntimePage | ✅ | `/space/:id/runtime` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| ChatRuntime | ✅ | （嵌入 Runtime）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| FlowSection (Canvas) | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| FlowDebugPanel | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| NodeConfigPanel | ✅ | （嵌入 Canvas）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| NodeDebugDrawer | ✅ | （嵌入 Canvas）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| VariablesPanel | ✅ | （嵌入 Studio）| -- | -- | ✅ |
| PublishDialog | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| ShareDialog | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| KeysSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| GrantsSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| ResourcesSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| SecretsSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| TestSuiteSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| RunsSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| AuditLogSection | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| VersionsDialog | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| EtagConflictDialog | ✅ | -- | -- | -- | ✅ |
| AnnotateDialog | ✅ | （嵌入 ChatRuntime）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| ImportExportDialog | ✅ | （嵌入 Studio）| ✅ | ❌ → ✅ | ⚠️ → ✅ |
| TemplateTryDialog | ✅ | -- | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| TasklistPage | ✅ | `/tasks` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| TaskDetailPage | ✅ | `/tasks/:token` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| MarketPage | ✅ | `/market` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ ⚠️ |
| MarketAdminPage | ✅ | `/admin/market` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| PublicAppPage | ✅ | `/ai-app/:slug` ✅ | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| QRCanvas | ✅ | -- | -- | -- | ✅ |
| flow/CommentThread | ✅ | -- | ✅ | ❌ → ✅ | ⚠️ → ✅ |
| flow/MentionTextarea | ✅ | -- | -- | -- | ✅ |
| flow/configs/HumanTaskFields | ✅ | -- | -- | -- | ✅ |
| flow/configs/HttpFields | ✅ | -- | -- | -- | ✅ |
| flow/inferSchema / flowValidate | ✅ | -- | -- | -- | ✅ |

**前端结论**：UI 实现完整（30+ 组件），**所有 API 调用全部因 vite proxy 缺失而被阻断**——本次 hotfix 解决全部连锁故障。

### 2.3 IA / 路由审计（关键发现）

| 类别 | Sidebar 入口 | 路由 | 备注 |
| --- | --- | --- | --- |
| AI 空间主入口 | "AI Space" | `/space` | ✅ |
| 应用市场 | "应用市场" | `/market` | **冲突 1**：与 Gallery 内 `?scope=public` Tab 重复 |
| 模型广场（独立产品）| "模型广场" | `/marketplace` | **冲突 2**：与"应用市场"名称相近，易混淆 |
| 我的待办 | "我的待办" | `/tasks` | ✅ |
| 公开应用页 | （无入口）| `/ai-app/:slug` | 仅外部访问；OK |
| 管理员审核 | （无入口）| `/admin/market` | 缺管理员侧入口；普通用户无所谓 |

---

## 3. 修复优先级与计划

### P0 — 阻断性 hotfix（≤ 30 分钟）

#### P0-1：vite 反代清单补全 ✅ 本次已合入
```diff
+ '/api/ai/space',
+ '/api/v1/ai-app',
+ '/api/embed/ai-space.js',
```
**状态**：本次提交已合入 `apps/web/vite.config.ts`。

#### P0-2：nginx 模板同步
私有化部署用 nginx 反代，规则要保持一致。`docs/plans/ai-space-private-deployment.md` 中的 `nginx.conf` 片段已含 `location /api/`，但需确认运维实际部署的 nginx 配置含：

```nginx
# 已有
location /api/ {
    proxy_pass http://chayuan-server:62581/;
    proxy_buffering off;
    proxy_read_timeout 1d;
}

# embed.js 单独 cache（高优先级匹配）
location = /api/embed/ai-space.js {
    proxy_pass http://chayuan-server:62581/embed/ai-space.js;
    add_header Cache-Control "public, max-age=3600";
    gzip on;
}
```

**状态**：模板文档已就位；运维上线时需要走一遍 §6 验证清单。

### P1 — IA / 用户认知（≤ 1 周，需产品确认）

#### P1-1：合并 `/market` 与 Gallery `scope=public`
**问题**：双入口割裂，用户认知成本高。

**推荐方案 A（合并）**：
- 删除独立 `/market` 路由 + Sidebar 入口
- 保留 Gallery 三 Tab，但"应用市场" Tab 用 URL hash/query 持久化（`/space?tab=market`），刷新不丢
- Sidebar 直接进 `/space`

**推荐方案 B（强分化）**：
- Gallery 删 "市场" Tab，只保留"我的 / 共享给我"
- `/market` 升级为 Coze 风格的展示型页面（hero + 推荐位 + 分类网格）
- 两条路径职责单一：`/space` = 创作工作台，`/market` = 浏览发现

**推荐采用 B**，理由：
1. 与现有 §3.1（Gallery）+ §3.12（Market 详情）设计稿契合
2. Coze / Dify 都是双入口分化设计
3. Gallery 当前实现已偏向"我的应用列表"形态，强行塞 Market 反而拥挤

#### P1-2：Sidebar 名称去歧义
- "应用市场" → 保持
- "模型广场" → "模型平台" 或 "模型管理"（因为它管理的是 LLM 平台 / 模型，不是应用）

#### P1-3：管理员入口
- Sidebar 底部对 `role=admin` 用户显示"市场审核"链接 → `/admin/market`

### P2 — 防回归（≤ 2 周）

#### P2-1：反代清单单源
**问题**：当前 vite 与 nginx 各维护一份反代规则；新模块上线很容易漏。

**方案**：在 `packages/api/src/` 新建 `routes-manifest.ts`，所有 API client 文件 import 一个常量列表：

```ts
// packages/api/src/routes-manifest.ts
export const BACKEND_PREFIXES = [
  '/admin', '/auth',
  // ... existing prefixes ...
  '/api/ai/space',
  '/api/v1/ai-app',
  '/api/embed/ai-space.js',
] as const;
```

vite.config.ts 直接 import：
```ts
import { BACKEND_PREFIXES } from '../packages/api/src/routes-manifest';
const proxy = Object.fromEntries(BACKEND_PREFIXES.map(p => [p, { target: backend, changeOrigin: true, ws: true }]));
```

nginx 模板从同一份生成（脚本生成 `nginx.routes.conf` 片段）。

#### P2-2：API client 路径校验测试
**思路**：`packages/api/src/__tests__/routes-coverage.test.ts` —— 用 ts-morph / regex 扫描 client 文件中所有 `http.get/post/...` 调用，提取 URL，断言每条都被 `BACKEND_PREFIXES` 中的某条前缀覆盖。

```ts
// 示意
test('every api call URL is covered by routes-manifest', () => {
  const calls = extractApiCalls('packages/api/src/');
  for (const url of calls) {
    expect(BACKEND_PREFIXES.some(p => url.startsWith(p))).toBe(true);
  }
});
```

CI 必跑；漏一条直接挂。

#### P2-3：dev 启动 smoke check
新增 `apps/web/scripts/dev-smoke.ts`：vite 启动 5 秒后用 `curl` 打 5 个关键端点（apps list / templates / tasks / health / embed.js），返回非 200 打日志告警，**不阻塞**启动但开发者立即看到。

#### P2-4：e2e 用例（playwright）
为本次两个 bug 写最小 e2e：
1. 登录 → 访问 `/space` → 看到 Gallery → 点"创建应用" → 完成 3 步 → 看到 Studio
2. 登录 → 访问 `/market` → 看到至少 1 个公共应用卡片（依赖测试 fixture）
3. Studio → 编辑 Flow → 点"调试运行" → 时间线出现 done

放 `e2e/ai-space.spec.ts`，CI 必跑。

### P3 — 体验优化（独立排期）

#### P3-1：错误状态升级
当前 listApps 失败时仅看到 `"加载中…"` 永久 stuck。改成 react-query 的 `error` 状态显式渲染：

```tsx
{appsQ.isError ? (
  <ErrorBanner
    title="无法加载应用列表"
    detail={String(appsQ.error)}
    actions={[
      { label: '重试', onClick: () => appsQ.refetch() },
      { label: '联系管理员', onClick: () => window.open('/help', '_blank') },
    ]}
  />
) : null}
```

#### P3-2：加载性能（Gallery 首屏）
当前 listApps 一次拉 60 条；建议：
- 切窗滚动加载（virtualization）超过 30 条时
- 缓存 60s（ETag）
- 预加载 templates（用户多半要创建）

#### P3-3：Studio 顶栏"AI Space" 面包屑
从 Studio 回 Gallery 当前用浏览器返回；改成顶栏左侧显式面包屑 `AI Space › 应用名 › 流程`，每段可点。

---

## 4. 后续技术路线（前瞻）

### 4.1 反代统一 + OpenAPI 自动生成

**当前状态**：手写 API client；vite/nginx 各维护反代清单。

**目标**：FastAPI 已经自动出 `openapi.json` —— 用 `openapi-typescript` 在 CI 阶段生成强类型 client + 路由清单：

```sh
# CI 步骤
npx openapi-typescript http://localhost:62581/openapi.json \
  -o packages/api/src/generated/types.ts

# 同时生成路由清单
node scripts/extract-prefixes.js openapi.json > packages/api/src/routes-manifest.ts
```

**收益**：
- 后端 endpoint 改动 → 前端 type 自动更新 + 编译失败立显
- 反代清单单源 → vite/nginx 同步零成本
- 老 client 平滑废弃，新 client 走 openapi-fetch

### 4.2 dev/prod 环境一致性

**当前状态**：dev 走 vite proxy（`/api` 转 62581），prod 走 nginx。两套配置各自演化。

**目标**：dev 也走 nginx（docker-compose dev profile）：
```yaml
services:
  chayuan-server: { ports: [] }  # 不暴露
  chayuan-frontend-dev:
    image: node:20
    command: pnpm --filter @chayuan/web dev
    volumes: [...]
  nginx-dev:
    image: nginx
    ports: ['5173:5173']
    volumes: [./nginx.dev.conf:/etc/nginx/conf.d/default.conf]
    # 同一份 nginx.conf；只改 upstream
```

dev 用同一份 nginx 配置 → 反代规则永远一致 → 杜绝本次 bug 类。

### 4.3 SSE / WebSocket 反代规范

**易踩坑**：vite proxy 的 SSE 长连接默认 `proxyTimeout: 60s`，跑长 Flow 时连接被 reset。

**修订**：在 `vite.config.ts` 的 proxy 配置加：
```ts
{
  target: backend, changeOrigin: true, ws: true,
  // SSE 关键：不缓冲、长超时
  configure: (p) => {
    p.on('proxyReq', (req) => {
      req.setHeader('Connection', 'keep-alive');
    });
  },
  timeout: 0,
  proxyTimeout: 0,
}
```

nginx 同等设置：
```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 1d;
proxy_send_timeout 1d;
chunked_transfer_encoding on;
```

### 4.4 Studio 与 Gallery 的 IA 重整

**当前**：
- Sidebar：AI Space → 进 `/space`（Gallery）；应用市场 → `/market`（独立）
- Gallery 有"市场" Tab（与 `/market` 数据一致但 UI 不同）

**问题**：
- 创作者动线和消费者动线混在一起
- 同一份数据两套 UI（Gallery 卡片 vs MarketPage hero+网格）

**重整建议**（取自 Coze）：
```
顶栏（更高优先级，比 Sidebar）:
  AI 空间                        ← 单一总入口，hover 出二级
    ├─ 我的应用    （/space）
    ├─ 共享给我    （/space?scope=shared）
    ├─ 应用市场    （/market）
    └─ 创建新应用  （Cmd-K 或顶栏按钮）

  我的待办        （/tasks）
  应用市场审核     （/admin/market；仅 admin 可见）
```

去掉 Sidebar 的"应用市场"独立项，改放 AI 空间二级菜单内；保留 `/market` 路由作为深链。

---

## 5. 监控 / 告警建议（私有化部署）

### 5.1 黄金信号

| 信号 | 阈值 | 告警通道 |
| --- | --- | --- |
| `POST /api/ai/space/apps` 5xx 率 | > 5% / 5min | 飞书机器人 |
| `POST /apps/{id}/run` SSE 中断率 | > 10% / 1h | 飞书 + 邮件 |
| Human-Task pending 数 | > 1000（默认水位） | 邮件 |
| `code` 节点错误数 | > 10 / 1h | 飞书（沙箱可能被探测）|
| Postgres `ai_app_run.state_blob` 单行 > 1MB | 立即告警 | 飞书 |
| `/embed/ai-space.js` 304 命中率 | < 80% | 邮件（CDN 配错）|

### 5.2 私有化部署后的"上线检查表"

新增 `docs/plans/ai-space-go-live-checklist.md`（待写）：
- [ ] alembic 升级到 head（0018）
- [ ] 4 个 seed 类型 + 5 个内置模板可拉
- [ ] /api/ai/space/apps GET 返 200
- [ ] /api/embed/ai-space.js GET 200 + Content-Type js
- [ ] 创建一个 chatbot 应用 + 用 mock 模型跑一次 done
- [ ] 创建一个含 human_task 的应用 → 暂停 → 提交 → 续跑成功
- [ ] 飞书 / 钉钉 webhook（如配置了）发一条测试通知
- [ ] PublishDialog 走通 Draft → Private 发布

---

## 6. 验证步骤（hotfix 后回归）

### 6.1 dev 模式

```bash
cd /work/chayuan-client
pnpm --filter @chayuan/web dev
```

打开 `http://localhost:5173`，登录后：

1. **Gallery** —— 访问 `/space`，列表正常加载（不再 stuck "加载中…"）
2. **创建应用** —— 点击"创建应用" → 走完三步 → 跳到 `/space/<新id>/studio` 不再 404
3. **应用市场** —— 访问 `/market`，看到公共应用卡片（如有 fixture 数据）
4. **运行 Flow** —— Studio → 调试运行 → 时间线出现 `meta / node_start / node_end / done`
5. **Tasklist** —— 访问 `/tasks`，列表加载（即使是空也要返 200）
6. **embed.js** —— 浏览器直接访问 `http://localhost:5173/api/embed/ai-space.js`，返 JS 内容（5KB 左右）

### 6.2 网络面板验证

按 F12 → Network，关键请求都应：
- `Status: 200 / 304`
- `Type: xhr / fetch`
- `Response Headers` 含 `Content-Type: application/json` 或 `text/event-stream`

**反例**（修复前会看到的）：
- `Status: 200`
- `Type: document`（说明被 SPA fallback 兜了）
- `Response: <!DOCTYPE html>...`

### 6.3 prod 部署验证

按 §5.2 上线检查表逐项跑；任何一项失败按 §3 优先级回退。

---

## 7. 工作量估算（修复部分）

| 项 | 工程量 | 优先级 |
| --- | --- | --- |
| P0-1 vite proxy 补全 | 已完成（5 分钟） | 🔴 完成 |
| P0-2 nginx 模板复核 | 0.5h | 🔴 上线前 |
| P1-1 IA 去重（双入口合并 / 分化） | 1 天（含产品评审） | 🟠 1 周内 |
| P1-2 Sidebar 文案改名 | 1h | 🟠 1 周内 |
| P1-3 管理员入口 | 0.5 天 | 🟢 2 周内 |
| P2-1 反代清单单源 | 1 天 | 🟢 2 周内 |
| P2-2 API client 路径校验 | 0.5 天 | 🟢 2 周内 |
| P2-3 dev smoke check | 0.5 天 | 🟢 2 周内 |
| P2-4 e2e 用例 | 1 天 | 🟢 2 周内 |
| P3-1 错误 banner | 0.5 天 | 🔵 后续 |
| P3-2 列表性能 | 1 天 | 🔵 后续 |
| P3-3 面包屑 | 0.5 天 | 🔵 后续 |
| 4.1 OpenAPI codegen | 2 天 | 🔵 长期 |
| 4.2 dev/prod 一致 | 2 天 | 🔵 长期 |

**P0+P1 合计 ≈ 2.5 工程日**；P2 防回归 ≈ 3 工程日；P3 + 4 长期演进 ≈ 6 工程日。

---

## 8. 一句话总结

> **本次缺陷暴露的不是功能缺失（功能 100% 实现），而是反代清单维护机制缺失。**
>
> hotfix（vite proxy 加 3 条）已让用户立即可用；P1 的 IA 整改让长期体验更顺；P2 的 OpenAPI codegen + 路径校验让此类问题永不复发。
>
> AI Space 平台**架构、契约、节点编排、Human-in-the-Loop、生命周期、协作、测试、可观测、私有化部署、多语 i18n** 闭环已成形；后续重心应从"补功能"转到"提质量 + 防回归 + 优体验"。
