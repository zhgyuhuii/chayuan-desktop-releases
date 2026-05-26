/**
 * 本地推理服务安装指南 — 离线友好的图文教程数据。
 *
 * 设计原则:
 *   - 所有内容是**纯文字 + 命令**,不依赖外网图片,断网也能看
 *   - 命令、yaml 全部"复制即用"(带常用参数 / 镜像 tag / 端口),不要让用户自己再查文档
 *   - 推荐模型 id 用各家服务的 namespace(Ollama 用 ``qwen2.5:7b``,Infinity 用
 *     ``BAAI/bge-m3`` 这种 HF id),保证用户拷过去能跑
 *   - 「在察元里配置」一栏给具体路径(模型广场 → 厂商 → ⚙️ → 测试连通 → 设置默认),
 *     连接外部安装与产品内配置
 *
 * 覆盖范围(2026-05):Ollama / Infinity / Xinference / LM Studio / vLLM / llama.cpp。
 * 其他本地厂商(Jan / GPT4All / KoboldCpp / TGI / OpenLLM / LocalAI / TextGenWebUI 等)
 * 仍走 ProviderCard 老路径 openExternal 跳官网,后续按需补完。
 */

export type ModelKind = 'chat' | 'embed' | 'rerank' | 'image' | 'vision' | 'speech';

export const MODEL_KIND_LABEL: Record<ModelKind, string> = {
  chat: '对话模型',
  embed: '嵌入模型',
  rerank: '重排模型',
  image: '图像生成',
  vision: '图文向量化',
  speech: '语音模型',
};

export type PlatformKey = 'windows' | 'macos' | 'linux' | 'docker';

export const PLATFORM_LABEL: Record<PlatformKey, string> = {
  windows: 'Windows',
  macos: 'macOS',
  linux: 'Linux',
  docker: 'Docker',
};

/** 一个步骤;有 code 就渲染代码块,无 code 就纯文字说明 */
export interface GuideStep {
  /** 步骤标题(例:"1. 安装 Ollama") */
  title: string;
  /** 说明正文(可空) */
  description?: string;
  /** 代码块语言 — bash / powershell / yaml / python / dockerfile */
  language?: string;
  /** 代码块内容 — 多行用 ``\n`` 拼 */
  code?: string;
}

export interface PlatformGuide {
  /** 这个平台是否原生支持(false 时显示"不直接支持"提示)*/
  supported?: boolean;
  /** 不支持时的备注(例:"Windows 不直接支持,推荐 WSL2 + Docker") */
  note?: string;
  steps: GuideStep[];
}

export interface RecommendedModel {
  kind: ModelKind;
  /** 给用户的展示名(可包含 quant tag 等) */
  name: string;
  /** 拉取/部署命令(可选) */
  command?: string;
  /** 用途简述 */
  description?: string;
}

export interface InstallGuide {
  pid: string;
  /** 标题前的 emoji(简单美化) */
  emoji: string;
  title: string;
  /** 一句话简介 */
  intro: string;
  /** 该服务支持的模型类型 */
  capabilities: ModelKind[];
  /** 默认 OpenAI 兼容 API base(察元里填这个) */
  defaultBaseUrl: string;
  /** 平台分支 */
  platforms: Partial<Record<PlatformKey, PlatformGuide>>;
  /** 推荐模型清单 */
  recommendedModels: RecommendedModel[];
  /**
   * "在察元里如何接入" 的步骤(纯文字,2-4 步)。
   * 显式描述模型广场 → 厂商 → 测试连通 → 设置默认这条路径。
   */
  chayuanConfigSteps: string[];
  /** 完整官方文档链接,放在 dialog 底部"查看完整文档"按钮 */
  officialDocs: string;
}

