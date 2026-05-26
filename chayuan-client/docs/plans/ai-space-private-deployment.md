# AI Space 私有化部署手册

> 面向运维 / 项目经理 / 系统集成商。覆盖一键安装、环境变量、IM 渠道接入、`embed.js` 内网域名替换、容量规划、备份恢复、升级回滚。

## 0. 部署形态

| 形态 | 说明 | 推荐场景 |
| --- | --- | --- |
| **裸机 / VM 单机** | docker-compose；Postgres + MinIO + chayuan-server + nginx + 前端静态 | 小团队 < 50 用户 |
| **k8s 单集群** | helm chart；StatefulSet for PG / Redis；Deployment for server | 部门级 50–500 用户 |
| **离线安装** | 镜像 tar 包 + 离线模型 + sealos / kubeadm | 完全无外网环境 |
| **桌面 Tauri** | 单机 sqlite + ollama / vllm | 个人 / 演示 |

无论哪种形态，**AI Space 全功能均可用**，唯一差异是 IM 渠道（飞书/钉钉/企微）需要外网回调；裸内网部署可关闭通知 fan-out 仅保留 inapp。

---

## 1. 必需的环境变量

### 1.1 数据库与对象存储

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `SQLALCHEMY_DATABASE_URI` | PG 连接串；建议 `postgresql+psycopg2://...` | （必填） |
| `CHAYUAN_FILE_STORAGE` | `minio` / `local` / `fastdfs` | `local` |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 三件套 | -- |

### 1.2 鉴权

| 变量 | 说明 |
| --- | --- |
| `AUTH_REQUIRED` | `true`/`false`；私有化推荐 `true` |
| `JWT_SECRET` | JWT 签名密钥；生产必须改 |
| `JWT_ACCESS_TTL_SECONDS` / `JWT_REFRESH_TTL_SECONDS` | token 寿命 |

### 1.3 AI Space 专用

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `CHAYUAN_AI_SPACE_CODE_ENABLED` | 允许 `code` 节点执行 RestrictedPython；**安全敏感** | `false` |
| `CHAYUAN_AI_SPACE_HTTP_ALLOWLIST` | `http` 节点出网域名白名单（逗号分隔） | 空（拒绝所有） |
| `CHAYUAN_AI_SPACE_STEP_LIMIT` | 单 run 最大步数 | `200` |
| `CHAYUAN_AI_SPACE_DEFAULT_MODEL` | seed / 模板默认模型 | `qwen2.5-72b-instruct` |
| `CHAYUAN_AI_SPACE_TASK_DEADLINE_SCAN_SEC` | Human-Task 截止扫描周期 | `60` |
| `CHAYUAN_AI_SPACE_DELETED_RETENTION_DAYS` | 软删除保留天数 | `30` |

### 1.4 IM 渠道（可选）

| 变量 | 渠道 |
| --- | --- |
| `CHAYUAN_FEISHU_WEBHOOK` | 飞书自定义机器人 webhook URL |
| `CHAYUAN_DINGTALK_WEBHOOK` | 钉钉机器人 |
| `CHAYUAN_WEWORK_WEBHOOK` | 企业微信机器人 |
| `CHAYUAN_FEISHU_APP_ID` / `CHAYUAN_FEISHU_APP_SECRET` | 飞书机器人 OAuth（卡片回调用） |

未配置的渠道在 `Notifier.send` 时会软失败 + 日志告警，**不阻塞主流程**——任务依然在 Tasklist，用户可主动进入处理。

---

## 2. 一键安装（docker-compose 示例）

```yaml
# docker-compose.yml（节选）
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: chayuan
      POSTGRES_USER: chayuan
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes: [pgdata:/var/lib/postgresql/data]

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes: [miniodata:/data]

  redis:
    image: redis:7
    volumes: [redisdata:/data]

  chayuan-server:
    image: chayuan/chayuan-server:latest
    depends_on: [postgres, minio, redis]
    environment:
      SQLALCHEMY_DATABASE_URI: postgresql+psycopg2://chayuan:${PG_PASSWORD}@postgres/chayuan
      AUTH_REQUIRED: "true"
      JWT_SECRET: ${JWT_SECRET}
      CHAYUAN_FILE_STORAGE: minio
      MINIO_ENDPOINT: http://minio:9000
      CHAYUAN_AI_SPACE_CODE_ENABLED: "false"   # 默认关闭，按需开启
      CHAYUAN_AI_SPACE_HTTP_ALLOWLIST: "internal-api.example.com,gw.example.com"
      CHAYUAN_FEISHU_WEBHOOK: ${FEISHU_WEBHOOK:-}

  chayuan-frontend:
    image: chayuan/chayuan-frontend:latest
    environment:
      VITE_API_BASE: /api
    depends_on: [chayuan-server]

  nginx:
    image: nginx:1.25
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/ssl:ro
    depends_on: [chayuan-server, chayuan-frontend]

volumes: { pgdata: {}, miniodata: {}, redisdata: {} }
```

