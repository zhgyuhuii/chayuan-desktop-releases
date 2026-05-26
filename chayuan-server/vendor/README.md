# `vendor/` — 第三方服务与运行时离线包"投放区"

本目录是**开发者 / 运维**把第三方依赖（PostgreSQL、Redis、MinIO、Milvus、
Ollama、llama.cpp、vLLM、Whisper、Piper、RapidOCR、ComfyUI ...）"放进来"
的统一约定位置。chayuan-server 不下载、不内嵌任何第三方二进制，但提供：

1. **目录约定**——开发者按既定结构丢进去；
2. **扫描器**——`chayuan service vendor` 与 `GET /runtime/vendor` 接口能
   立即识别可用项；
3. **端口分配 + 凭据生成**——PortAllocator 用大端口避免与系统已装服务冲突；
   首次启动自动生成账号密码，落到 `<CHAYUAN_ROOT>/runtime.json`；
4. **chayuan service info**——一条命令打印每个服务最终端口、地址、用户、密码。

> 注意：本目录默认在 `.gitignore` 之外（保持空），生产环境的 vendor 投放
> 通常发生在 `<CHAYUAN_ROOT>/vendor/`（用户数据目录），与代码仓库分离。

## 目录布局

```
vendor/
├── README.md                       本文件
├── services/                       后台基础设施（PG / Redis / MinIO ...）
│   ├── postgres/                   ① 优先：放 docker-compose.yml；② 也可 bin/postgres
│   │   ├── docker-compose.yml
│   │   └── README.md               每个服务自己的下载链接 + 端口约定
│   ├── redis/
│   ├── minio/
│   ├── milvus/
│   ├── elastic/
│   └── onlyoffice/
└── runtimes/                       推理运行时（Ollama / llama.cpp / vLLM ...）
    ├── ollama/bin/ollama
    ├── llama-cpp/bin/llama-server
    ├── vllm/                       venv 或 wheel 解压目录
    ├── whisper-cpp/bin/whisper-server
    ├── funasr/
    ├── piper/bin/piper
    ├── cosyvoice/
    ├── rapidocr/
    ├── paddleocr/
    ├── comfyui/                    完整 ComfyUI 仓库
    └── infinity/                   Infinity-Embeddings server
```

## 端口约定（PortAllocator 偏好端口）

| 服务 | 上游默认端口 | 察元偏好端口（不冲突） | 备注 |
|---|---|---|---|
| API Server | — | **62581**（本仓库默认） | yaml 改 `API_SERVER.port` |
| 配置面板 | — | **8502** | yaml 改 `CONFIG_SERVER.port` |
| PostgreSQL | 5432 | **35432** | +30000 偏移，远离 docker 默认 |
| Redis | 6379 | **36379** | |
| MinIO API | 9000 | **39000** | |
| MinIO Console | 9001 | **39001** | |
| Milvus | 19530 | **39530** | gRPC |
| Milvus Metrics | 9091 | **39091** | |
| Elasticsearch | 9200 | **39200** | |
| OnlyOffice | 80 | **38080** | |
| Ollama | 11434 | **31434** | |
| llama.cpp server | 8080 | **38081** | |
| vLLM server | 8000 | **38000** | |
| whisper.cpp | 8080 | **38010** | |
| FunASR | — | **38020** | |
| Piper TTS | — | **38030** | |
| CosyVoice | — | **38040** | |
| RapidOCR | — | **38050** | |
| PaddleOCR | — | **38060** | |
| ComfyUI | 8188 | **38188** | |
| Infinity | 7997 | **37997** | |

> **任何一个偏好端口被占用**，PortAllocator 会在 `Settings.PORT_RANGE`
> （默认 `[40000, 60999]`）内自动 bump 到下一个空闲端口；最终值落到
> `runtime.json`，下次重启稳定复用。运行 `chayuan service info` 立即看到。

## 凭据策略

* 任何启用了鉴权的服务（Postgres / Milvus / MinIO / OnlyOffice ...）首次
  启动会**自动生成** 24 字符高熵密码，落到 `<CHAYUAN_ROOT>/runtime.json`
  （`chmod 600`）。
* yaml 中相关字段（`SQLALCHEMY_DATABASE_URI` / `MINIO_ACCESS_KEY` ...）
  保持空白即可，运行时会读 `runtime.json` 拼出真正的连接串。
* 显示密码：`chayuan service info --reveal` 或 `GET /runtime/services?reveal=true`。

## 怎么"放"一个服务

1. 创建目录：`mkdir -p vendor/services/redis`
2. 选 A 或 B：
    * **A. 用 docker**——把官方 docker-compose 片段放到
      `vendor/services/redis/docker-compose.yml`，监听端口写
      `${CHAYUAN_RUNTIME_REDIS_PORT}`（chayuan 启动前会注入）；
    * **B. 用离线二进制**——把 `redis-server` 放到 `vendor/services/redis/bin/`，
      并加可执行权限。
3. 验证：`chayuan service vendor` —— 看到 `✓ redis` 即扫描通过。
4. 启动：`chayuan service recheck`（自动选择端口、生成凭据）→
   `chayuan service info`（查看分配结果）。

## 详细子目录指引

* [services/README.md](./services/README.md) — 后端基础设施
* [runtimes/README.md](./runtimes/README.md) — 推理运行时

## 与 chayuan-server / chayuan-client 的关系

* **chayuan-server**：本目录的扫描器（`chayuan/server/runtime/vendor_loader.py`）
  + 端口分配器（`port_allocator.py`）+ 凭据生成器（`credentials.py`）+
  HTTP API（`/runtime/*`）。
* **chayuan-client**：`packages/api/src/runtime.ts` 调 `/runtime/*`，
  `apps/desktop` & `apps/web` 在"系统设置 → 系统服务"页面渲染。

## 常见 FAQ

**Q：之前已经有一个 PostgreSQL 跑在 5432，这里又放一个会冲突吗？**
A：不会。PortAllocator 默认使用 35432；即便您把偏好端口手动改成 5432，
扫到端口被占也会自动 bump，并把最终端口写到 runtime.json。

**Q：我希望系统直接复用机器上已经装好的 Redis（系统包安装的）。**
A：不放 `vendor/services/redis/` 即可；在配置面板（或 yaml）里直接把
`REDIS_URL` 指过去。`chayuan service info` 会显示"redis 来自外部"，
不再纳入受管列表。

**Q：vendor 里的二进制能和 chayuan 一起打包成安装包吗？**
A：可以；`packaging/{linux,mac,windows}/` 的脚本把 `vendor/` 整体复制进
分发包。在终端用户机器上 `chayuan init` 完，`<CHAYUAN_ROOT>/vendor/` 就有
全套二进制，无需联网。