// ─────────────────────────────────────────────────────────────────
// Ollama
// ─────────────────────────────────────────────────────────────────
const ollama: InstallGuide = {
  pid: 'ollama',
  emoji: '🦙',
  title: 'Ollama',
  intro: '跨平台本地大模型一键运行。自带模型管理 + OpenAI 兼容 API,装完即用,适合零基础用户。',
  capabilities: ['chat', 'embed'],
  defaultBaseUrl: 'http://127.0.0.1:11434/v1',
  platforms: {
    windows: {
      supported: true,
      steps: [
        {
          title: '1. 安装 Ollama',
          description: '可以在官网下载安装包双击安装,也可以用 winget 一键装。安装后 Ollama 会作为服务在后台运行,无需手动启动。',
          language: 'powershell',
          code: 'winget install --id Ollama.Ollama',
        },
        {
          title: '2. 拉取模型',
          description: '示例:拉一个对话模型(qwen2.5:7b,约 4.7 GB)和一个嵌入模型(bge-m3,约 1.2 GB)。模型保存在 %USERPROFILE%\\.ollama\\models。',
          language: 'powershell',
          code: 'ollama pull qwen2.5:7b\nollama pull bge-m3',
        },
        {
          title: '3. 验证服务',
          description: '默认监听 127.0.0.1:11434。浏览器或察元访问 OpenAI 兼容 endpoint:',
          language: 'powershell',
          code: 'curl http://127.0.0.1:11434/v1/models',
        },
      ],
    },
    macos: {
      supported: true,
      steps: [
        {
          title: '1. 安装 Ollama',
          description: '推荐 brew 一键装(也支持下载 Ollama.dmg 拖到 Applications)。',
          language: 'bash',
          code: 'brew install ollama',
        },
        {
          title: '2. 启动服务',
          description: 'brew 装的需要手动启 service;dmg 拖装的会自动启。',
          language: 'bash',
          code: 'brew services start ollama',
        },
        {
          title: '3. 拉取模型',
          language: 'bash',
          code: 'ollama pull qwen2.5:7b\nollama pull bge-m3',
        },
      ],
    },
    linux: {
      supported: true,
      steps: [
        {
          title: '1. 一行安装',
          description: '官方脚本支持 Ubuntu / Debian / RHEL / 国产 UOS / 麒麟等。会自动安装 systemd 服务并启动。',
          language: 'bash',
          code: 'curl -fsSL https://ollama.com/install.sh | sh',
        },
        {
          title: '2. 拉取模型',
          language: 'bash',
          code: 'ollama pull qwen2.5:7b\nollama pull bge-m3',
        },
        {
          title: '3. 服务管理(可选)',
          description: '安装脚本默认启 systemd 服务 ollama.service。',
          language: 'bash',
          code: 'sudo systemctl status ollama\nsudo systemctl restart ollama',
        },
      ],
    },
    docker: {
      supported: true,
      steps: [
        {
          title: '1. 启动容器',
          description: '把下面 docker-compose.yml 保存到任意目录,执行 ``docker compose up -d``。GPU 推理需要主机装 NVIDIA Container Toolkit,把 deploy 段的 GPU 配置取消注释即可。',
          language: 'yaml',
          code: `services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      - ./ollama-data:/root/.ollama
    restart: unless-stopped
    # 如需 GPU 推理(主机已装 nvidia-container-toolkit):
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]`,
        },
        {
          title: '2. 拉模型',
          description: '通过容器内 ollama CLI 拉取(模型存到上面挂载的 ./ollama-data 卷)。',
          language: 'bash',
          code: 'docker exec -it ollama ollama pull qwen2.5:7b\ndocker exec -it ollama ollama pull bge-m3',
        },
      ],
    },
  },
  recommendedModels: [
    { kind: 'chat',  name: 'qwen2.5:7b',          command: 'ollama pull qwen2.5:7b',          description: '阿里通义千问 2.5 7B,中文对话首选,4.7 GB' },
    { kind: 'chat',  name: 'deepseek-r1:7b',      command: 'ollama pull deepseek-r1:7b',      description: 'DeepSeek-R1 蒸馏版,推理 / 代码强,4.7 GB' },
    { kind: 'chat',  name: 'llama3.3',            command: 'ollama pull llama3.3',            description: 'Meta Llama 3.3,英文场景表现优秀,~40 GB' },
    { kind: 'embed', name: 'bge-m3',              command: 'ollama pull bge-m3',              description: '北智 BGE-M3,中英多语,1024 维,1.2 GB' },
    { kind: 'embed', name: 'nomic-embed-text',    command: 'ollama pull nomic-embed-text',    description: '英文长文本嵌入,768 维,275 MB' },
  ],
  chayuanConfigSteps: [
    '打开「模型广场」→ 找到 Ollama 卡片 → 点右下角 ⚙️ 设置',
    'API Base URL 默认填 ``http://127.0.0.1:11434/v1``(本地默认端口),Docker 部署也是这个',
    '点击「测试连通 / 刷新模型」,会自动列出已 pull 的模型',
    '到「设置 → 默认模型」把对话模型选 ``qwen2.5:7b``、嵌入模型选 ``bge-m3``,新对话和知识库就会用本地模型',
  ],
  officialDocs: 'https://github.com/ollama/ollama/blob/main/docs/api.md',
};