### 2.1 nginx 配置要点（关键片段）

```nginx
# /api/* → chayuan-server，注意 SSE 关 buffer
location /api/ {
    proxy_pass http://chayuan-server:62581/;
    proxy_buffering off;            # SSE 必须
    proxy_read_timeout 1d;          # /run / /tasks/{token}/complete 长连接
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# embed.js 静态产物 + 高 cache
location = /api/embed/ai-space.js {
    proxy_pass http://chayuan-server:62581/embed/ai-space.js;
    add_header Cache-Control "public, max-age=3600";
    gzip on; gzip_types application/javascript;
}

# 公开页 /ai-app/{slug} 直接走前端 SPA
location /ai-app/ {
    try_files $uri $uri/ /index.html;
}
```

### 2.2 首次启动

```bash
# 1) 启动基础组件
docker compose up -d postgres minio redis

# 2) 跑 alembic 升级（一次性）
docker compose run --rm chayuan-server alembic upgrade head

# 3) 启动主服务
docker compose up -d
```

升级后会推进到 `0018_ai_app_rate_limit`（含 ai-space 全部 11 张表）。

---

## 3. 离线模型接入

私有化部署优先选用本地 LLM，避免外网依赖：

| 后端 | 推荐 | 配置 |
| --- | --- | --- |
| **vLLM** | 性能最优 | `model_settings.yaml` → `MODEL_PLATFORMS[].api_base_url=http://vllm:8000/v1` |
| **Ollama** | 易用 | `api_base_url=http://ollama:11434/v1` |
| **OneAPI / FastChat** | 多模型聚合 | 走 OneAPI 网关 |
| **xinference** | 含 Embedding / Rerank | 同 OpenAI 兼容协议 |

模型平台通过 **管理员后台**（`/marketplace`）在线增删，DB（migration `0004`）即时生效，无需重启。

---

## 4. `embed.js` 内网域名替换

`embed.js` 默认走 `<script src="${origin}/api/embed/ai-space.js">`，对内网部署天然适配。需要二次发行（如客户白标）时：

1. **改默认 host**：编辑 `chayuan/server/ai_space/embed_js.py` 中的 `_EMBED_JS` 字符串里的 `host` 推断逻辑
2. **CORS / Referer 白名单**：在 ShareDialog → 嵌入 → 添加被嵌入网站的 origin（如 `https://intranet.example.com/*`）
3. **CDN 缓存**：nginx `Cache-Control: public, max-age=3600`；如需即时失效，前端引入时加 `?v=2`

`embed.js` **不需要 sk** —— 仅靠 publishable_key + Referer 白名单做边界，安全模型独立于内部 Bearer Key。

---

## 5. 容量规划

私有化场景**不做计费**，但仍需评估容量：

| 指标 | 来源 | 红线 |
| --- | --- | --- |
| **并发 run** | `ai_app_run.status='running'` 计数 | 单实例 100 并发；超过加副本 |
| **每秒新建 run** | `RunLog` 滑窗 | 单实例 50 RPS |
| **Human-Task 挂起数** | `ai_app_task.status='pending'` | 5000；超过加 deadline_worker 频次 |
| **Postgres** | 主要写：`ai_app_run.state_blob`（JSON） | 单 row < 256KB；超过开归档 |
| **MinIO** | trace blob + KB 文件 | 默认无上限；按存储桶配额 |
| **Redis** | rate_limit 计数 + presence 心跳 | 默认全可丢失，故障无影响 |

容量看板：`Studio → 历史` 区显示过去 24h 状态分布；管理员可拉 `/admin/runs/stats` 拿到聚合数据。

---

## 6. 备份与恢复

### 6.1 备份范围

| 数据 | 备份 | RTO / RPO |
| --- | --- | --- |
| Postgres（应用元数据 + 草稿 + 任务） | `pg_dump --format=custom` 每天 + WAL | RPO 5 min |
| MinIO（KB 文件 / trace blob） | `mc mirror` 异步 | RPO 1h |
| Redis | 无（可重建） | -- |
| `model_settings.yaml` / `prompt_settings.yaml` | git | -- |

### 6.2 恢复演练

