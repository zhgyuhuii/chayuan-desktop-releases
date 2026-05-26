# AI Space 私有化部署清单

> 适用于 `chayuan-server` + `chayuan-client` 内网部署。本文档汇总了所有可选 / 必选配置。

---

## 1. 数据库迁移

部署前先把 alembic 升到最新（含 AI Space 全部表）：

```bash
cd chayuan-server/libs/chayuan-server
alembic upgrade head
```

涉及的迁移（按编号）：

| Migration | 表 / 变更 |
| --- | --- |
| `0008_ai_app` | `ai_app` / `ai_app_version` / `ai_app_key` |
| `0009_ai_app_human_task` | `ai_app_run` / `ai_app_task` / `ai_app_task_event` |
| `0010_ai_app_eval` | `ai_app_test_case` / `ai_app_test_run` |
| `0011_ai_app_share` | `ai_app.share_settings` 字段 |
| `0012_ai_app_audit_log` | `ai_app_audit_log` |
| `0013_ai_app_secret` | `ai_app_secret` (vault) |
| `0014_ai_app_node_comment` | `ai_app_node_comment` |
| `0015_ai_app_grants` | `ai_app_grant` (RBAC) |
| `0016_ai_app_run_log` | `ai_app_run_log` (节点级日志) |
| `0017_ai_app_market_review` | `ai_app.market_review_*` 字段 |
| `0018_ai_app_rate_limit` | `ai_app_rate_limit` (PG 限流计数表) |

---

## 2. 必选环境变量

```bash
# 数据库 / Redis 等基础已在主部署文档；以下仅 AI Space 增量

# Vault 加密密钥 —— 必填，否则密钥将以弱混淆存储
CHAYUAN_SOURCE_SECRET_KEY="<32+ 位随机字符串>"

# 公开短链 / IM 卡片回跳的 base URL
CHAYUAN_PUBLIC_BASE_URL="https://ai.example.com"
```

---

## 3. 可选功能开关

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHAYUAN_AI_SPACE_HTTP_ALLOWLIST` | `*` | http 节点出网域名白名单，逗号分隔；支持 `*.example.com` 后缀 / 精确域 |
| `CHAYUAN_AI_SPACE_CODE_ENABLED` | `false` | 是否启用 `code` 沙箱节点（必须由 admin 显式开启） |
| `CHAYUAN_AI_SPACE_CODE_RLIMIT` | `false` | code 节点是否启用 setrlimit 限 CPU/内存（仅 *nix） |
| `CHAYUAN_AI_SPACE_CODE_MEM_MB` | `256` | code 节点的内存上限 MB |

---

## 4. 通知通道（Notifier）

任务通知 fan-out 通道按渠道独立配置；缺失即"软失败"，不影响主流程。

### 4.1 邮件（Email）

```bash
CHAYUAN_SMTP_HOST="smtp.example.com"
CHAYUAN_SMTP_PORT="465"
CHAYUAN_SMTP_USER="noreply@example.com"
CHAYUAN_SMTP_PASSWORD="<password>"
CHAYUAN_SMTP_FROM="noreply@example.com"   # 可选；空则用 SMTP_USER
CHAYUAN_SMTP_SSL="true"                    # false 走 STARTTLS
```

收件人：从任务的 `context.recipient_email` 读取（assignee 解析时由 RoleResolver 填）。

### 4.2 飞书 / 钉钉 / 企业微信

```bash
CHAYUAN_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
CHAYUAN_DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=xxx"
CHAYUAN_WEWORK_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
```

也可在 `human_task` 节点的 `notify.context.{feishu,dingtalk,wework}_webhook` 字段配置 per-task 覆盖。

---

## 5. 限流配置

按 ApiKey 设置 `rate_limit` JSON：

```json
{
  "qps": 10,
  "max_concurrent": 5,
  "daily_tokens": 1000000
}
```

落点：

- `qps` / `daily_tokens` 走 PG 计数表（多副本一致）
- `max_concurrent` 仍是进程内（多副本下整体上限 = 进程数 × max_concurrent）；
  严格上限需要 Redis SET NX，可后续接入

---

## 6. 嵌入与公开页

```bash
# /embed/ai-space.js 跨域：需要在反向代理放开
# /ai-app/{slug} 公开页：lifecycle=public 才返回 200
# /s/{token} 短链：按 slug 前缀匹配（≥3 字符；冲突时返回 404）
```

公网 SaaS 形态下建议：
- 强制 `share_settings.require_login=true`
- 公开页不放敏感模型名 / token 数（前端已隐藏）

---

## 7. Deadline / Reminder 后台 worker

启动时自动起，60s 轮询 `ai_app_task`：
- 过期 → status=expired + 写 `task_event(type='expired')`
- 30 分钟内到期 → 触发 `task_event(type='reminded')` + fanout 一次提醒

无需额外配置；多副本部署时每个进程都跑，依赖行锁 (`with_for_update(skip_locked=True)`) 避免重复处理。

---

## 8. Run 日志清理

`ai_app_run_log` 表会快速增长。建议加定时任务：

```sql
DELETE FROM ai_app_run_log WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM ai_app_rate_limit WHERE window < extract(epoch from now()) - 86400;
```

或写到部署侧的清理 cronjob。

---

## 9. 安全 checklist

- [ ] `CHAYUAN_SOURCE_SECRET_KEY` 已从默认值改成 32+ 位随机
- [ ] `CHAYUAN_AI_SPACE_HTTP_ALLOWLIST` 显式配置（不留 `*`）
- [ ] `CHAYUAN_AI_SPACE_CODE_ENABLED` 仅在确需 code 节点时打开，且开 `CODE_RLIMIT`
- [ ] 数据库用户对 `ai_app_*` 表只有最小权限
- [ ] 反向代理开启 SSL；公开页加 CSP
- [ ] 定期审计 `ai_app_audit_log` —— 含 publish / make_public / share_settings_changed / approve_market 等关键动作

---

## 10. 监控指标

私有化场景推荐外接：

- `ai_app_run` 表实时计数（按 status group by）→ Prometheus exporter
- `ai_app_run_log` 聚合（avg duration_ms / sum tokens_in/out by app_id）
- `ai_app_rate_limit` 实时 counter

容量看板 `/api/ai/space/apps/{id}/observe?days=N` 已内置；可直接拉指标做更精细的 dashboard。