// ─────────────────────────────────────────────────────────────────
// Infinity
// ─────────────────────────────────────────────────────────────────
const infinity: InstallGuide = {
  pid: 'infinity',
  emoji: '⚡',
  title: 'Infinity',
  intro: '高性能 embedding / reranker / 图文向量化推理服务,OpenAI 兼容,支持 ONNX / CUDA / CPU。一台机器服务多个模型,知识库场景首选。',
  capabilities: ['embed', 'rerank', 'vision'],
  defaultBaseUrl: 'http://127.0.0.1:7997/v1',
  platforms: {
    docker: {
      supported: true,
      steps: [
        {
          title: '1. docker-compose 启动(推荐)',
          description: '把下面 yaml 保存为 docker-compose.yml 后执行 ``docker compose up -d``。命令里 ``--model-id`` 启动时挂载的模型,可以多个。GPU 部署去掉注释。',
          language: 'yaml',
          code: `services:
  infinity:
    image: michaelf34/infinity:latest
    container_name: infinity
    ports:
      - "7997:7997"
    # 模型缓存目录,避免重启重新下载
    volumes:
      - ./infinity-cache:/app/.cache
    # 启动时同时挂多个模型(嵌入 + 重排)
    command: >
      v2
      --model-id BAAI/bge-m3
      --model-id BAAI/bge-reranker-v2-m3
      --port 7997
    restart: unless-stopped
    # 如需 GPU 推理:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]`,
        },
        {
          title: '2. 验证服务',
          description: '启动需要先下载模型(国内用户可设置 HF_ENDPOINT=https://hf-mirror.com 加速)。访问 /docs 看 swagger,或直接 GET /v1/models。',
          language: 'bash',
          code: 'curl http://127.0.0.1:7997/v1/models',
        },
      ],
    },
    linux: {
      supported: true,
      steps: [
        {
          title: '1. pip 安装',
          description: '需要 Python 3.10+。``[all]`` 包含全部 backend(ONNX / Torch / TensorRT)。',
          language: 'bash',
          code: 'pip install "infinity-emb[all]"',
        },
        {
          title: '2. 启动服务',
          description: '示例:同时托管嵌入和重排模型(模型从 HuggingFace 下载,首次启动较慢)。',
          language: 'bash',
          code: 'infinity_emb v2 \\\n  --model-id BAAI/bge-m3 \\\n  --model-id BAAI/bge-reranker-v2-m3 \\\n  --port 7997',
        },
      ],
    },
    macos: {
      supported: true,
      note: 'macOS 直接 pip 安装可用 CPU 推理;GPU(MPS)支持有限,推荐用小模型。',
      steps: [
        {
          title: '1. pip 安装',
          language: 'bash',
          code: 'pip install "infinity-emb[all]"',
        },
        {
          title: '2. 启动(CPU 模式)',
          language: 'bash',
          code: 'infinity_emb v2 --model-id BAAI/bge-m3 --port 7997 --device cpu',
        },
      ],
    },
    windows: {
      supported: false,
      note: 'Windows 不直接支持 pip 一键启动(部分 backend 缺 wheel)。推荐用 WSL2 + 上面 Linux 的命令,或直接用 Docker 部署(见 Docker tab)。',
      steps: [],
    },
  },
  recommendedModels: [
    { kind: 'embed',  name: 'BAAI/bge-m3',                   command: '--model-id BAAI/bge-m3',                  description: '中英多语嵌入,1024 维,首选' },
    { kind: 'embed',  name: 'jinaai/jina-embeddings-v3',     command: '--model-id jinaai/jina-embeddings-v3',    description: '多语言 32k 长上下文,1024 维' },
    { kind: 'rerank', name: 'BAAI/bge-reranker-v2-m3',       command: '--model-id BAAI/bge-reranker-v2-m3',      description: '中英多语重排,知识库召回质量提升明显' },
    { kind: 'rerank', name: 'jinaai/jina-reranker-v2-base-multilingual', command: '--model-id jinaai/jina-reranker-v2-base-multilingual', description: 'Jina 多语重排,长上下文友好' },
    { kind: 'vision', name: 'jinaai/jina-clip-v1',           command: '--model-id jinaai/jina-clip-v1',          description: '图文 CLIP,文本与图像同向量空间' },
  ],
  chayuanConfigSteps: [
    '打开「模型广场」→ 找到 Infinity 卡片 → 点右下角 ⚙️ 设置',
    'API Base URL 填 ``http://127.0.0.1:7997/v1``,API Key 留空',
    '点击「测试连通 / 刷新模型」,会列出 ``--model-id`` 启动时挂载的所有模型',
    '到「设置 → 默认模型」把嵌入指到 ``BAAI/bge-m3``、重排指到 ``BAAI/bge-reranker-v2-m3``',
  ],
  officialDocs: 'https://michaelfeil.github.io/infinity/latest/deploy/',
};

