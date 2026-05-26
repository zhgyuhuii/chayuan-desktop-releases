# 本地模型运行网关设计规划

## 背景

模型广场已经开始支持从 `hf-mirror.com`、Hugging Face、ModelScope、Civitai 等来源同步模型元数据、导入官方 JSON、分页查询、保留原始字段和多源归并。下一阶段的核心目标是让模型从 `hf-mirror.com` 下载后尽可能自动运行起来。

这个目标不能通过“一个推理框架支持所有模型”实现。Hugging Face 生态包含对话、嵌入、重排、视觉、扩散、视频、语音、TTS、多模态等大量模型族，权重格式、依赖库、显存需求、推理入口都不统一。最优方案是建设一个本地模型运行网关，把下载、识别、运行时选择、进程管理、测试和前端状态统一起来。

## 总体目标

1. 从 `hf-mirror.com` 下载模型后，系统自动扫描模型目录并识别能力类型、权重格式、运行时需求。
2. 对支持的模型自动选择最优运行时，并提供一键运行、停止、测试和健康检查。
3. 对不支持或缺依赖的模型明确展示原因和推荐安装包，不误导用户。
4. 单机部署可用，普通用户不需要部署 Kubernetes 或复杂服务。
5. 服务部署可扩展到远程推理节点，前端和业务 API 不感知本地/远程差异。
6. 集群部署可支持多 GPU、多节点、队列调度、模型热加载和资源隔离。
7. 主安装包保持轻量，运行时按需安装，避免全家桶安装包达到数十 GB。

## 需求范围

### 必须支持的能力类型

| 编号 | 能力 | 统一类型 | 目标体验 |
|---|---|---|---|
| 1 | 对话模型 | `chat` | 下载后可一键启动本地 OpenAI-compatible 服务 |
| 2 | 文字嵌入模型 | `text-embedding` | 下载后可批量生成文本向量 |
| 3 | 图像嵌入模型 | `image-embedding` | 下载后可对图片生成向量 |
| 4 | 重排模型 | `rerank` | 下载后可对 query/document pairs 打分 |
| 5 | 文生图模型 | `text-to-image` | 下载后可输入 prompt 生成图片 |
| 6 | 文生视频模型 | `text-to-video` | 下载后可输入 prompt 生成短视频 |
| 7 | 文生声音模型 | `text-to-audio` | 下载后可输入文本生成语音/音频 |
| 8 | 语音识别模型 | `speech-to-text` | 下载后可上传音频转文字 |
| 9 | 图像识别文字模型 | `image-to-text` | 下载后可上传图片生成描述/OCR/VQA |

### 非目标

- 不承诺 Hugging Face 全站所有模型 100% 下载后可运行。
- 不在主安装包内置所有运行时和 CUDA 依赖。
- 不默认执行模型仓库中的任意远程代码。
- 不把模型文件内置进产品安装包。
- 不把所有推理任务塞进主后端进程。

## 架构原则

1. **控制面和数据面分离**  
   主后端负责模型索引、下载、状态、调度、鉴权和 API 聚合；实际推理运行在独立进程、独立服务或远程 Worker 中。

2. **多运行时适配，而不是单运行时幻想**  
   LLM、Embedding、Diffusion、Whisper、TTS、VLM 的最佳运行技术不同，应通过统一网关封装差异。

3. **下载后自动识别，运行前明确判定**  
   模型下载完成后立即生成 `runtime_manifest.json`，记录可运行性、推荐运行时、资源需求和缺失依赖。

4. **单机优先，向服务和集群自然演进**  
   单机用本地进程管理；服务部署把 Runtime Worker 独立成服务；集群部署增加调度器、队列和资源管理。

5. **运行时按需安装**  
   主安装包只带检测器、网关和轻量运行时。大依赖按能力包安装。

6. **高并发依赖批处理、队列和隔离**  
   文本嵌入/重排适合 batch；LLM 适合常驻服务和流式响应；图像/视频生成适合队列；大模型按进程隔离。

## 总体架构

```text
模型广场
  |
  | 下载 / 运行 / 测试 / 停止
  v
chayuan-server FastAPI 控制面
  |
  |-- model_registry      模型索引、官方 raw JSON、多源归并
  |-- model_downloader    hf-mirror 下载、断点、校验、回写磁盘状态
  |-- local_runtime       运行网关、检测、适配器、状态机
  |-- runtime_store       SQLite 状态、运行配置、健康检查记录
  |
  v
Runtime Manager
  |
  |-- 本地进程: llama.cpp / faster-whisper / ComfyUI / Python worker
  |-- 本地服务: OpenAI-compatible server / HTTP adapter
  |-- 远程 Worker: runtime-worker API
  |-- 集群 Worker: GPU node / queue / scheduler
```

