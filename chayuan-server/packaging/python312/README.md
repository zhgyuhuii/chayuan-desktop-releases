# 察元跨平台打包器（Python 3.12）

> 一份脚本搞定 **3 种 OS × 2 种架构 × 3 种发布矩阵 = 18 组安装包**。
> 同一份 `layout.yaml` 描述「该放什么二进制 / 该装哪些模型」；
> 开发人员只管"放进去"，packager 负责"装出来"。

---

## 1. 30 秒上手

```bash
# 一次性安装（开发机上跑一次，需要 Python ≥ 3.12）
cd chayuan-server/packaging/python312
python3.12 -m venv .venv
source .venv/bin/activate          # win 上：.\.venv\Scripts\activate
pip install -e .

# 拉资源 → 解压 → 打包（lite 包，约 2.6 GB）
chayuan-pack fetch  --target linux --arch x86_64 --release lite
chayuan-pack stage  --target linux --arch x86_64 --release lite
chayuan-pack build  --target linux --arch x86_64 --release lite --version 1.0.0

# 产物：packaging/python312/build/out/chayuan-1.0.0-lite-linux-x86_64.AppImage
```

---

## 2. 目录约定（"该放什么 / 放哪 / 放完做什么"）

```
chayuan-server/
├── vendor/
│   ├── runtimes/                       ← 运行时与推理引擎（packager 帮你拉）
│   │   ├── python/                     Python 3.12 (python-build-standalone)
│   │   ├── jdk17/                      OpenJDK 17 (Adoptium)
│   │   ├── nodejs/                     Node.js 20 (nodejs.org)
│   │   ├── ollama/                     Ollama (github://ollama/ollama)
│   │   ├── llama-cpp/                  llama.cpp (github://ggerganov/llama.cpp)
│   │   ├── whisper-cpp/                whisper.cpp
│   │   ├── piper/                      Piper TTS
│   │   └── ...                         （详见 layout.yaml ‘inference’ 段）
│   └── services/                       ← 后台基础设施
│       ├── postgres/bin/postgres       ← 离线 PG 二进制（EnterpriseDB tarball）
│       │   或
│       ├── postgres/docker-compose.yml ← 用 docker 启动
│       ├── redis/                      ← 同上：bin/ 或 docker-compose
│       ├── minio/                      ← MinIO 单二进制（dl.min.io）
│       ├── milvus/                     ← Milvus 二进制 / docker
│       ├── docker-compose/             ← compose CLI（系统无 docker 时引导用户装）
│       ├── elastic/                    ← Elasticsearch
│       └── onlyoffice/                 ← 仅 docker-compose（无单二进制）
└── models/                             ← 9 类模型（按目录区分类别）
    ├── chat/Qwen--Qwen2.5-3B-Instruct-GGUF/qwen2.5-3b-instruct-q4_k_m.gguf
    ├── embedding/BAAI--bge-small-zh-v1.5/...
    ├── rerank/BAAI--bge-reranker-base/...
    ├── clip/                           （image-embedding）
    ├── t2i/                            （文生图）
    ├── t2v/                            （文生视频）
    ├── tts/                            （语音合成）
    ├── asr/                            （语音识别）
    └── ocr/                            （图像文字识别）
```

> **一句话**：把可执行二进制丢到 `vendor/services/<name>/bin/`（或写一个
> `docker-compose.yml`），把模型文件丢到 `models/<category>/<Org--Repo>/`，
> packager 自动识别、自动选端口、自动生成密码、自动启子进程。

---

## 3. 各服务的"投放说明"

### 3.1 Postgres

```bash
# A. 二进制（推荐 linux 服务端 / win 桌面端）
mkdir -p vendor/services/postgres/bin
# 从 https://www.enterprisedb.com/download-postgresql-binaries 下载对应平台的
# tar/zip，把里面的 pgsql/bin/{postgres,initdb,psql} 复制过来
cp /path/to/pg/bin/{postgres,initdb,psql} vendor/services/postgres/bin/

# B. docker-compose
cat > vendor/services/postgres/docker-compose.yml <<'EOF'
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${PG_PASSWORD}
      POSTGRES_USER:     ${PG_USER}
      POSTGRES_DB:       chayuan
    ports:
      - "${PG_PORT}:5432"
    volumes:
      - ../../data/postgres:/var/lib/postgresql/data
EOF
```