// ─────────────────────────────────────────────────────────────────
// Xinference
// ─────────────────────────────────────────────────────────────────
const xinference: InstallGuide = {
  pid: 'xinference',
  emoji: '🌀',
  title: 'Xinference',
  intro: 'Xorbits Inference,一站式本地推理。覆盖对话 / 嵌入 / 重排 / 图像 / 语音多模态,自带 Web UI 启停模型。',
  capabilities: ['chat', 'embed', 'rerank', 'image', 'speech'],
  defaultBaseUrl: 'http://127.0.0.1:9997/v1',
  platforms: {
    linux: {
      supported: true,
      steps: [
        {
          title: '1. pip 安装',
          description: '``[all]`` 包含全部依赖(transformers / vllm / ggml backend);只装基础包用 ``pip install xinference``。',
          language: 'bash',
          code: 'pip install "xinference[all]"',
        },
        {
          title: '2. 启动服务',
          description: '本地模式(单机)。``-H 0.0.0.0`` 让其它机器能连。',
          language: 'bash',
          code: 'xinference-local --host 0.0.0.0 --port 9997',
        },
        {
          title: '3. 部署模型',
          description: '浏览器打开 http://127.0.0.1:9997 → 点击 Launch Model → 选模型(qwen / bge / stable diffusion 等)→ 选规格 → Launch。Xinference 会自动从 HuggingFace 或 ModelScope 下载并启服务。',
        },
      ],
    },
    macos: {
      supported: true,
      steps: [
        {
          title: '1. pip 安装',
          description: 'macOS 推荐用 ``[ml]``(轻量,不带 vllm),M 系列芯片可启用 MPS。',
          language: 'bash',
          code: 'pip install "xinference[ml]"',
        },
        {
          title: '2. 启动 + 部署',
          language: 'bash',
          code: 'xinference-local --host 0.0.0.0 --port 9997',
        },
      ],
    },
    windows: {
      supported: true,
      note: '部分 backend(如 vllm)在 Windows 不可用,但 transformers / ggml backend OK,可以跑大部分对话和嵌入模型。',
      steps: [
        {
          title: '1. pip 安装',
          language: 'powershell',
          code: 'pip install "xinference[all]"',
        },
        {
          title: '2. 启动',
          language: 'powershell',
          code: 'xinference-local --host 0.0.0.0 --port 9997',
        },
      ],
    },
    docker: {
      supported: true,
      steps: [
        {
          title: 'docker-compose',
          language: 'yaml',
          code: `services:
  xinference:
    image: xprobe/xinference:latest
    container_name: xinference
    ports:
      - "9997:9997"
    volumes:
      - ./xinference-cache:/root/.xinference
      - ./hf-cache:/root/.cache/huggingface
    command: xinference-local -H 0.0.0.0 --port 9997
    restart: unless-stopped
    # GPU 部署:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]`,
        },
      ],
    },
  },
  recommendedModels: [
    { kind: 'chat',   name: 'qwen2.5-instruct',         description: '通义千问 2.5,Web UI 选 7B/14B/32B/72B' },
    { kind: 'chat',   name: 'deepseek-r1-distill-qwen', description: 'DeepSeek R1 蒸馏 Qwen,推理强' },
    { kind: 'embed',  name: 'bge-m3',                    description: '中英多语嵌入' },
    { kind: 'rerank', name: 'bge-reranker-v2-m3',        description: '中英多语重排' },
    { kind: 'image',  name: 'stable-diffusion-3.5-medium', description: '图像生成' },
  ],
  chayuanConfigSteps: [
    '在 Xinference Web UI(http://127.0.0.1:9997)用 Launch Model 把要用的模型起起来',
    '打开察元「模型广场」→ Xinference 卡片 → 点右下角 ⚙️ 设置',
    'API Base URL 填 ``http://127.0.0.1:9997/v1``,API Key 留空',
    '「测试连通 / 刷新模型」拉模型列表(只显示 Xinference 已 Launch 的)',
    '「设置 → 默认模型」按需选对话 / 嵌入 / 重排',
  ],
  officialDocs: 'https://inference.readthedocs.io/zh-cn/latest/getting_started/installation.html',
};

