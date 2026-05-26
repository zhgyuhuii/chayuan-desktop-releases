# 察元 AI — Docker 部署

单 image 跑后端 + nginx + 前端 SPA，浏览器开网址直接用。

## 一键启动

```bash
cd docker
docker compose up -d --build
```

第一次 `build` 约 5–15 分钟（拉 Python / Node 镜像 + 装依赖 + build 前端）。后续 `up -d` 几秒拉起。

启动完毕（约 60–180s 让 sidecar 就绪）：

| 入口 | URL | 用途 |
|---|---|---|
| 前端 SPA | http://localhost:8080 | 浏览器主入口 |
| 后端 API | http://localhost:62581 | Tauri / WPS / 三方集成 |
| 配置面板 | http://localhost:8502 | 模型 / yaml 配置 (NiceGUI) |

## 目录结构（compose 同目录映射）

```
docker/
├── docker-compose.yaml     ← 服务定义 (你正在用)
├── Dockerfile              ← image 构建
├── nginx.conf              ← nginx 反代配置
├── entrypoint.sh           ← 容器内启动脚本
├── README.md
│
├── data/                   ← KB / sqlite / 上传文件 (volume → /chayuan/data)
├── bundled_models/         ← 模型 (volume → vendor/bundled_models)
│   ├── chat/
│   │   └── Qwen3-4B-Instruct-2507-GGUF/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
│   ├── embedding/
│   │   └── bge-m3/bge-m3-Q8_0.gguf
│   ├── rerank/
│   │   └── gpustack--bge-reranker-v2-m3-GGUF/bge-reranker-v2-m3-Q8_0.gguf
│   └── asr/
│       └── whisper.cpp/ggml-medium.bin
├── config/                 ← 可选: 自定义 *.yaml (会 symlink 到 CHAYUAN_ROOT)
└── logs/                   ← nginx + chayuan-server 运行日志
```

## 提供模型

容器 image 里**不打模型**(节省体积 + 升级独立)。第一次启动前，请把 GGUF 模型按结构放进 `docker/bundled_models/`：

```bash
mkdir -p docker/bundled_models/{chat,embedding,rerank,asr}

# 例: 从 ModelScope 下载 (国内可达)
# Chat
curl -L -o docker/bundled_models/chat/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://www.modelscope.cn/api/v1/models/Qwen/Qwen3-4B-Instruct-2507-GGUF/repo?Revision=master&FilePath=Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

# Embedding
mkdir -p docker/bundled_models/embedding/bge-m3
curl -L -o docker/bundled_models/embedding/bge-m3/bge-m3-Q8_0.gguf \
  "https://www.modelscope.cn/api/v1/models/gpustack/bge-m3-GGUF/repo?Revision=master&FilePath=bge-m3-Q8_0.gguf"

# Rerank
mkdir -p docker/bundled_models/rerank/gpustack--bge-reranker-v2-m3-GGUF
curl -L -o docker/bundled_models/rerank/gpustack--bge-reranker-v2-m3-GGUF/bge-reranker-v2-m3-Q8_0.gguf \
  "https://www.modelscope.cn/api/v1/models/gpustack/bge-reranker-v2-m3-GGUF/repo?Revision=master&FilePath=bge-reranker-v2-m3-Q8_0.gguf"

# ASR (可选, 不要语音功能可跳过)
mkdir -p docker/bundled_models/asr/whisper.cpp
curl -L -o docker/bundled_models/asr/whisper.cpp/ggml-medium.bin \
  "https://www.modelscope.cn/api/v1/models/AI-ModelScope/whisper.cpp/repo?Revision=master&FilePath=ggml-medium.bin"
```

放好后 `docker compose restart chayuan`，新模型自动注册。

## 常用命令

```bash
# 看日志
docker compose logs -f chayuan

# 重启 (改了模型/配置)
docker compose restart chayuan

# 进容器 shell
docker compose exec chayuan bash

# 看 sidecar 状态
docker compose exec chayuan curl http://127.0.0.1:62581/runtime/llama/status

# 停止 (数据保留)
docker compose down

# 完全删除 (含 image, 数据 bind mount 仍在本目录不动)
docker compose down --rmi local
```

## 端口冲突 / 改端口

宿主机已经占了 8080 / 62581 / 8502 → 改 compose 的 `ports`:

```yaml
ports:
  - "9090:80"      # 改成 9090
```

注意：API 端口 62581 是 chayuan-server **容器内**写死的，**只能改宿主映射端口**（左边数字）；右边 62581 不能改。

## 升级

```bash
git pull
cd docker
docker compose up -d --build      # 重 build image + 平滑替换容器，数据保留
```

## 故障排查

```bash
# 看 nginx + chayuan 启动日志
docker compose logs --tail=200 chayuan

# 健康检查
curl http://localhost:8080/healthz                          # nginx
curl http://localhost:62581/runtime/llama/status            # 后端 sidecar

# 看 /chayuan/data 真实位置
ls -la docker/data/data/knowledge_base/

# 重启清缓存（不删数据）
docker compose down && docker compose up -d
```

## 登录 / 多用户切换

默认是**单机匿名模式** — 装完即用，所有访问按 GUEST 共享，无需登录。

切**多用户 + 登录**模式：

```bash
cp .env.example .env
# 编辑 .env，取消注释 "模式 2" 那段：
#   CHAYUAN_AUTH_REQUIRED=true
#   CHAYUAN_ALLOW_REGISTRATION=false       # 内部部署关注册更安全
#   JWT_SECRET=$(openssl rand -hex 64)     # 必须 ≥32 字节，否则随机生成，重启失效
#   AUTH_DEFAULT_ADMIN_USERNAME=admin
#   AUTH_DEFAULT_ADMIN_PASSWORD=改你想要的初始密码
docker compose up -d --build
```

启用后：
- `/chat/*` `/knowledge_base/*` `/api/*` 全部要 JWT
- 前端首屏自动弹登录框
- 没设 `AUTH_DEFAULT_ADMIN_PASSWORD` → 首次启动随机生成，看日志：
  ```bash
  docker compose logs chayuan | grep -iE "默认管理员|admin password|password="
  ```
- 关掉自助注册后，新用户只能 admin 在 **配置面板 (`:8502`)** 添加

随时切回匿名：`.env` 改 `CHAYUAN_AUTH_REQUIRED=false`，`docker compose up -d` 重启容器。

## 安全

**生产/公网部署**前清单：

1. **必须**多用户模式 (`CHAYUAN_AUTH_REQUIRED=true`)，`JWT_SECRET` 不能留空
2. nginx `listen 80` 换成 TLS（或前置 caddy / cloudflare / 你自己的 ingress）
3. `ports` 中只对外暴露 `:8080`（前端），**不要**直接暴露 `:62581` / `:8502` 到公网
   —— 它们是后端 + 后台，只该走内网或绑 127.0.0.1
4. `bundled_models` / `data` 做定期备份
5. 关掉 `CHAYUAN_ALLOW_REGISTRATION` 避免任意人注册

## 与 desktop 安装包关系

Docker image 跑的是同一份 `chayuan-server` 源码（不是 frozen PyInstaller）。所有功能跟 desktop 一致，但：

- **不打 chayuan-client Tauri 壳**：浏览器代替桌面 webview
- **不打 bundled_models**：volume 挂入,避免每次升级重复下几个 GB
- 跑 Linux 版 `llama-server` / `whisper-server` 二进制（Linux x64 only）

ARM64 / Windows / Mac 二进制不打进 docker image（用 `.dockerignore` 排除了，省 image 体积）。
