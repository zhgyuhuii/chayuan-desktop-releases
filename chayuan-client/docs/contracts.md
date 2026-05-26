# 前后端 Contract 对齐

> 本文记录重构涉及的 5 个领域(Auth / Chat / KB / Marketplace / AI Space)的接口现状、缺口、处理方案。
> 与 `chayuan-server@<HEAD>` 对齐;未来若后端变更,**先改这里再改 client 代码**。

---

## 1. Auth(JWT 用户名密码)

| 端点 | 状态 | 备注 |
|---|---|---|
| `POST /auth/login` | ✅ 后端 `auth_routes.py` | 返 access + refresh token |
| `POST /auth/refresh` | ✅ | 401 单飞刷新已就位 |
| `POST /auth/logout` | ✅ | 黑名单 token |
| `POST /auth/register` | ✅ | admin 控制是否开放 |
| 手机号 / SMS | ❌ | **不做**(ADR-03) |

前端处理:`packages/api/src/auth.ts` 已就位;M4 仅做 UI 重塑。

---

## 2. Chat(LangGraph)

| 端点 | 状态 | 备注 |
|---|---|---|
| `POST /chat/v2/chat` (SSE) | ✅ `chat_routes.py` | LangGraph 主入口 |
| `POST /chat/v2/chat/resume` | ✅ | HIL 续跑 |
| `POST /chat/chat/completions` (SSE) | ✅ 老 OpenAI 兼容 | 保留作降级 |
| `POST /chat/feedback` | ✅ | thumbs up/down → Langfuse score |
| `GET /v1/models` | ✅ `openai_routes.py` `list_models()` | 返 `{ id, object, owned_by, platform_name, created }` |

前端处理:`packages/transport` 已就位;M3 模型广场直接消费 `/v1/models`。

### 模型广场扩展点

参考图的"推荐 / 厂商分组 / 管理"无对应后端端点。前端策略:

| UI | 数据来源 |
|---|---|
| 厂商 Tab | 按 `platform_name` 分组 |
| 推荐 Tab | 前端常量 `RECOMMENDED_MODELS`(置于 `packages/api/src/marketplace/recommended.ts`) |
| Hero 卡 | `useComposerStore.modelId` 当前默认模型 |
| logo | 复用 `chayuan-server` 的 `/img/model_logos/logos-manifest.json`(同源 / 直连双 candidate) |
| "管理"按钮 | 跳 `/settings#models`(开关某模型),**该开关后端待补**;M3 仅做 UI 占位 |

---

## 3. Knowledge Base

| 端点 | 状态 | 备注 |
|---|---|---|
| `GET /knowledge_base/list_knowledge_bases` | ✅ | 列出可见 KB(含 ACL) |
| `POST /knowledge_base/create_knowledge_base` | ✅ | |
| `POST /knowledge_base/upload_docs` | ✅ + Arq 队列 | 异步入库 |
| `POST /knowledge_base/local_kb/{name}/chat/completions` (SSE) | ✅ | KB 流式问答 |
| `POST /knowledge_base/temp_kb/...` `search_engine/...` | ✅ | 临时 KB / 联网搜索 |
| `GET /knowledge_base/list_files` | ✅ | KB 文件列表 |
| `DELETE /knowledge_base/delete_docs` | ✅ | |

前端处理:`packages/api/src/kb.ts` 已就位;M3 重写 `KbBoard` UI,API 0 改动。

---

## 4. AI Space（应用编排平台）

> 历史版本（伪服务 + fixture）已被 M1–M9 的真实后端**全量替代**。`ai-space.ts` 仅留作老路由兼容；新代码统一走 `aiSpaceApps.ts` / `aiSpaceTasks.ts` / `aiSpaceEval.ts`。
>
> 设计文档：`docs/plans/ai-space-orchestration.md`（§1–§28）+ `docs/plans/ai-space-ui-design.md`。
> ADR：`docs/adr/0007-ai-app-flow-dsl.md`（DSL v1 与节点白名单）。

### 4.1 后端模块