> 默认端口 **35432**；端口被占由 `PortAllocator` 自动 +1 探到 `[40000, 60999]` 内的空位。

### 3.2 Redis

```bash
# Linux/mac（开发机能编译）：直接用 packager 自动拉源码 + make
chayuan-pack fetch --release lite
# 它把 redis-7.4.0.tar.gz 解压到 vendor/services/redis/redis-7.4.0/
# 并跑 make -j 在 vendor/services/redis/bin/redis-server 出二进制

# Windows：用 tporadowski/redis 的 zip
# packager 自动下，无需手动操作
```

> 默认端口 **36379**；首启自动生成 24 字符密码 → `<CHAYUAN_ROOT>/runtime.json`。

### 3.3 MinIO

```bash
# 单二进制（dl.min.io）
mkdir -p vendor/services/minio/bin
curl -fLo vendor/services/minio/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x vendor/services/minio/bin/minio
```

> 端口 **39000**（API） / **39001**（Console）；用户名 `chayuan_admin`，密码自动生成。

### 3.4 Milvus（向量库）

```bash
# 单机：默认走 milvus-lite（pip wheel pymilvus[lite]，无独立进程）
# 服务器：放 milvus 二进制 + docker-compose
```

> 默认端口 **39530**（gRPC）。

### 3.5 OnlyOffice（在线文档协作）

OnlyOffice 不提供单机二进制，只有 docker 镜像。落地策略：

* **Linux 服务器**：`vendor/services/onlyoffice/docker-compose.yml`（packager 已模板化）；
* **Win/mac 单机**：检测无 docker → 弹窗"该功能需 Docker Desktop，[查看引导]"；
  packager 把 `vendor/services/docker-compose/` 的 compose CLI 静态二进制带上，
  当用户装好 Docker Desktop 之后能立刻 `compose up -d`。

### 3.6 Docker / Docker Compose

| 平台 | 操作 |
|---|---|
| Linux 服务器 | packager 把 docker-compose 静态二进制 + 一份系统 docker.repo 放进 vendor/；首启 `chayuan doctor --fix-docker` 引导用户装 daemon |
| Linux 单机 | 默认不依赖 docker；onlyoffice 等需要 docker 的功能默认关闭 |
| Windows 桌面 | packager 检测 `Docker Desktop` 是否在 `%ProgramFiles%\Docker\Docker\` → 没有就弹引导链接 |
| macOS 桌面 | 同上，检 `/Applications/Docker.app/` |

---

## 4. 模型的"投放说明"

模型文件 = 数据；对 packager 来说只是"特殊的 vendor"。约定：

```
models/
├── chat/<Org>--<Model>/{config.json, model.safetensors / *.gguf, ...}
├── embedding/<Org>--<Model>/...
├── rerank/<Org>--<Model>/...
├── clip/<Org>--<Model>/...
├── t2i/<Org>--<Model>/{model_index.json, ...}
├── t2v/<Org>--<Model>/...
├── tts/<Org>--<Model>/...
├── asr/<Org>--<Model>/...
└── ocr/<Org>--<Model>/...
```

* 目录名用 `Org--Model`（双连字符），与 chayuan-identify 的"路径回退"规则匹配；
* `chayuan-discovery` 每 60s 扫一次，新加目录自动出现在 `GET /v1/models` 和 UI 下拉；
* 单文件 GGUF 也行：`models/chat/Qwen--Qwen2.5-3B-Instruct-GGUF/qwen2.5-3b-instruct-q4_k_m.gguf`，
  `chayuan-identify` 会从 magic + metadata 识别。

### 拉模型最快路径

```bash
# 用 chayuan-cli（前提：venv 已装 chayuan_cli）
chayuan ai-platform model pull Qwen/Qwen2.5-3B-Instruct-GGUF