// ─────────────────────────────────────────────────────────────────
// LM Studio
// ─────────────────────────────────────────────────────────────────
const lmStudio: InstallGuide = {
  pid: 'lm-studio',
  emoji: '🖥️',
  title: 'LM Studio',
  intro: '跨平台桌面 GUI,可视化下载 GGUF 模型 + 一键启 OpenAI 兼容 server。零命令行,适合纯 GUI 用户。',
  capabilities: ['chat', 'embed'],
  defaultBaseUrl: 'http://127.0.0.1:1234/v1',
  platforms: {
    windows: {
      supported: true,
      steps: [
        {
          title: '1. 下载安装',
          description: '到 https://lmstudio.ai/download 下载 LM-Studio-x.x.x.exe 双击安装(需要 Windows 10/11 64-bit)。',
        },
        {
          title: '2. 在 app 内下载模型',
          description: '打开 LM Studio → 左侧 Discover 标签 → 搜索 "Qwen2.5 7B Instruct GGUF",选 Q4_K_M 量化 → Download。',
        },
        {
          title: '3. 启动 OpenAI 兼容 server',
          description: '左侧 Local Server 标签 → 顶部 Model 选刚下的模型 → Start Server,默认监听 1234 端口。打开 "Cross-Origin-Resource-Sharing" 让察元能访问。',
        },
      ],
    },
    macos: {
      supported: true,
      steps: [
        {
          title: '1. 下载安装',
          description: 'https://lmstudio.ai/download 下载 LM-Studio-x.x.x.dmg,拖到 Applications。Apple Silicon 自动启用 Metal 加速。',
        },
        {
          title: '2. 在 app 内下载模型',
          description: '同 Windows:Discover → 搜索 → Q4_K_M → Download。',
        },
        {
          title: '3. 启动 server',
          description: 'Local Server → Start Server。',
        },
      ],
    },
    linux: {
      supported: true,
      steps: [
        {
          title: '1. 下载 AppImage',
          description: 'https://lmstudio.ai/download 下载 LM_Studio-x.x.x.AppImage,赋予执行权限并运行。',
          language: 'bash',
          code: 'chmod +x LM_Studio-*.AppImage\n./LM_Studio-*.AppImage',
        },
        {
          title: '2. 模型 + server',
          description: '同其他平台:Discover → 下载 → Local Server → Start。',
        },
      ],
    },
  },
  recommendedModels: [
    { kind: 'chat',  name: 'Qwen2.5-7B-Instruct-GGUF (Q4_K_M)',          description: '中文对话首选,4.4 GB' },
    { kind: 'chat',  name: 'DeepSeek-R1-Distill-Qwen-7B-GGUF (Q4_K_M)',  description: '推理 / 代码,~4.7 GB' },
    { kind: 'chat',  name: 'Llama-3.3-70B-Instruct-GGUF (Q4_K_M)',       description: '英文场景,~40 GB,需要大显存' },
    { kind: 'embed', name: 'nomic-embed-text-v1.5-GGUF',                  description: '英文嵌入,275 MB' },
  ],
  chayuanConfigSteps: [
    '在 LM Studio → Local Server 标签 → 选好模型 → Start Server',
    '察元「模型广场」→ LM Studio 卡片 → 右下角 ⚙️ 设置',
    'API Base URL 填 ``http://127.0.0.1:1234/v1``,API Key 留空',
    '「测试连通」OK 后,LM Studio 当前加载的模型就是察元能用的;切模型直接在 LM Studio 切,察元这边不用改配置',
  ],
  officialDocs: 'https://lmstudio.ai/docs',
};