```
chayuan/server/ai_space/
  __init__.py
  dsl.py              # Pydantic DSL schema + 节点白名单 + 环检测
  seeds.py            # 4 类型默认 DSL + 5 内置模板
  notify.py           # Notifier 抽象（inapp / email / 飞书 / 钉钉 / 企微）
  tasks.py            # Human-Task 业务规则（assignee 解析 + ISO 8601 时长）
  eval.py             # 测试集执行 + 期望匹配（4 类型）
  openapi.py          # OpenAPI 兼容 invoke 入口辅助
  presence.py         # 协作 v1：当前编辑者心跳
  rate_limit.py       # 限流（QPS / 日 token / 并发上限）
  deadline_worker.py  # Human-Task 截止 / 提醒后台轮询
  embed_js.py         # /embed/ai-space.js 静态 SDK
  runtime/
    state.py          # VariableSpace（inputs/state/persona/env）+ 模板渲染
    expressions.py    # IF / Switch mini-lang AST 严格白名单求值
    nodes.py          # 12 节点：input/output/llm/kb_retrieve/tool_call/if/
                      #          switch/loop/parallel/try_catch/subflow/
                      #          code(沙箱)/http(allowlist)/human_task
    executor.py       # DAG walker；step-limit=200；retry/timeout/fallback；
                      #            PauseSignal 持久化挂起；resume 续跑
```

### 4.2 数据库表（migrations 0008–0018）

| Migration | 表 |
| --- | --- |
| 0008_ai_app | `ai_app / ai_app_version / ai_app_key` |
| 0009_ai_app_human_task | `ai_app_run / ai_app_task / ai_app_task_event` |
| 0010_ai_app_eval | `ai_app_test_case / ai_app_test_run` |
| 0011_ai_app_share | 给 ai_app 加 `slug + share_settings` |
| 0012_ai_app_audit_log | `ai_app_audit_log` |
| 0013_ai_app_secret | `ai_app_secret`（vault://name/key 解析） |
| 0014_ai_app_node_comment | `ai_app_node_comment` |
| 0015_ai_app_grants | `ai_app_grant`（user/group/role × scope） |
| 0016_ai_app_run_log | 给 ai_app_run 加事件 trace blob |
| 0017_ai_app_market_review | `ai_app_market_review`（公开市场审核） |
| 0018_ai_app_rate_limit | `ai_app_rate_limit_record`（按 ApiKey 限流计数） |

### 4.3 路由清单（按主题分组；prefix `/api/ai/space`）

**App CRUD / 列表 / Fork**
```
GET    /apps?scope=mine|shared|public|all&keyword=&type=&lifecycle=draft|private|team|public
POST   /apps                                # 含 template_id（模板创建）
POST   /apps/fork                           # source_app_id + new name
GET    /apps/{id}
PATCH  /apps/{id}
DELETE /apps/{id}                           # 软删除 30 天保留
```

**版本 + 草稿（ETag 乐观锁）**
```
GET    /apps/{id}/versions
GET    /apps/{id}/draft
PUT    /apps/{id}/draft                     # 409 → {reason:'etag_conflict', current}
POST   /apps/{id}/versions/publish
```

**生命周期 + 审计**
```
POST   /apps/{id}/lifecycle/visibility       # 通用（旧）
POST   /apps/{id}/lifecycle/make-private
POST   /apps/{id}/lifecycle/make-team        # body: {group_id}
POST   /apps/{id}/lifecycle/make-public
POST   /apps/{id}/lifecycle/unpublish
GET    /apps/{id}/audit-log
```

**资源授权（grants）**
```
GET    /apps/{id}/grants
POST   /apps/{id}/grants                     # principal + scope
DELETE /apps/{id}/grants/{grant_id}
```

**API Key（OpenAPI 兼容）**
```
GET    /apps/{id}/keys
POST   /apps/{id}/keys                       # is_publishable / referer_allowlist / scopes
DELETE /apps/{id}/keys/{key_id}
POST   /api/v1/ai-app/{id}/invoke            # OpenAI 兼容 SSE；Bearer ak[.sk] / publishable_key + Origin
```

**Secrets（vault）**
```
GET    /apps/{id}/secrets
PUT    /apps/{id}/secrets/{key}
DELETE /apps/{id}/secrets/{key}
```

**Flow Run（SSE）**
```
POST   /apps/{id}/run                        # SSE 流；owner 默认走 draft
POST   /nodes/{type}/run                     # 单节点调试，不依赖 Flow
```

**Human-Task（人机协同）**
```
GET    /tasks?scope=assigned_to_me|created_by_me|group_inbox|done
GET    /tasks/{token}
POST   /tasks/{token}/claim
POST   /tasks/{token}/complete               # SSE 续跑
POST   /tasks/{token}/cancel
```

**测试集（M5.5）**
```
GET    /apps/{id}/tests
POST   /apps/{id}/tests
PATCH  /apps/{id}/tests/{case_id}
DELETE /apps/{id}/tests/{case_id}
POST   /apps/{id}/tests/run
GET    /apps/{id}/tests/runs
```