至少季度执行一次：
1. 拉新机器，挂载备份
2. `pg_restore` → `alembic current`（确认 head 一致）
3. 启动 server；登录 Tauri 端测试 5 个内置模板
4. 跑一次 Human-Task 流程；验证通知到达

---

## 7. 升级 / 回滚

```bash
# 1) 备份（前置）
pg_dump --format=custom chayuan > pre-upgrade.dump

# 2) 拉新镜像
docker compose pull chayuan-server chayuan-frontend

# 3) 跑 migration
docker compose run --rm chayuan-server alembic upgrade head

# 4) 滚动重启
docker compose up -d --no-deps chayuan-server chayuan-frontend

# 5) 回滚（如需要）
alembic downgrade <prev_revision>
docker compose up -d --no-deps chayuan-server:<prev_tag>
```

**降级注意**：alembic downgrade 仅在 server 版本同步降级时安全；跨 N 版降级建议**全库恢复 + 重启**而不是脚本回滚。

---

## 8. 安全 checklist

部署上线前过一遍：

- [ ] `JWT_SECRET` 已改非默认值且 ≥ 32 字符
- [ ] `AUTH_REQUIRED=true`
- [ ] Postgres 密码长且仅内网可访问；外网仅 nginx 443 暴露
- [ ] `CHAYUAN_AI_SPACE_CODE_ENABLED=false`（除非有审计流程）
- [ ] `CHAYUAN_AI_SPACE_HTTP_ALLOWLIST` 已配置；为空 = 拒绝所有出网
- [ ] 所有 ApiKey 默认 `is_publishable=false`；前端浮动球必须配 referer 白名单
- [ ] nginx 启用 TLS；HSTS / `X-Frame-Options: DENY` 或显式 allowlist
- [ ] 公开应用默认 `require_login=true`
- [ ] MinIO bucket 私有；不公网直连
- [ ] 模型平台的 api_key 通过 admin 后台维护，不进 yaml git

---

## 9. 常见故障排查

| 现象 | 怀疑 | 排查 |
| --- | --- | --- |
| Studio 调试运行卡在 node_paused 不前进 | Human-Task 通知未送达 | 看 `chayuan-server` 日志 `[notify]` 行；确认渠道 webhook 可达 |
| 提交任务后流程未续跑 | resume 失败 | `GET /apps/{id}/runs/{run_id}` 看 `state_blob.current_node_id` 与 status；查 `ai_app_run_log` |
| OpenAPI invoke 401 | sk 哈希不匹配 | 重新生成 Key；检查请求头 `Bearer ak.sk` 格式 |
| embed.js 浮动球出不来 | publishable_key 没建 / referer 不在白名单 | F12 Network 看 `/api/embed/ai-space.js` 是否 200；浏览器 console 看 `[chayuan-embed]` 提示 |
| 公开页 410 | share_settings.expires_at 已到 | 编辑分享设置或清空过期时间 |
| 节点 `code` 报 disabled | 安全开关未开 | 慎重评估后设 `CHAYUAN_AI_SPACE_CODE_ENABLED=true`；建议结合 admin 角色 |
| `http` 节点 SSRF 警告 | 域名不在白名单 | 加到 `CHAYUAN_AI_SPACE_HTTP_ALLOWLIST` |
| Tasklist 红点不掉 | WS 心跳失败 | 看 `/ws/notifications` 连接；nginx `proxy_read_timeout` 是否够长 |

---

## 10. Tauri 桌面端补充

`apps/desktop` 用 Tauri 打包，AI Space 全功能可用。差异：

- 通知走 OS 原生（macOS Notification Center / Windows Action Center）；`embed.js` 不适用桌面
- IM 渠道仍走服务端 webhook；客户端被动接收 inapp WS 推送
- 离线缓存：Tasklist 最近 50 条 / 表单草稿 / 应用元信息走 `tauri-plugin-store`
- 系统托盘红点：未完成任务数；点击拉起到 `/tasks`

构建：

```bash
pnpm --filter @chayuan/desktop tauri build
# 产物在 apps/desktop/src-tauri/target/release/bundle/
```

---

## 附：自检脚本

部署后跑一遍自检：

```bash
# 1. 健康检查
curl -fsS http://localhost/api/health/ready

# 2. alembic head
docker compose exec chayuan-server alembic current
# 期望: 0018_ai_app_rate_limit (head)

# 3. 模板列表
curl -fsS -H "Authorization: Bearer $TOKEN" http://localhost/api/ai/space/templates | jq '.items | length'
# 期望: 5

# 4. embed.js
curl -I http://localhost/api/embed/ai-space.js
# 期望: 200 + content-type application/javascript
```

任意一步失败请按 §9 故障排查表定位。