// ─────────────────────────────────────────────────────────────────
// vLLM
// ─────────────────────────────────────────────────────────────────
const vllm: InstallGuide = {
  pid: 'vllm',
  emoji: '🚀',
  title: 'vLLM',
  intro: 'UC Berkeley 高性能 LLM 推理服务,GPU 必备(NVIDIA / AMD ROCm)。OpenAI 兼容,生产部署主力。',
  capabilities: ['chat', 'embed'],
  defaultBaseUrl: 'http://127.0.0.1:8000/v1',
  platforms: {
    linux: {
      supported: true,
      steps: [
        {
          title: '1. pip 安装',
          description: '需要 NVIDIA GPU + CUDA 12.1+。建议在虚拟环境里装,vLLM 依赖较重。',
          language: 'bash',
          code: 'pip install vllm',
        },
        {
          title: '2. 启动 OpenAI server',
          description: '示例:Qwen2.5 7B Instruct,bfloat16 精度,8000 端口。``--max-model-len`` 控制上下文长度,根据显存调小。',
          language: 'bash',
          code: 'python -m vllm.entrypoints.openai.api_server \\\n  --model Qwen/Qwen2.5-7B-Instruct \\\n  --port 8000 \\\n  --dtype bfloat16 \\\n  --max-model-len 16384',
        },
        {
          title: '3. 验证',
          language: 'bash',
          code: 'curl http://127.0.0.1:8000/v1/models',
        },
      ],
    },
    docker: {
      supported: true,
      steps: [
        {
          title: 'docker-compose(GPU)',
          description: '主机需要装 nvidia-container-toolkit。HF cache 挂载避免每次重启都重新下模型。',
          language: 'yaml',
          code: `services:
  vllm:
    image: vllm/vllm-openai:latest
    container_name: vllm
    ports:
      - "8000:8000"
    volumes:
      - ./hf-cache:/root/.cache/huggingface
    ipc: host
    command: >
      --model Qwen/Qwen2.5-7B-Instruct
      --dtype bfloat16
      --max-model-len 16384
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped`,
        },
      ],
    },
    windows: {
      supported: false,
      note: 'vLLM 不原生支持 Windows。推荐用 WSL2 + CUDA,在 WSL Ubuntu 里按 Linux tab 操作。',
      steps: [],
    },
    macos: {
      supported: false,
      note: 'vLLM 不支持 macOS(需要 NVIDIA / AMD ROCm GPU)。Mac 用户推荐 Ollama 或 LM Studio。',
      steps: [],
    },
  },
  recommendedModels: [
    { kind: 'chat',  name: 'Qwen/Qwen2.5-7B-Instruct',           command: '--model Qwen/Qwen2.5-7B-Instruct',            description: '通义 7B,16 GB+ 显存' },
    { kind: 'chat',  name: 'Qwen/Qwen2.5-32B-Instruct',          command: '--model Qwen/Qwen2.5-32B-Instruct',           description: '通义 32B,需要 ≥48 GB 显存(可双卡)' },
    { kind: 'chat',  name: 'deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', command: '--model deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', description: 'DeepSeek-R1 蒸馏,推理强' },
    { kind: 'embed', name: 'intfloat/e5-mistral-7b-instruct',    command: '--model intfloat/e5-mistral-7b-instruct',     description: '英文长文本嵌入(vllm ≥ 0.6)' },
  ],
  chayuanConfigSteps: [
    '在 GPU 机器上启 vLLM(参考左侧 Linux / Docker tab)',
    '察元「模型广场」→ vLLM 卡片 → ⚙️ 设置',
    'API Base URL 填 ``http://127.0.0.1:8000/v1``(远程机器把 127.0.0.1 改成对方 IP),API Key 留空',
    '「测试连通 / 刷新模型」会看到启动时 ``--model`` 指定的那一个模型',
  ],
  officialDocs: 'https://docs.vllm.ai/en/latest/getting_started/installation.html',
};