## 建议模块结构

后端新增：

```text
chayuan.server.local_runtime
├── detector.py              # 扫描模型目录并识别能力、格式、依赖
├── manifest.py              # runtime_manifest.json 读写
├── manager.py               # 启动、停止、健康检查、端口管理
├── scheduler.py             # 本机资源调度、队列、并发限制
├── store.py                 # SQLite 状态表
├── routes.py                # FastAPI routes
├── security.py              # trust_remote_code、路径、命令白名单
├── adapters/
│   ├── base.py              # RuntimeAdapter 抽象
│   ├── llama_cpp.py
│   ├── vllm.py
│   ├── fastembed.py
│   ├── sentence_transformers.py
│   ├── flag_embedding.py
│   ├── transformers_pipeline.py
│   ├── diffusers.py
│   ├── comfyui.py
│   ├── faster_whisper.py
│   ├── kokoro_tts.py
│   └── remote_worker.py
└── workers/
    ├── embedding_worker.py
    ├── rerank_worker.py
    ├── image_embedding_worker.py
    ├── vlm_worker.py
    └── tts_worker.py
```

前端新增或扩展：

```text
packages/api/src/localRuntime.ts
packages/app/src/features/marketplace/runtime/
├── RuntimeStatusBadge.tsx
├── RuntimeActionButtons.tsx
├── RuntimeInstallDialog.tsx
└── RuntimeTestPanel.tsx
```

## API 设计

### 控制面 API

```http
POST /local_runtime/scan
POST /local_runtime/start
POST /local_runtime/stop
GET  /local_runtime/status
GET  /local_runtime/capabilities
GET  /local_runtime/install_packs
POST /local_runtime/install_pack
POST /local_runtime/test
```

### 推理 API

```http
POST /local_runtime/chat
POST /local_runtime/embed_text
POST /local_runtime/embed_image
POST /local_runtime/rerank
POST /local_runtime/text_to_image
POST /local_runtime/text_to_video
POST /local_runtime/text_to_audio
POST /local_runtime/speech_to_text
POST /local_runtime/image_to_text
```

### 运行状态

```json
{
  "model_id": "Qwen/Qwen2.5-7B-Instruct-GGUF",
  "downloaded": true,
  "runnable": true,
  "runtime": "llama.cpp",
  "status": "running",
  "endpoint": "http://127.0.0.1:18081/v1",
  "pid": 12345,
  "device": "cuda:0",
  "memory_required_mb": 6144,
  "max_concurrency": 4,
  "missing": []
}
```

## 模型识别策略

下载完成后扫描模型目录，生成 `runtime_manifest.json`。

### 文件特征

| 特征 | 推断 |
|---|---|
| `*.gguf` | `llama.cpp` 对话模型优先 |
| `modules.json` | `sentence-transformers` 嵌入/重排模型 |
| `model_index.json` | `diffusers` 模型 |
| `config.json` + `*.safetensors` | Transformers/vLLM/Diffusers 候选 |
| `preprocessor_config.json` + `tokenizer.json` | Transformers 管线候选 |
| `open_clip_config.json` / CLIP config | 图像嵌入候选 |
| `generation_config.json` | 生成式模型候选 |
| `vocoder` / `speaker` / `tts` 文件 | TTS 候选 |
| `whisper` / `ct2` / `model.bin` | Whisper/faster-whisper 候选 |

### 元数据特征

优先使用官方字段：

- `pipeline_tag`
- `tags`
- `library_name`
- `transformersInfo`
- `cardData`
- `safetensors`
- `config.architectures`

识别顺序：

1. 官方 `pipeline_tag` 直接映射。
2. `library_name` 判断运行框架。
3. 文件结构判断权重格式。
4. `config.architectures` 判断模型族。
5. 模型名、tags、README 作为辅助。
6. 多个候选时按运行成功率排序。

## 运行时选型

### 1. 对话模型

首选：

- `llama.cpp server`

适用：

- GGUF 格式 LLM。
- CPU 或单 GPU 单机部署。
- 需要部署简单、资源可控、OpenAI-compatible API。

备选：

- `vLLM`：适合 NVIDIA GPU、safetensors、服务部署和高并发。
- `transformers`：兼容兜底，性能和并发不作为首选。

策略：

