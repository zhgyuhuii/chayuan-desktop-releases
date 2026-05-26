# `vendor/services/` — 后端基础设施离线包

每个子目录代表一个**可由察元自管的后端服务**。子目录扫描器约定如下：

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 优先识别。若存在，`chayuan service vendor` 会标 `✓` 表示"通过 docker 启" |
| `bin/<exe>`          | 离线二进制兜底；与 docker-compose 二选一即可 |
| `README.md`          | 子目录自身的下载链接 / 启动注意事项（必填） |

## 已支持的服务

| 子目录 | 用途 | 上游下载 | 默认偏好端口 |
|---|---|---|---|
| `postgres/`   | 业务数据库 / pgvector | https://www.postgresql.org/download/ <br/> https://hub.docker.com/_/postgres | 35432 |
| `redis/`      | 缓存 / 限流 / 异步队列 | https://redis.io/download <br/> https://hub.docker.com/_/redis | 36379 |
| `minio/`      | 统一文件存储 / Milvus 后端 | https://min.io/download <br/> https://hub.docker.com/r/minio/minio | 39000 / 39001 |
| `milvus/`     | 向量库 | https://milvus.io/docs/install_standalone-docker.md <br/> https://hub.docker.com/r/milvusdb/milvus | 39530 / 39091 |
| `elastic/`    | BM25 / 全文 / 知识源 | https://www.elastic.co/cn/downloads/elasticsearch <br/> https://hub.docker.com/_/elasticsearch | 39200 |
| `onlyoffice/` | 文档协同 | https://www.onlyoffice.com/zh/download-docs.aspx <br/> https://hub.docker.com/r/onlyoffice/documentserver | 38080 |
| `llama-server/` | 本地 LLM runtime (CPU)<br/>跟集成版 .msi 走,装机后由 LlamaRuntimeManager spawn | https://github.com/ggerganov/llama.cpp/releases<br/>`llama-bin-win-cpu-x64.zip` (Win) / `llama-bin-ubuntu-x64.zip` (Linux) / `llama-bin-macos-arm64.zip` (Mac)<br/>开发机:`scripts/install-llama-server.{ps1,sh}` | 62582 |

## 端口与凭据

* 偏好端口仅是"试一下"，PortAllocator 发现冲突会自动 bump；
* 用户名 / 密码首次启动自动生成 24 字符高熵；
* `<CHAYUAN_ROOT>/runtime.json` 是 SSOT，docker-compose 子目录里的环境
  变量请用 `${CHAYUAN_RUNTIME_<NAME>_USER}` / `${...}_PASSWORD` /
  `${...}_PORT` 占位符（chayuan 启动前会注入）。

## 模板：`vendor/services/<name>/docker-compose.yml`

```yaml
name: chayuan-vendor-<name>
services:
  <name>:
    image: <official-image>:<pin-version>
    restart: unless-stopped
    environment:
      USER: ${CHAYUAN_RUNTIME_<NAME>_USER}
      PASSWORD: ${CHAYUAN_RUNTIME_<NAME>_PASSWORD}
    ports:
      - "127.0.0.1:${CHAYUAN_RUNTIME_<NAME>_PORT}:<container_port>"
    volumes:
      - ${CHAYUAN_DATA}/vendor-data/<name>:/var/lib/<name>
```

> `CHAYUAN_DATA` = `<CHAYUAN_ROOT>` 的别名，由 chayuan 启动时 export。