// ─────────────────────────────────────────────────────────────────
// llama.cpp
// ─────────────────────────────────────────────────────────────────
const llamaCpp: InstallGuide = {
  pid: 'llama-cpp',
  emoji: '🐉',
  title: 'llama.cpp',
  intro: 'GGUF / GGML 推理引擎,纯 C++,CPU 友好,跨平台,内存占用低。适合无 GPU 的轻量场景。',
  capabilities: ['chat', 'embed'],
  defaultBaseUrl: 'http://127.0.0.1:8080/v1',
  platforms: {
    macos: {
      supported: true,
      steps: [
        {
          title: '1. brew 安装',
          description: 'Apple Silicon 自动启用 Metal 加速。',
          language: 'bash',
          code: 'brew install llama.cpp',
        },
        {
          title: '2. 下载 GGUF 模型',
          description: '从 HuggingFace 下载 *.gguf 文件,例如 Qwen2.5-7B-Instruct-Q4_K_M.gguf。文件 ~4.4 GB。',
        },
        {
          title: '3. 启动 OpenAI 兼容 server',
          description: '``-c`` 上下文长度,``--host`` / ``--port`` 监听地址。',
          language: 'bash',
          code: 'llama-server \\\n  -m ./qwen2.5-7b-instruct-q4_k_m.gguf \\\n  -c 8192 \\\n  --host 127.0.0.1 \\\n  --port 8080',
        },
      ],
    },
    linux: {
      supported: true,
      steps: [
        {
          title: '1. 编译',
          description: '需要 cmake + g++。GPU 用 ``-DGGML_CUDA=ON`` 启 CUDA;Apple Silicon 自动 Metal;CPU 不需要参数。',
          language: 'bash',
          code: `git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j`,
        },
        {
          title: '2. 启动 server',
          language: 'bash',
          code: './build/bin/llama-server \\\n  -m ./model.gguf \\\n  -c 8192 \\\n  --host 0.0.0.0 \\\n  --port 8080',
        },
      ],
    },
    windows: {
      supported: true,
      steps: [
        {
          title: '1. 下载预编译版本',
          description: 'https://github.com/ggerganov/llama.cpp/releases 下载 ``llama-bxxxx-bin-win-cuda-x64.zip``(GPU 版)或 ``llama-bxxxx-bin-win-avx2-x64.zip``(CPU 版)。解压到任意目录。',
        },
        {
          title: '2. PowerShell 启动',
          description: '路径中如果有空格请加引号。',
          language: 'powershell',
          code: '.\\llama-server.exe `\n  -m .\\model.gguf `\n  -c 8192 `\n  --host 127.0.0.1 `\n  --port 8080',
        },
      ],
    },
    docker: {
      supported: true,
      steps: [
        {
          title: 'docker-compose',
          description: 'GGUF 模型放 ./models 下,容器从 /models 路径加载。',
          language: 'yaml',
          code: `services:
  llama-cpp:
    image: ghcr.io/ggerganov/llama.cpp:server
    container_name: llama-cpp
    ports:
      - "8080:8080"
    volumes:
      - ./models:/models
    command: -m /models/model.gguf -c 8192 --host 0.0.0.0 --port 8080
    restart: unless-stopped`,
        },
      ],
    },
  },
  recommendedModels: [
    { kind: 'chat',  name: 'Qwen2.5-7B-Instruct-GGUF (Q4_K_M)',                            description: '中文对话,4.4 GB,CPU 也能跑' },
    { kind: 'chat',  name: 'Llama-3.3-70B-Instruct-GGUF (Q4_K_M)',                          description: '英文场景,~40 GB,需要 ≥48 GB RAM' },
    { kind: 'chat',  name: 'DeepSeek-R1-Distill-Qwen-7B-GGUF (Q4_K_M)',                     description: '推理强,4.7 GB' },
    { kind: 'embed', name: 'bge-m3-Q5_K_M-GGUF',                                            description: '中英嵌入,GGUF 量化版' },
  ],
  chayuanConfigSteps: [
    '本地启动 llama-server -m model.gguf 之后',
    '察元「模型广场」→ llama.cpp 卡片 → ⚙️ 设置',
    'API Base URL 填 ``http://127.0.0.1:8080/v1``,API Key 留空',
    '「测试连通」拉到的就是启动时 ``-m`` 指定的那个模型',
  ],
  officialDocs: 'https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md',
};

// ─────────────────────────────────────────────────────────────────

export const INSTALL_GUIDES: Record<string, InstallGuide> = {
  ollama,
  infinity,
  xinference,
  'lm-studio': lmStudio,
  vllm,
  'llama-cpp': llamaCpp,
};

export function getInstallGuide(pid: string): InstallGuide | null {
  return INSTALL_GUIDES[pid] ?? null;
}