- 如果存在 `.gguf`，优先 llama.cpp。
- 如果是主流 CausalLM safetensors 且 GPU 可用，推荐 vLLM。
- 如果模型需要 `trust_remote_code`，默认标记为需要用户确认。

### 2. 文字嵌入模型

首选：

- `FastEmbed`
- `ONNX Runtime`

备选：

- `sentence-transformers`
- `transformers AutoModel`

策略：

- 支持 batch 推理。
- 单进程可常驻，适合高并发。
- 输出向量维度写入 manifest。

### 3. 图像嵌入模型

首选：

- `transformers` + CLIP/SigLIP/DINOv2

备选：

- ONNX Runtime
- OpenCLIP

策略：

- 识别 `CLIPModel`、`SiglipModel`、`Dinov2Model`。
- 支持图片 batch。
- 统一输出 embedding。

### 4. 重排模型

首选：

- `FlagEmbedding`
- `sentence-transformers CrossEncoder`

备选：

- `transformers AutoModelForSequenceClassification`

策略：

- BGE reranker 系列优先 FlagEmbedding。
- CrossEncoder 模型使用 sentence-transformers。
- 支持 batch pairs。

### 5. 文生图模型

首选：

- `ComfyUI`

备选：

- `diffusers`

策略：

- SD/SDXL/Flux/ControlNet/LoRA 生态优先 ComfyUI。
- 简单 Diffusers pipeline 可走内置 Python worker。
- ComfyUI 通过工作流模板适配不同模型族。

### 6. 文生视频模型

首选：

- `ComfyUI`

备选：

- `diffusers` video pipeline

策略：

- Wan、CogVideo、AnimateDiff 等优先 ComfyUI 工作流。
- 视频生成强制进入任务队列，不做无控制并发。
- 默认限制分辨率、帧数和并发。

### 7. 文生声音模型

首选：

- `Kokoro`
- `ChatTTS`
- `CosyVoice`

备选：

- `transformers` audio pipeline

策略：

- TTS 模型族差异很大，必须按模型族适配。
- 优先支持部署简单、许可证清晰、推理稳定的模型。
- 声音克隆、说话人选择等功能作为扩展能力，不进入第一阶段核心。

### 8. 语音识别模型

首选：

- `faster-whisper`

备选：

- `whisper.cpp`

策略：

- 单机 CPU/GPU 都可用。
- 支持长音频分段。
- 支持任务队列和后台转写。

### 9. 图像识别文字 / 图生文

首选：

- `transformers` VLM pipeline

备选：

- `llama.cpp` multimodal
- 专用模型族适配器：BLIP、Florence、Qwen-VL、LLaVA

策略：

- OCR、图片描述、VQA 统一走 `image_to_text` 接口。
- 对大 VLM 做显存预估和并发限制。

## 运行时 Pack 设计

主程序不内置所有依赖。建议拆成能力包：

| Pack | 内容 | 典型安装后大小 |
|---|---|---:|
| Core Runtime | 网关、检测器、SQLite、轻量管理代码 | 100-500 MB |
| LLM CPU Pack | llama.cpp CPU binary | 50-300 MB |
| LLM CUDA Pack | llama.cpp CUDA binary / 可选 vLLM | 1-8 GB |
| Embedding Pack | FastEmbed、ONNX Runtime、sentence-transformers | 500 MB-3 GB |
| Vision Pack | transformers、torch、Pillow、OpenCLIP | 2-8 GB |
| Image Generation Pack | ComfyUI、diffusers、xformers/torch | 5-20 GB |
| Audio Pack | faster-whisper、TTS 运行时 | 1-6 GB |
| Video Pack | ComfyUI video nodes、ffmpeg、diffusers video deps | 5-20 GB |

安装策略：

- 首次安装只带 Core Runtime。
- 点击“运行”时如果缺运行时，弹出安装建议。
- 支持离线 Pack 导入。
- 支持管理员预装 Pack。
- Pack 安装路径放在 `$CHAYUAN_ROOT/runtimes`，便于迁移和清理。

## 存储设计

### SQLite 表

建议从 NDJSON 逐步迁移到 SQLite + FTS5 + JSON 字段：