**协作 v1（M8.5）**
```
GET    /apps/{id}/comments?node_id=
GET    /apps/{id}/comments/counts
POST   /apps/{id}/comments
PATCH  /apps/{id}/comments/{id}
DELETE /apps/{id}/comments/{id}
GET    /apps/{id}/presence
POST   /apps/{id}/presence/heartbeat
```

**运行历史 / 容量看板（M8）**
```
GET    /apps/{id}/runs?status=&limit=
GET    /apps/{id}/runs/{run_id}
```

**模板 + 应用市场**
```
GET    /templates                            # 5 内置模板
POST   /apps/{id}/market/submit              # 提交审核
GET    /admin/market/pending                 # 管理员审核队列
POST   /admin/market/{id}/approve
POST   /admin/market/{id}/reject
```

**分享（M6.5）**
```
PUT    /apps/{id}/share-settings             # slug + access_code + expires_at + referer_allowlist + embed_enabled
GET    /api/ai/space/public/by-slug/{slug}   # 无需登录；可带 ?code=
GET    /embed/ai-space.js                    # 静态 SDK，浮动球嵌入
```

**导入 / 导出（M8.7）**
```
GET    /apps/{id}/export                     # 含 manifest/app/version/grants(ref)/tests；secrets 仅留 key 名
POST   /apps/import                          # 返回 resource_mapping_required 列表
```

### 4.4 前端 API 客户端

```
packages/api/src/aiSpaceApps.ts   App / Version / Key / Lifecycle / Share / Run(SSE)
packages/api/src/aiSpaceTasks.ts  Human-Task / Tasklist / debugNode
packages/api/src/aiSpaceEval.ts   测试集 / 测试批次
```

`ai-space.ts`（旧 fixture-fallback 模块）保留以兼容老 SpacePage 旧路径；新代码导入 `aiSpaceApps` 等。

### 4.5 SSE 事件帧（与 `/chat/v2/chat` 同形）

```
{ type: "meta",        run_id, app_id, version_id, resumed? }
{ type: "node_start",  node_id, node_type }
{ type: "node_end",    node_id, returns, duration_ms }
{ type: "node_error",  node_id, error, kind, recoverable, duration_ms }
{ type: "node_paused", node_id, node_type, task_token, run_id, duration_ms }
{ type: "task_created",node_id, task_token, assignee_user_id?, assignee_group_id? }
{ type: "if_branch",   node_id, matched }
{ type: "llm_done",    node_id, model, duration_ms, usage? }
{ type: "loop_done" / "parallel_done" / "subflow_done", ... }
{ type: "resumed",     node_id, run_id }
{ type: "log",         level, message }
{ type: "done",        output, state? }
{ type: "error",       error, kind }
```

---

## 5. Tools / MCP

| 端点 | 状态 |
|---|---|
| `GET /tools?enabled=true` | ✅ `tool_routes.py` |
| `POST /tools/{name}/call` | ✅ |
| MCP CRUD | ✅ `mcp_routes.py` |

前端处理:`packages/api/src/tools.ts` `mcp.ts` 已就位;M3 仅做 UI 视觉对齐。

---

## 6. Catalog(能力快捷条 / 写作模板等)

参考图的"AI 操控 / AI 翻译 / AI 写作 / AI 妙记 / 同传字幕 / AI 修图"是**前端能力**,不是后端实体。

| UI | 数据来源 |
|---|---|
| 7 张快捷条 + "全部" | 前端常量 `packages/api/src/fixtures/skills.ts` |
| 写作模板分类(推荐/工作/学习/商业/改写/文学) | 同上,前端常量 |
| "进入"模板 | 路由 `/skill/$id?template=$tmpl` |
| 模板对话 | 复用现有 `/chat/v2/chat`,prompt 拼好系统提示 |

---

## 7. 总结表

| 领域 | 后端就位 | 前端处理 | 风险 |
|---|---|---|---|
| Auth | ✅ | M4 仅做 UI | 低 |
| Chat / Models | ✅ | M3 直连,M3 视情况补"模型管理"前端占位 | 低 |
| KB | ✅ | M3 直连 | 低 |
| Tools / MCP | ✅ | M3 直连 | 低 |
| AI Space | ❌ | M3 前端伪服务 | 中(M5 后端落地时需冒烟回归) |
| Catalog / Skills | N/A(纯前端) | M2/M4 常量 + SkillTemplate | 低 |

**所有领域 M3 起手前不存在后端阻塞。**
