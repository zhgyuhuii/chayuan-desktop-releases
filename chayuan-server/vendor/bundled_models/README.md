# vendor/bundled_models — 项目内固定模型槽

把模型文件**固定**放在这个目录下,调试时直接被扫到、打包时随 exe 一同分发、
安装时种到用户数据目录。所有路径都在 server 仓库内部,**不要**把这里的内容
拷到 `chayuan-client` 或 `chayuan` 仓库。

---

## 目录

- [目录约定](#目录约定)
- [推荐模型清单与下载地址](#推荐模型清单与下载地址)
- [下载方法](#下载方法)
- [模型格式速查](#模型格式速查)
- [四种工作模式](#四种工作模式)
- [解析优先级](#解析优先级)
- [打包流程](#打包流程)
- [调试模型](#调试模型)
- [不该放什么](#不该放什么)
- [提交注意事项](#提交注意事项)

---

## 目录约定

按 capability 分子目录存放,目录名 = capability tag:

```
vendor/bundled_models/
├── chat/        <repo>/...gguf | tokenizer.json + ...
├── embedding/   <repo>/...gguf | model.onnx + ...
├── rerank/      <repo>/tokenizer.json + ...
├── asr/         whisper-*.bin | model.onnx
├── tts/         piper/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx + .onnx.json
├── ocr/         det.onnx + rec.onnx + ...
├── image/       stable-diffusion-*.gguf | safetensors
└── custom/      其它自定义(capability 由文件名/标志推断)
```

**摆法**:

- 单文件模型:直接放 `*.gguf` / `*.onnx`,如 `chat/qwen3-4b-q4_k_m.gguf`。
- 多文件仓库:放一个**子目录**,里面带 `config.json` / `tokenizer.json` /
  `model_index.json` / `manifest.json` 任一标志文件即视为完整仓库。

扫描深度上限 5 层,扫描器遇到带标志文件的目录就停止下钻,避免把仓库内
子目录误识别。

---

## 推荐模型清单 (实测 2026-05-14 全部 200 可下载)

> ⚠ **Windows installer 单文件 ≥ 2 GB 直接炸**
>
> NSIS makensis 32-bit mmap 上限 = 2 GiB;WiX 3 light.exe LGHT0263 硬编码
> INT32_MAX = 2 GiB。两条 Windows installer 路都过不去。集成版打包要在
> `vendor/bundled_models/` 里**严格保持每个单文件 < 2 GB**。下表用 ⚠ 标的
> 行就是会触雷的尺寸,**集成版打包**别用,只能放进 install_job 在线下载。
>
> **快速换装**(一键替换 chat / embedding / reranker 为瘦身默认集):
>
> ```powershell
> # Windows
> .\scripts\install-bundled-models.ps1 -Clean
> ```
>
> ```bash
> # Linux / Mac(或 Windows 上直接调 .py)
> python scripts/install-bundled-models.py --clean
> ```
>
> 脚本自带 `HF_ENDPOINT=https://hf-mirror.com`,跳过 `hf` CLI / 环境变量
> 各种坑;镜像挂了用 `-Endpoint https://huggingface.co` 直连官方。`build.py
> --sync-bundled-only` 也加了 size-guard,撞 2 GB 在 sync 阶段就 abort,
> 不会让你等到 makensis / light.exe 才看见。

下表每一行的 HF 仓库 + 具体文件名都在 `hf-mirror.com` 上**实际 HEAD 探测过返回 200**。
每条都给出探测时拿到的真实 `Content-Length`,与官方页面尺寸对得上。
**release 列**只是参考(说明这个模型属于"轻量 / 标准 / 专业"哪一档),不是说
跑某条命令就会自动放进 `vendor/bundled_models/` — 实际下载请看下一节"下载方法"。

> ⚠ 不要随便填 `Qwen/Qwen3-4B-Instruct-GGUF` 或 `Qwen/Qwen3-7B-Instruct-GGUF`
> 这两个仓库 — **不存在** / gated,HF API 返 401。下面表里给出的是经过实测、能
> 直接 `wget` 下来的 official 或社区镜像仓库。

| capability | HF 仓库(实测 200) | 文件(单文件)或整仓 | 真实大小 | release |
|---|---|---|---|---|
| chat **(集成版默认)** | `unsloth/Qwen3-4B-Instruct-2507-GGUF` | `Qwen3-4B-Instruct-2507-Q3_K_S.gguf` | ~1.85 GB | lite / standard / pro |
| chat (在线下载 lite 用,质量更好) | `unsloth/Qwen3-4B-Instruct-2507-GGUF` | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` ⚠ ≥ 2 GB,集成版打不进 | 2.33 GB | install_job 在线拉,**不放 vendor** |
| chat (备选,官方,内存 < 6 GB) | `Qwen/Qwen2.5-3B-Instruct-GGUF` | `qwen2.5-3b-instruct-q4_k_m.gguf` | 1.96 GB | lite 备选 |
| chat (pro 在线下载,16 GB+ 内存) | `bartowski/Qwen2.5-7B-Instruct-GGUF` | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` ⚠ ≥ 2 GB | 4.36 GB | install_job 在线拉,**不放 vendor** |
| embedding **(集成版默认)** | `Alibaba-NLP/gte-multilingual-base` | 整仓 | ~1.22 GB | lite / standard / pro |
| embedding (备选,多功能 hybrid) | `BAAI/bge-m3` | 整仓(`model.safetensors` ~2.27 GB ⚠) | ~2.27 GB 单文件,**集成版打不进**;在线下载可用 | install_job 在线拉 |
| embedding (备选,中文偏置) | `BAAI/bge-large-zh-v1.5` | 整仓 | ~1.4 GB | standard / pro |
| rerank **(集成版默认)** | `Alibaba-NLP/gte-multilingual-reranker-base` | 整仓 | ~584 MB | lite / standard / pro |
| rerank (备选,多语种强,在线下载) | `BAAI/bge-reranker-v2-m3` | 整仓(主权重 `model.safetensors` ⚠ ≥ 2 GB) | 2.12 GB | install_job 在线拉,**不放 vendor** |
| rerank (备选,瘦身) | `jinaai/jina-reranker-v2-base-multilingual` | 整仓 | 531 MB | lite 备选 |
| rerank (备选,bge 家族 v1) | `BAAI/bge-reranker-base` | 整仓 | 1.04 GB | (按需) |
| rerank (备选,极小,仅英文) | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 整仓 | 87 MB | (英文场景) |
| asr | `ggerganov/whisper.cpp` | `ggml-tiny.bin` | 74 MB | lite / standard / pro |
| asr (备选,更准) | `ggerganov/whisper.cpp` | `ggml-base.bin` | 142 MB | standard 备选 |
| asr (备选,最准) | `ggerganov/whisper.cpp` | `ggml-small.bin` | 465 MB | pro 备选 |
| ocr | `SWHL/RapidOCR` | `PP-OCRv4/ch_PP-OCRv4_det_infer.onnx` + `..._rec_infer.onnx`(配 `PP-OCRv3/ch_ppocr_mobile_v2.0_cls_train.onnx`) | 共 ~16 MB | standard / pro |

**为什么 chat 行有 3 个而不是 1 个**:
* `unsloth/Qwen3-4B-Instruct-2507-GGUF` — 最新 Qwen3 系列(2025-07 发布),
  社区单文件 GGUF 镜像,2.3 GB,**推荐默认**。
* `Qwen/Qwen2.5-3B-Instruct-GGUF` — Qwen 官方,2024 年的 Qwen2.5,
  内存 < 6 GB 的机器跑这个。
* `bartowski/Qwen2.5-7B-Instruct-GGUF` — Qwen 官方 `Qwen/Qwen2.5-7B-Instruct-GGUF`
  把 7B 拆成 2 个分片不便单文件 wget,bartowski 这个社区镜像把它打回单文件,
  4.4 GB,**16 GB 内存机器才跑得动**。

**为什么 rerank 行有 5 个**(默认 `bge-reranker-v2-m3` 2.12 GB,如果嫌大可以换):

| 候选 | 大小 | 语言 | 何时选 |
|---|---|---|---|
| `BAAI/bge-reranker-v2-m3` | 2.12 GB | 100+ 语种 | **默认**,多语种 + 中文办公强,跟 `bge-m3` embedding 同家族对齐 |
| `Alibaba-NLP/gte-multilingual-reranker-base` | 584 MB | 中英 + 70+ 语种 | **首选瘦身**,Alibaba 出品,同档质量,体积 1/4 |
| `jinaai/jina-reranker-v2-base-multilingual` | 531 MB | 中英 + 多语种 | 再小一点,Jina 出品 |
| `BAAI/bge-reranker-base` | 1.04 GB | 中英 | bge 家族 v1,想保家族一致性时用 |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | 87 MB | **仅英文** | 极致小,中文几乎不可用,仅纯英文场景 |

挑选思路:
- 中文 RAG 优先级 = 召回质量 > 体积,默认 `bge-reranker-v2-m3` 不要换;
- 想让 standard 包瘦身、又不想牺牲中文,换 `gte-multilingual-reranker-base`;
- 内网 / 带宽极差,牺牲一点中文质量也行,可换 `jinaai/jina-reranker-v2-base-multilingual`;
- 纯英文场景(察元几乎不存在),才考虑 `ms-marco-MiniLM-L-6-v2`。

**自己加模型**:不在表里的随便加,放进对应 `<cap>/` 目录且符合
[模型格式速查](#模型格式速查)的格式即可被扫描到。

**所有 HF 仓库都可以用国内镜像 `hf-mirror.com` 替换 `huggingface.co`**,速度快、
无需翻墙、协议格式完全一致。下面的下载命令默认就走镜像。

---

## 下载方法

> **重要**: `vendor/bundled_models/` 是手工组装的目录。`chayuan_packaging
> fetch/stage` 是另一个独立流程(把全 release 资源解到
> `packaging/python312/build/staging-.../`),**不会**写到本目录,别用错
> 工具。下面给三种把模型直接放进 `vendor/bundled_models/<cap>/` 的可执行
> 方法,任选其一。

> 下面的命令都假设你已经 `cd` 到 chayuan-server 仓库根(也就是
> `vendor/bundled_models/` 的父目录的父目录)。Windows 把 `/` 换成 `\` 也
> 行,PowerShell 两种斜杠都接受。

### 跨平台速查

| 操作 | macOS / Linux (bash) | Windows (PowerShell) | Windows (cmd.exe) |
|---|---|---|---|
| 设环境变量(本次会话) | `export HF_ENDPOINT=https://hf-mirror.com` | `$env:HF_ENDPOINT = "https://hf-mirror.com"` | `set HF_ENDPOINT=https://hf-mirror.com` |
| 建目录 | `mkdir -p vendor/bundled_models/chat` | `New-Item -ItemType Directory -Force vendor\bundled_models\chat` | `mkdir vendor\bundled_models\chat` |
| 下载到指定文件 | `wget -c -O <out> <url>` | `Invoke-WebRequest -OutFile <out> <url>` 或 `curl.exe -L -o <out> <url>` | `curl.exe -L -o <out> <url>` |
| 行尾续行字符 | 反斜杠 `\` | 反引号 (`` ` ``) | 脱字符 `^` |

> Windows 10+ 自带 `curl.exe`(注意要带 `.exe`,否则 PowerShell 会拦截
> 到 `Invoke-WebRequest` 别名,语义完全不同)。本文档下面所有 wget 例子
> 在 Windows 用 `curl.exe -L -o ...` 替代即可。

---

### 方法 A — `hf`(推荐,三平台都最稳)

> **2025 起的命令名**:旧版 `huggingface-cli download` 已被官方废弃,跑会
> 输出 `Warning: huggingface-cli is deprecated and no longer works. Use hf instead.`。
> 新命令名叫 `hf`(由 `huggingface_hub` Python 包附带),子命令和参数完全沿用 —
> `hf download <repo> [<file>] --local-dir <dir>`。

需要 Python 环境(或用 standalone installer 装独立 `hf` 二进制),断点
续传、自动校验、支持单文件 / 整仓。命令在三平台**完全一致**,差别只在
前一行的"设镜像 endpoint"。

#### A.1 装 `hf` CLI

两种安装方式,任选其一。**国内网络推荐 (a) pip**,因为 (b) standalone installer
的脚本来自 `hf.co`,国内常常无法连通(典型报错:`irm : 无法连接到远程服务器`,或
`curl: Could not resolve host: hf.co`)。

**(a) pip 安装(三平台通吃,国内可达,推荐)**

```bash
# 三平台一致,新版 cli 内置在 huggingface_hub 主包里,无需 [cli] extra
pip install -U "huggingface_hub"
```

走 PyPI 中国镜像可加 `-i` 提速:

```bash
pip install -U "huggingface_hub" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**(b) Standalone installer(独立二进制,无需 Python — 仅在能直连 `hf.co` 时可用)**

```bash
# macOS / Linux:
curl -LsSf https://hf.co/cli/install.sh | bash
```

```powershell
# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://hf.co/cli/install.ps1 | iex"
```

#### A.2 设镜像 endpoint

> ⚠ **关键坑**:`$env:` / `export` / `set` 设的环境变量**只在当前 shell 会话生效**。
> 新开一个 PowerShell / 切换 conda env(`(base)` ↔ `(py312)` 等)就没有了,
> 下载会再次报 `ConnectTimeout` 或 `cannot find the requested files in the
> local cache`。
>
> 想一劳永逸,看下面"永久生效"段。每次新开 shell 也不嫌烦的,跳过即可。

**(临时) 当前会话生效**

macOS / Linux:
```bash
export HF_ENDPOINT=https://hf-mirror.com
```

Windows (PowerShell):
```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
```

Windows (cmd.exe):
```cmd
set HF_ENDPOINT=https://hf-mirror.com
```

**(永久) 用户级,影响所有新开 shell / 子进程**

macOS / Linux(把这行加到 `~/.bashrc` 或 `~/.zshrc`,然后 `source` 一下):
```bash
echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.bashrc
source ~/.bashrc
```

Windows (PowerShell,设过后**关掉当前窗口、新开一个**才会读到):
```powershell
[Environment]::SetEnvironmentVariable("HF_ENDPOINT", "https://hf-mirror.com", "User")
# 验证(新开 PS 窗口里跑):
# echo $env:HF_ENDPOINT  →  应输出 https://hf-mirror.com
```

Windows (cmd.exe,同样要新开窗口才生效):
```cmd
setx HF_ENDPOINT "https://hf-mirror.com"
```

**(诊断) 怀疑环境变量没生效?**

```bash
# 1) 看当前 shell 是否设上(三平台用各自的 echo 命令)
echo $HF_ENDPOINT           # bash / zsh
echo $env:HF_ENDPOINT        # PowerShell
echo %HF_ENDPOINT%           # cmd.exe

# 2) 开 hf CLI 的 debug 日志,看实际请求哪个域名
#    PowerShell:
$env:HF_DEBUG = "1"
hf download <repo> ...
# 看 traceback 里的 URL:
#   出现 hf-mirror.com → endpoint 生效,问题在别处
#   仍是 huggingface.co → 环境变量没传到 hf 进程
```

#### A.3 下载推荐组合(lite 套件:chat + embedding + rerank + asr)

> ⚠ **重要**:`hf download` **没有 `--endpoint` 命令行参数**,只认环境
> 变量 `HF_ENDPOINT`。**如果跳过 A.2 直接跑下载,国内必然 `ConnectTimeout`**
> (典型错误:`Got: ConnectTimeout: [WinError 10060] 由于连接方在一段时间后
> 没有正确答复或连接的主机没有反应,连接尝试失败`)。下面每个平台的代码块
> 都在第一行重复设上镜像,直接整段复制粘贴就行。

下面四条命令实测可用(repo + 文件名 + URL 全部 HEAD 200)。Windows 用 `\` 也行,
这里统一写 `/`,PowerShell / cmd 都接受。

**macOS / Linux (bash):**

```bash
export HF_ENDPOINT=https://hf-mirror.com

# chat: Qwen3-4B-Instruct-2507 Q4_K_M(单文件 2.33 GB,推荐默认)
hf download unsloth/Qwen3-4B-Instruct-2507-GGUF Qwen3-4B-Instruct-2507-Q4_K_M.gguf --local-dir vendor/bundled_models/chat

# embedding: bge-m3(整仓,多文件)
hf download BAAI/bge-m3 --local-dir vendor/bundled_models/embedding/bge-m3

# rerank: bge-reranker-v2-m3(整仓)
hf download BAAI/bge-reranker-v2-m3 --local-dir vendor/bundled_models/rerank/bge-reranker-v2-m3

# asr: whisper-tiny(单文件 74 MB)
hf download ggerganov/whisper.cpp ggml-tiny.bin --local-dir vendor/bundled_models/asr
```

**Windows (PowerShell):**

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"

# chat
hf download unsloth/Qwen3-4B-Instruct-2507-GGUF Qwen3-4B-Instruct-2507-Q4_K_M.gguf --local-dir vendor/bundled_models/chat

# embedding
hf download BAAI/bge-m3 --local-dir vendor/bundled_models/embedding/bge-m3

# rerank
hf download BAAI/bge-reranker-v2-m3 --local-dir vendor/bundled_models/rerank/bge-reranker-v2-m3

# asr
hf download ggerganov/whisper.cpp ggml-tiny.bin --local-dir vendor/bundled_models/asr
```

**Windows (cmd.exe):**

```cmd
set HF_ENDPOINT=https://hf-mirror.com

hf download unsloth/Qwen3-4B-Instruct-2507-GGUF Qwen3-4B-Instruct-2507-Q4_K_M.gguf --local-dir vendor/bundled_models/chat
hf download BAAI/bge-m3 --local-dir vendor/bundled_models/embedding/bge-m3
hf download BAAI/bge-reranker-v2-m3 --local-dir vendor/bundled_models/rerank/bge-reranker-v2-m3
hf download ggerganov/whisper.cpp ggml-tiny.bin --local-dir vendor/bundled_models/asr
```

#### A.3.1 替换 chat 模型(内存 < 6 GB 或 ≥ 16 GB 才需要)

设过 `HF_ENDPOINT` 后再跑:

```bash
# 内存只够 4 GB:换 Qwen2.5-3B(1.96 GB)
hf download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf --local-dir vendor/bundled_models/chat

# 16 GB+ 想要更强对话:换 Qwen2.5-7B(4.36 GB 单文件)
hf download bartowski/Qwen2.5-7B-Instruct-GGUF Qwen2.5-7B-Instruct-Q4_K_M.gguf --local-dir vendor/bundled_models/chat
```

#### A.3.2 替换 rerank 模型(想瘦身或换厂商时)

默认 `BAAI/bge-reranker-v2-m3` 2.12 GB。如果嫌大,先删掉 `vendor/bundled_models/
rerank/bge-reranker-v2-m3/`,再选一个备选下载(设过 `HF_ENDPOINT` 后跑):

```bash
# 首选瘦身:Alibaba gte-multilingual-reranker-base(584 MB,中英+多语种)
hf download Alibaba-NLP/gte-multilingual-reranker-base --local-dir vendor/bundled_models/rerank/gte-multilingual-reranker-base

# 再小:Jina v2 base multilingual(531 MB)
hf download jinaai/jina-reranker-v2-base-multilingual --local-dir vendor/bundled_models/rerank/jina-reranker-v2-base-multilingual

# bge 家族 v1(1.04 GB,中英)
hf download BAAI/bge-reranker-base --local-dir vendor/bundled_models/rerank/bge-reranker-base

# 极致小(87 MB),仅英文场景才用
hf download cross-encoder/ms-marco-MiniLM-L-6-v2 --local-dir vendor/bundled_models/rerank/ms-marco-MiniLM-L-6-v2
```

各候选的取舍详见上方主表的「为什么 rerank 行有 5 个」对比。

#### A.4 验证

三平台都用 Python 跑体检脚本:

```bash
python scripts/check-bundled-models.py
```

**注意 `--local-dir` 的语义**:`hf download` 把文件直接放到 `--local-dir`
指向的目录(不会自动加 `<org>/<repo>/` 前缀)。所以:

- 单文件场景:`--local-dir vendor/bundled_models/chat`,文件落到
  `vendor/bundled_models/chat/qwen3-4b-instruct-q4_k_m.gguf`。
- 整仓场景:**自己**带上仓库名作为最后一段(例如
  `--local-dir vendor/bundled_models/embedding/bge-m3`),所有文件落进这个
  目录,目录名就是后端识别用的 `model_id`。

---

### 方法 B — 直链下载(无 Python 环境)

直链格式都一样:`https://hf-mirror.com/<org>/<repo>/resolve/main/<file>`,
三平台只是工具名不同。

#### macOS / Linux(`wget`,自带或一行 brew/apt 装)

```bash
# chat: Qwen3-4B Q4_K_M(2.33 GB)
mkdir -p vendor/bundled_models/chat
wget -c -O vendor/bundled_models/chat/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  'https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'

# asr: whisper-tiny(74 MB)
mkdir -p vendor/bundled_models/asr
wget -c -O vendor/bundled_models/asr/ggml-tiny.bin \
  'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin'
```

`wget -c` = 断点续传,网络断了再跑一次会从中断处继续。

#### Windows (PowerShell,`curl.exe` Win10+ 自带)

```powershell
# chat
New-Item -ItemType Directory -Force vendor\bundled_models\chat | Out-Null
curl.exe -L -C - -o vendor\bundled_models\chat\Qwen3-4B-Instruct-2507-Q4_K_M.gguf `
  https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf

# asr
New-Item -ItemType Directory -Force vendor\bundled_models\asr | Out-Null
curl.exe -L -C - -o vendor\bundled_models\asr\ggml-tiny.bin `
  https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin
```

`-L` 跟重定向(HF 镜像会 302 到 CDN),`-C -` 断点续传。**一定要写 `curl.exe`**,
不要省略 `.exe`,否则 PowerShell 把 `curl` 当 `Invoke-WebRequest` 别名,参数风格
完全不同。

#### Windows (cmd.exe)

```cmd
mkdir vendor\bundled_models\chat
curl.exe -L -C - -o vendor\bundled_models\chat\Qwen3-4B-Instruct-2507-Q4_K_M.gguf https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf

mkdir vendor\bundled_models\asr
curl.exe -L -C - -o vendor\bundled_models\asr\ggml-tiny.bin https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin
```

多文件仓库(embedding / rerank)逐个 wget/curl 较繁琐 — 建议改用方法 A
或方法 C。

#### 浏览器手动下载(任何系统,最简单)

把 URL 直接粘进浏览器地址栏 → 下载 → 拖到对应 `vendor/bundled_models/<cap>/`
子目录里。这是零工具门槛的兜底方案。

---

### 方法 C — `git clone` + git-lfs(整仓克隆)

适合多文件仓库(embedding / rerank / 图像扩散等)。三平台命令一致,只是
git-lfs 的安装方式不同。

#### 安装 git-lfs

| 平台 | 安装命令 |
|---|---|
| macOS | `brew install git-lfs` |
| Ubuntu / Debian | `sudo apt install git-lfs` |
| RHEL / CentOS | `sudo yum install git-lfs` |
| Windows | 在 https://git-lfs.com 下安装包 或 `winget install GitHub.GitLFS` |

#### 克隆(三平台一致)

```bash
git lfs install --skip-smudge

mkdir -p vendor/bundled_models/embedding          # Windows: New-Item -ItemType Directory -Force ...
git clone https://hf-mirror.com/BAAI/bge-m3 vendor/bundled_models/embedding/bge-m3

cd vendor/bundled_models/embedding/bge-m3
git lfs pull                                      # 真正拉权重(可能数 GB,断点续传由 git-lfs 自己管)
```

**坑提示**:不带 `--skip-smudge` 时 `git clone` 会一次拉所有 lfs 文件,
某些仓库会很慢、断点不友好。`--skip-smudge` + 手动 `git lfs pull` 更可控。

---

### 方法 D — ModelScope(完全国内,适合公司内网)

需要在 modelscope.cn 上搜对应仓库名(通常与 HF 同名,但 owner 可能不同)。
三平台都用 pip 装 cli,命令完全一致:

```bash
pip install -U modelscope

# 把 <ORG/REPO> 替换成 modelscope.cn 上的真实仓库名(在站点搜索一下)
modelscope download --model <ORG/REPO> [filename] --local_dir vendor/bundled_models/chat
```

ModelScope 仓库命名与 HF 不完全一致,建议先到 `modelscope.cn` 搜索确认。
比如 Qwen 系列通常是 `Qwen/...` 同名,BAAI 系列在 ModelScope 上常见镜像
账号是 `BAAI` 或 `AI-ModelScope`。

---

## 关于 `chayuan_packaging fetch`(不是给 bundled_models 用的)

`packaging/python312/chayuan_packaging` 是**整 release 打包工具**,它的
`fetch`/`stage` 命令:

- `fetch` 把 layout.yaml 列出的 runtime + service + 模型按 sha256 缓存到
  `packaging/python312/.cache/`;
- `stage` 把 cache 解压到 `packaging/python312/build/staging-<release>-<plat>/`,
  这个目录是给"完整 tarball + 后续 build/dmg/installer 阶段"用的;
- 既不会落到 `chayuan-server/vendor/bundled_models/`,也不会被 PyInstaller
  spec 自动嵌入。

只有你在用旧的 staging 风格打包,或者写脚本把 staging 产物批量复制到
`vendor/bundled_models/` 时,这个工具才相关。日常往 bundled_models 里加
模型 — 用上面的方法 A/B/C/D。

---

## 模型格式速查

不同 capability 的后端 runtime 期望不同格式,放错位置 / 错格式扫描器会
把 `capability` 误判成 `other`,启动时不会被默认模型 promote 选中。

| 格式 | 后缀 | 典型 capability | 后端 runtime | 标志文件 |
|---|---|---|---|---|
| GGUF | `.gguf` | chat / embedding | llama-cpp / llama-server | 单文件即可 |
| HF transformers | (目录) | embedding / rerank / 多模态 | infinity-emb / transformers | `config.json` + `tokenizer.json` + 权重 |
| safetensors | `.safetensors` | chat / image / 通用 | transformers / diffusers | 通常配 `config.json` |
| ONNX | `.onnx` | embedding / rerank / OCR | onnxruntime | 通常配 `tokenizer.json` 或 `manifest.json` |
| whisper.cpp ggml | `ggml-*.bin` | asr | whisper.cpp | 单文件,无标志文件 |
| PaddleOCR ONNX | `det.onnx`+`rec.onnx`+`cls.onnx` | ocr | rapidocr-onnxruntime | 三件套同目录 |

**经验**:

- 同一模型有 GGUF / safetensors 两版,**桌面端优先用 GGUF**(单文件、内存
  友好、llama-cpp 跨平台)。
- 量化等级 `q4_k_m` 是甜区(精度损失 < 1%,体积 ~原 16bit 的 30%)。
- embedding / rerank 选 `bge-*` 家族;同家族搭配检索效果最好。

---

## 四种工作模式

### 1. 开发态(dev mount)

server 启动时 `first_launch.run_first_launch_hooks()` 会扫
`<chayuan-server>/vendor/bundled_models/`,把内容 seed 到
`<CHAYUAN_ROOT>/models/bundled/`,再 `scan_once` 写入 local_index。
`pnpm run dev` 起 sidecar 一次,这些模型就能在对话框下拉里看到了。

```
$ chayuan model status
local_index:
  - models/bundled/chat/qwen3-4b-q4_k_m.gguf    (capability=chat, source=models)
  - models/bundled/embedding/bge-m3              (capability=text-embedding)
```

### 2. 打包态(Tauri resources,**2026-05-15 改动**)

> 历史:之前 `chayuan-server.spec` 把 `bundled_models` 作为 PyInstaller
> `datas` 嵌进 sidecar exe,导致 sidecar ≥ 2 GB → NSIS makensis mmap 上限
> → 集成版 installer 打不出。现已改路。

**新方案**:模型由 Tauri 的 `bundle.resources` 字段承载:

1. `build.py` 跑完 PyInstaller 拷 sidecar 后,把 `vendor/bundled_models/`
   整树同步到 `chayuan-client/apps/desktop/src-tauri/bundled_models/`
   (`CHAYUAN_LITE_BUILD=1` 时改为清空 → 轻量版)。
2. `tauri.conf.json` 里声明 `"resources": ["bundled_models/**/*"]`,
   Tauri 把这些文件打进 NSIS installer。
3. 装机后落在 `<install_dir>/bundled_models/`,跟 sidecar exe 平级。
4. sidecar 通过 `bundled_models_dir()` 的"exe 同级 fallback"找到它
   (`Path(sys.argv[0]).parent / "bundled_models"`)。

好处:sidecar 永远瘦(~600 MB),NSIS 单文件没瓶颈;两个 flavor 共享同一份
sidecar,build script 不再为了切 flavor 而 rebuild PyInstaller。

打包脚本依 layout.yaml `releases.<name>.models` 字段决定**哪些**模型
纳入哪个 release,详见 [打包流程](#打包流程)。

### 3. 安装态(auto-seed)

首启时 `seed_bundled_models()` 把 Tauri resources 里的模型(或 dev 模式下
的 `vendor/bundled_models/`)拷到 `<CHAYUAN_ROOT>/models/bundled/`,
**idempotent**:目标已存在且大小一致则跳过;用户改动过 mtime 的文件不覆盖。

### 4. 替换态(runtime swap)

用户在 `<CHAYUAN_ROOT>/models/bundled/<cap>/` 下替换或新增任何文件,
下次 server 启动(或运行 `chayuan model scan`)`scan_once` 自动识别
并写入 `local_models.json`,无需重启电脑。

---

## 解析优先级

`local_index.bundled_models_dir()` 按以下顺序找根目录:

1. 环境变量 `CHAYUAN_BUNDLED_MODELS_DIR`(测试 / 特殊部署专用)
2. PyInstaller 运行时:`sys._MEIPASS / "bundled_models"`(2026-05-15 后 spec
   已不再嵌,此条仅留作历史兼容;新装机包不会命中)
3. 仓库 dev 模式:`<chayuan-server>/vendor/bundled_models`
4. exe 同级:`<argv0 dir>/bundled_models`(**Tauri NSIS 集成版的真路径**,
   也兼容便携安装)

返回第一个**存在**的路径;都不存在则返回 `None`,扫描和 seed 都跳过。

---

## 打包流程

完整的打包链:

```
fetch (拉模型/runtime → .cache/)
   ↓
stage (解压到 vendor/bundled_models + vendor/runtimes/)
   ↓
pyinstaller (生成 onefile sidecar,内嵌 vendor/bundled_models)
   ↓
build.py (拷 sidecar 到 chayuan-client/apps/desktop/binaries/)
   ↓
tauri build (生成 .dmg / .msi / .deb)
```

### 一键打包(推荐)

```bash
cd chayuan-server
# 把指定 release 的所有资源拉好 + 解压
python -m chayuan_packaging fetch --target linux --arch x86_64 --release standard
python -m chayuan_packaging stage  --target linux --arch x86_64 --release standard

# 生成 sidecar(产物在 dist/chayuan-server[.exe])
poetry run python packaging/pyinstaller/build.py
```

`build.py` 内部跑 `poetry run pyinstaller packaging/pyinstaller/chayuan-server.spec
--noconfirm`,再把 `dist/chayuan-server*` 拷到
`chayuan-client/apps/desktop/src-tauri/binaries/`(并按 Tauri sidecar
命名约定加 `-${triple}` 后缀)。

### 单独验证打包

打包前先确认 layout / spec 没漏配:

```bash
python -m chayuan_packaging audit --release standard
poetry run python -m py_compile packaging/pyinstaller/chayuan-server.spec
```

### 跨平台

PyInstaller 不能交叉编译,每个目标 OS / arch 在原生机器上跑一次:

| 平台 | 命令 |
|---|---|
| macOS arm64 | `python -m chayuan_packaging fetch --target macos --arch arm64 --release standard` |
| macOS x86_64 | `python -m chayuan_packaging fetch --target macos --arch x86_64 --release standard` |
| Linux x86_64 | `python -m chayuan_packaging fetch --target linux --arch x86_64 --release standard` |
| Windows x86_64 | `python -m chayuan_packaging fetch --target windows --arch x86_64 --release standard` |

### release 选择

| release | 包大小 | 模型 | 推荐场景 |
|---|---|---|---|
| `lite` | ~3.5 GB | 4B chat + bge-m3 + reranker + whisper-tiny | 个人单机,8GB 内存机器 |
| `standard` | ~5 GB | lite + RapidOCR + bge-large 备选 | 服务器多用户、有 OCR 需求 |
| `pro` | ~8 GB | standard + 7B chat | 16GB+ 内存,高质量对话 |

### 不带模型的打包(用户自配)

如果想做"空"安装包(完全靠 BootstrapBanner 引导用户后下载),把
`vendor/bundled_models/<cap>/` 下的权重文件都删掉(留 `.gitkeep` 占位),
再跑 pyinstaller — spec 看到目录里没有真权重就 `bundled_models_dir()`
返回也可能为 None / 空,seed 步骤跳过。安装后用户在「设置 → 默认模型」
顶部的 BootstrapBanner 选「下载 standard 包」由 server 后台拉。

---

## 调试模型

### 一站式自检 — `chayuan model status`

最常用,一条命令把"扫描 → 候选 → 当前默认 → 推理引擎启动参数"都打出来:

```bash
chayuan model status              # 人类可读
chayuan model status --json       # 给脚本消费(jq 解析方便)
chayuan model status --no-scan    # 不重扫,直接读 local_models.json
```

输出三段:

1. **local_index**:磁盘上扫到的模型列表(model_id / capability / 路径 / 大小)
2. **capability_defaults**:9 类 capability 当前默认指向哪个模型
3. **process_args**:llama-server / infinity / piper 进程实际收到的
   `--model` 参数;`missing` 表示 capability 没指好

### 强制重扫 — `chayuan model scan`

放新模型 / 删除旧模型后,让 local_index 立刻知道:

```bash
chayuan model scan
chayuan model scan --verbose  # 看每个扫描根 + 每条 entry 的 identify 过程
```

### 列出 / 导入 / 下载 — 其它 model 子命令

```bash
chayuan model list                  # 只列已识别的本地模型
chayuan model import /path/to/model # 软链外部路径到 <CHAYUAN_ROOT>/models/custom/
chayuan model download <id>         # 按 layout.yaml 配置远程下载单个模型
```

### 看模型是否真能被推理引擎调用

写入扫描表只是第一步,推理引擎(llama-server / infinity / ...)能不能
真起来,要看 supervisor 日志:

```bash
# server 启动后看 supervisor 进程状态
chayuan supervisor status

# 看每个进程的 stdout / stderr
chayuan supervisor logs llama-server --tail 200
chayuan supervisor logs infinity-emb --tail 200

# 手动用单进程跑一下(打包到 vendor/runtimes/llama-cpp/bin/llama-server)
./vendor/runtimes/llama-cpp/bin/llama-server \
  --model <CHAYUAN_ROOT>/models/bundled/chat/qwen3-4b-q4_k_m.gguf \
  --port 18080 --host 127.0.0.1
```

### 直接 curl 测试 chat completion

server 跑起来后,从 `/v1/models` 看模型有没有出现,再用 OpenAI 兼容
接口测一次对话:

```bash
# 1. 模型已注册?
curl -s http://127.0.0.1:8000/v1/models | jq '.data[] | select(.platform_name=="local")'

# 2. 一次对话
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "bundled/chat/qwen3-4b-q4_k_m.gguf",
    "messages": [{"role":"user","content":"你好"}]
  }' | jq
```

### 常见诊断

| 现象 | 排查 |
|---|---|
| 对话框没有"本地模型"分组 | `chayuan model scan && chayuan model status` 看 local_index 是否扫到 |
| 扫到了但 capability=other | 标志文件缺失,或文件名识别不到家族关键字。摆到 `custom/` 下并加 `manifest.json` |
| capability 对但 default 没指到本地 | 设置页里手选;或 `chayuan model status` 看 `auto_assign` 是不是被云厂商抢先 |
| llama-server 起不来 | `supervisor logs llama-server` 看具体错;常见是模型路径不对 / 端口被占 / 内存不足 |
| 单元测试访问不到 bundled | 测试用 `monkeypatch.setenv('CHAYUAN_BUNDLED_MODELS_DIR', str(fake_dir))` |

---

## 不该放什么

- 大于 layout.yaml release 上限的模型(standard 单文件 ≤ 3GB;pro 不限)
- 任何 `.env`、`*.key`、`*.pem` 或私钥
- 非 AGPL / Apache / MIT 等开源协议的权重
- 训练 checkpoint 中间文件(只放可直接推理的最终 weights)

---

## 提交注意事项

git 默认会忽略 `*.gguf` / `*.safetensors` / `*.onnx`,以及本目录下的
`*.bin`(见仓库根 `.gitignore`),所以只有 `.gitkeep` / README 入库。

要在 CI 跑集成测试或本地复现打包,先用 `chayuan_packaging fetch
<release>` 把模型预拉到这里,再跑 pytest / pyinstaller。