```sql
model_registry(
  canonical_id text primary key,
  model_id text not null,
  source text,
  type text,
  original_type text,
  tags_json text,
  raw_json text,
  source_refs_json text,
  downloads integer,
  size_bytes integer,
  updated_at text,
  indexed_at integer
)

model_downloads(
  canonical_id text primary key,
  local_path text,
  downloaded integer,
  size_bytes integer,
  sha256 text,
  downloaded_at integer
)

runtime_manifests(
  canonical_id text primary key,
  manifest_json text,
  runnable integer,
  recommended_runtime text,
  updated_at integer
)

runtime_instances(
  instance_id text primary key,
  canonical_id text,
  runtime text,
  pid integer,
  endpoint text,
  status text,
  device text,
  started_at integer,
  last_active_at integer
)
```

### 文件结构

```text
$CHAYUAN_ROOT/
├── model_registry/
│   ├── models.db
│   ├── hf_mirror.full.ndjson
│   └── full_sync_status.json
├── models/
│   └── model_registry/
│       └── Qwen__Qwen2.5-7B-Instruct/
│           ├── config.json
│           ├── model.safetensors
│           └── runtime_manifest.json
├── runtimes/
│   ├── llama.cpp/
│   ├── comfyui/
│   ├── python-embedding/
│   └── python-vision/
└── runtime_logs/
```

## 单机部署路径

### 目标用户

- 个人电脑。
- 单 GPU 工作站。
- 无 Docker 或不熟悉服务部署的用户。

### 技术方案

- 主后端 FastAPI 作为控制面。
- Runtime Manager 使用本地子进程启动运行时。
- 每个模型一个实例或共享实例。
- 端口由系统自动分配并记录。
- 空闲超时自动卸载。

### 资源管理

- 检测 CPU、内存、GPU、显存。
- 每个模型启动前估算最低资源。
- 如果资源不足，阻止启动并展示原因。
- 文生图/视频默认串行队列。
- Embedding/Rerank 支持 batch 和小并发。

### 优点

- 部署简单。
- 调试容易。
- 不依赖额外服务。
- 符合当前项目落地速度。

### 风险

- Python 依赖冲突。
- Windows/macOS/Linux 运行时差异。
- GPU 驱动和 CUDA 版本复杂。

### 缓解

- 运行时 Pack 独立目录。
- 每类运行时独立虚拟环境或独立可执行。
- 禁止在主后端环境中安装所有推理依赖。

## 服务部署路径

### 目标用户

- 企业内网服务器。
- 多人共享模型服务。
- 后端和前端分离部署。

### 技术方案

拆分为：

```text
chayuan-api          控制面、鉴权、模型广场
runtime-worker       推理节点，负责加载和运行模型
runtime-store        SQLite/PostgreSQL
object/model-store   本地盘/NAS/对象存储
```

Runtime Worker 暴露：

```http
POST /worker/scan
POST /worker/start
POST /worker/stop
GET  /worker/status
POST /worker/infer/*
```

控制面选择可用 Worker：

- 本地 Worker。
- GPU Worker。
- CPU Worker。
- 专用图像/视频 Worker。

### 优点

- 主业务服务更稳定。
- 可以按部门共享模型。
- 运行时依赖和业务 API 解耦。

### 风险

- 网络传输大文件和图片/音频要考虑超时。
- Worker 状态一致性。
- 权限和审计复杂度提升。

### 缓解

- 控制面只传引用路径或对象存储 URL。
- Worker 心跳和租约机制。
- 所有运行/停止/测试写审计日志。

## 集群部署路径

### 目标用户

- 多 GPU 集群。
- 企业级多租户。
- 高并发在线推理。

### 推荐架构

```text
API Gateway
  |
Chayuan Control Plane
  |
Runtime Scheduler
  |
Queue / Event Bus
  |
Runtime Workers
  |-- LLM Worker Pool
  |-- Embedding Worker Pool
  |-- Diffusion Worker Pool
  |-- Audio Worker Pool
  |-- Video Worker Pool
```

### 调度策略

- 按模型类型路由到不同 Worker Pool。
- 按 GPU 显存、模型大小、当前负载做调度。
- 支持模型热加载和空闲卸载。
- 支持优先级队列和租户限流。
- 文生图/视频任务异步化。
- LLM 支持流式响应。
- Embedding/Rerank 合并 batch。

### 技术选型

控制面：

- FastAPI。
- PostgreSQL。
- Redis / NATS / RabbitMQ 作为队列。

运行层：

- LLM：vLLM、llama.cpp。
- Embedding：ONNX Runtime、FastEmbed。
- Diffusion/Video：ComfyUI Worker、Diffusers Worker。
- ASR：faster-whisper Worker。

容器和调度：

- Docker Compose 适合中小服务部署。
- Kubernetes + NVIDIA Device Plugin 适合 GPU 集群。
- Ray Serve 可作为高级推理调度方案，但不是第一阶段必选。

