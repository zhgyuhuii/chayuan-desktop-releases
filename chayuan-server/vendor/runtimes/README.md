# `vendor/runtimes/` — 推理运行时离线包

每个子目录代表一个**LLM / Embedding / ASR / TTS / OCR / 图像生成的本地运行时**。
chayuan-server 默认通过 OpenAI-compatible HTTP 接口调用它们；本目录只负责
"把可执行文件放在哪、跑哪个端口、要哪些权重"。

## 已支持的运行时

| 子目录 | 类型 | 上游 | 默认偏好端口 | 备注 |
|---|---|---|---|---|
| `ollama/`      | LLM   | https://ollama.com/download                          | 31434 | 把 `ollama` 二进制放到 `bin/`；模型在 `models/` |
| `llama-cpp/`   | LLM   | https://github.com/ggerganov/llama.cpp/releases       | 38081 | `bin/llama-server` |
| `vllm/`        | LLM   | `pip install vllm` 到独立 venv                         | 38000 | 通常需要 GPU |
| `whisper-cpp/` | ASR   | https://github.com/ggerganov/whisper.cpp/releases     | 38010 | `bin/whisper-server` + `models/ggml-*.bin` |
| `funasr/`      | ASR   | https://github.com/alibaba-damo-academy/FunASR        | 38020 | 仅 CPU；中文效果好 |
| `piper/`       | TTS   | https://github.com/rhasspy/piper/releases             | 38030 | `bin/piper` + `models/<lang>.onnx` |
| `cosyvoice/`   | TTS   | https://github.com/FunAudioLLM/CosyVoice              | 38040 | 中文 TTS，需要 GPU |
| `rapidocr/`    | OCR   | https://github.com/RapidAI/RapidOCR                   | 38050 | onnxruntime；纯 CPU |
| `paddleocr/`   | OCR   | https://github.com/PaddlePaddle/PaddleOCR             | 38060 | 装 paddlepaddle |
| `comfyui/`     | 图像  | https://github.com/comfyanonymous/ComfyUI             | 38188 | 完整克隆；模型放 `models/` |
| `infinity/`    | Embed | https://github.com/michaelfeil/infinity                | 37997 | OpenAI 兼容 embedding 服务 |

## 子目录结构示例

```
vendor/runtimes/ollama/
├── README.md             # 写明上游版本、license、CN/海外镜像
├── bin/
│   └── ollama            # 可执行（POSIX）；windows 用 ollama.exe
├── models/               # ollama pull 下来的模型缓存（可省略，让 ollama 自己管）
└── start.sh              # 可选：自定义启动脚本（chayuan 优先 bin/，找不到才走脚本）
```

## 与 `Settings.MODEL_PLATFORMS` 的关系

* 这里"放进来"的运行时**不会**自动注册到 `model_settings.yaml` 的
  `MODEL_PLATFORMS`；用户仍需在配置面板里加一条
  `platform_type=ollama, api_base_url=http://127.0.0.1:31434/v1`。
* 之所以分开：让"运行时是否可用"与"业务是否要用它"解耦。某些场景安装包
  里同时带了 ollama / llama-cpp 两个运行时，最终只让用户启用其中一个。

## chayuan 自带模型扫描器

* `<CHAYUAN_ROOT>/models/` 与 `vendor/runtimes/*/models/` 都会被
  `chayuan/server/model_registry/local_index.py` 周期扫描；
* GGUF / ONNX 单文件、HuggingFace 仓库目录、Diffusers 仓库都能识别（5 级回退）；
* 任何新增 / 修改 / 删除会通过 SSE `/runtime/models/events` 推给前端模型广场。

详细识别规则见 [`identifier.py`](../../libs/chayuan-server/chayuan/server/model_registry/identifier.py)。