# 或用 packager（适合"打包前先准备模型"）
chayuan-pack fetch --release lite       # 把所有 release=lite 用到的模型拉到 .cache
chayuan-pack stage  --release lite       # 解压到 staging/models/...
```

---

## 5. 不同平台 / 架构的特殊点

| 平台 | 特殊处理 |
|---|---|
| **linux-x86_64** | 全栈支持；首选；CI 主跑 |
| **linux-arm64**  | postgres / milvus 部分 vendor 没有官方二进制 → 走 docker-compose 兜底 |
| **mac-x86_64**   | postgres EnterpriseDB 是 universal binary；但 Apple Silicon 走 Rosetta 性能差，建议用 mac-arm64 |
| **mac-arm64**    | 不打 vllm（CUDA only）；comfyui 需 MPS 后端，发 standard/pro 时启 |
| **win-x86_64**   | redis 用 tporadowski/redis 的 zip（Memurai 备选）；NSIS 需要 makensis 在 PATH |
| **win-arm64**    | docker-compose / redis 走 x64 emulation；user 需要 Win11 ARM |

---

## 6. 安全策略不让跑怎么办

| 现象 | 修复方向 |
|---|---|
| Win11 SmartScreen 拦截 .exe | 用 EV 代码签名 cert（详见 `packaging/enterprise.md`） |
| 火绒 / 360 / Defender 误杀 | 给二进制加签 + `chayuan doctor --fix-av`（管理员）一键加白 |
| macOS Gatekeeper 不让运行 | Apple Developer Program + notarization |
| 公司禁联网 | 用 `--offline`：所有 vendor / 模型必须已经在 `.cache` 或仓库里 |
| 容器里 docker.sock 没挂 | onlyoffice 等 docker 服务自动跳过；功能降级到只读 |

`chayuan ai-platform doctor` 提供了完整的检查清单 + 修复指引，每个失败项给出
"建议命令"和"何处看更多"。

---

## 7. 集成到 CI（GitHub Actions 示例）

```yaml
# .github/workflows/build-installers.yml
on: { push: { tags: ["v*"] } }
jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        target: [linux, mac, win]
        arch:   [x86_64, arm64]
        release: [lite, standard]
    runs-on: ${{ matrix.target == 'linux' && 'ubuntu-22.04' || matrix.target == 'mac' && 'macos-14' || 'windows-2022' }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: |
          pip install -e packaging/python312
          chayuan-pack fetch  --target ${{ matrix.target }} --arch ${{ matrix.arch }} --release ${{ matrix.release }}
          chayuan-pack stage  --target ${{ matrix.target }} --arch ${{ matrix.arch }} --release ${{ matrix.release }}
          chayuan-pack build  --target ${{ matrix.target }} --arch ${{ matrix.arch }} --release ${{ matrix.release }} --version ${GITHUB_REF#refs/tags/v}
      - uses: actions/upload-artifact@v4
        with:
          name: chayuan-${{ matrix.target }}-${{ matrix.arch }}-${{ matrix.release }}
          path: packaging/python312/build/out/chayuan-*
```

---

## 8. 常见 FAQ

**Q：我家公司限制 GitHub 直连，能换镜像吗？**
A：在 yaml 里直接把 `source: https://your-mirror.example.com/...` 即可；
packager 优先尊重 yaml 的 URL；如果是 `github://owner/repo`，
设置环境变量 `GITHUB_MIRROR=https://ghproxy.cn` packager 也会自动注入。

**Q：能不能把 ai 模型都放本地 NAS，packager 直接拷？**
A：把 yaml 里 `source` 改成 `local://path/to/model`，packager 跳过下载，直接软链。

**Q：安装包到底多大？**
A：lite ≈ 2.6 GB · standard ≈ 7.5 GB · pro ≈ 35 GB（详见 `docs/multimodal-ai-platform-final-plan.md` §9）。

**Q：终端用户能再增加模型吗？**
A：可以。把模型扔到 `<CHAYUAN_ROOT>/models/<category>/<Org>--<Model>/`，
60s 内 UI 下拉自动出现。无需重启。

**Q：端口最终是哪个？密码是哪个？**
A：`chayuan ai-platform service info` 一条命令打全部端点 + 账号 + 密码（默认掩码，
加 `--reveal` 显式打印）。

---

## 9. 关键文件索引

| 文件 | 作用 |
|---|---|
| `layout.yaml` | 单一约定：什么平台/架构 → 该放什么二进制 |
| `chayuan_packaging/pack.py` | Click CLI（fetch/stage/build/audit/sbom/clean） |
| `chayuan_packaging/manifest.py` | yaml → dataclass |
| `chayuan_packaging/platform_info.py` | 6 种平台 key |
| `chayuan_packaging/fetchers/*.py` | github / http / hf-mirror / local 4 类下载器 |
| `chayuan_packaging/unpack.py` | tar.gz / zip / tar.gz+make 解压 |
| `chayuan_packaging/targets/*.py` | linux/mac/win 三类 finalizer |