### 集群阶段能力

- 模型副本数。
- 自动扩缩容。
- 显存水位调度。
- 节点标签：`llm`、`diffusion`、`audio`、`embedding`。
- 队列指标和任务追踪。
- 多租户配额。

## 并发与性能设计

### LLM

- llama.cpp 单机适合低到中并发。
- vLLM 适合 GPU 服务部署和高并发。
- 支持 streaming。
- 每个实例设置最大上下文、最大并发、空闲卸载。

### Embedding

- 重点做 batch。
- 请求在 5-20ms 窗口内合批。
- 可常驻进程。
- 适合 CPU 或小 GPU。

### Rerank

- batch pairs。
- query 相同可复用 tokenization。
- 可限制最大文档数。

### 图像/视频生成

- 任务队列。
- 默认低并发。
- 支持进度事件。
- 生成结果落盘并返回 URL。

### ASR

- 长音频分片。
- 后台任务。
- 支持进度。

## 安全设计

1. 默认不启用 `trust_remote_code`。
2. 运行时启动命令必须来自白名单。
3. 模型路径必须限制在 `$CHAYUAN_ROOT/models`。
4. 下载源必须经过 model_registry 的 source_refs。
5. 运行时进程使用最小权限。
6. 企业部署记录审计日志。
7. 支持禁用高风险模型运行。
8. 支持管理员配置可用 Pack 和允许的运行时。

## 前端体验设计

模型卡片状态：

```text
未下载
下载中
已下载，扫描中
已下载，可运行
缺少运行时
资源不足
运行中
运行失败
暂无适配器
```

按钮：

- `下载`
- `运行`
- `测试`
- `停止`
- `安装运行时`
- `查看原因`
- `打开配置`

详情页展示：

- 原始类型和中文类型。
- 标签分类。
- 下载源。
- 本地路径。
- 推荐运行时。
- 缺失依赖。
- CPU/GPU/显存需求。
- 并发配置。
- 启动日志。

## 实施路线

### 阶段 0：设计和契约

- 新增本文档。
- 定义 `runtime_manifest.json`。
- 定义 `/local_runtime/*` API。
- 前端模型卡片预留运行状态。

### 阶段 1：下载后扫描

- 实现 `detector.py`。
- 下载完成后自动扫描。
- 生成可运行性判定。
- 前端展示“可运行/缺运行时/暂无适配器”。

优先支持识别：

- GGUF LLM。
- sentence-transformers。
- BGE reranker。
- Whisper。
- Diffusers。
- CLIP/SigLIP。

### 阶段 2：首批可运行

实现：

- `llama.cpp` 对话。
- `FastEmbed` 文本嵌入。
- `FlagEmbedding` / CrossEncoder 重排。
- `faster-whisper` 语音识别。

原因：

- 单机成功率高。
- 部署复杂度相对低。
- 能覆盖知识库、对话和语音基础场景。

### 阶段 3：视觉和图像生成

实现：

- CLIP/SigLIP 图像嵌入。
- BLIP/Florence/Qwen-VL 图生文。
- ComfyUI 文生图。

### 阶段 4：音频和视频生成

实现：

- Kokoro/ChatTTS/CosyVoice。
- ComfyUI 文生视频工作流。
- 视频任务队列和进度。

### 阶段 5：服务部署

- Runtime Worker 独立服务。
- Worker 心跳。
- 远程启动和状态同步。
- Docker Compose 部署模板。

### 阶段 6：集群部署

- 调度器。
- Worker Pool。
- 队列。
- GPU 资源感知。
- PostgreSQL/Redis。
- Kubernetes 模板。

## 最优技术路线结论

本项目的最优路线是：

```text
模型广场 + hf-mirror 元数据同步
  -> huggingface_hub 下载
  -> 本地 runtime detector 扫描
  -> runtime_manifest 可运行性判定
  -> Local Runtime Gateway 控制面
  -> 多运行时适配器
  -> 单机进程 / 服务 Worker / 集群 Worker 逐级演进
```

首批不要做全家桶，不要把所有依赖塞进主安装包。应先把“下载后自动识别、可运行性明确、一键启动主流模型”做稳。随后通过运行时 Pack 覆盖更多类型，再通过 Worker 化进入服务和集群部署。

这个路线兼顾：

- 单机可运行。
- 安装包可控。
- 性能可扩展。
- 依赖隔离。
- 前端体验统一。
- 企业服务部署和集群部署有自然演进路径。
