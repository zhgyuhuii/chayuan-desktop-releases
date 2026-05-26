# Chayuan Server 基础环境必装清单(56 题)

`pip install chayuan-server` 一次性安装,无需后续手动 install。

## 内置 modality wrapper(无需 docker)

| 类别 | 包 | 用途 | 大小估算 |
|---|---|---|---|
| **OCR** | `rapidocr_onnxruntime` | 中英 OCR(轻量,内置模型) | ~150 MB |
| **OCR** | `paddleocr` + `paddlepaddle` | 文档识别主力(无 docker 镜像可替代) | ~1.5 GB |
| **TTS** | `piper-tts` | CPU TTS(轻量,内置中文音色) | ~30 MB |
| **Embedding/Rerank** | `sentence-transformers` | RAG 必需,默认可用 | ~50 MB(模型按需下) |

## 共享 ML 核心(transitive)

由 `sentence-transformers` / `paddlepaddle` 拖入:
- `torch`(CPU 版,~2 GB)
- `transformers`(~500 MB)
- `numpy / scipy / sklearn / pillow / opencv`(~500 MB)

## 总体(基础必装)估算

| 阶段 | 大小 |
|---|---|
| chayuan + 通用依赖(FastAPI/NiceGUI/SQLA/langchain) | ~1 GB |
| 共享 ML 核心(torch CPU + transformers + sklearn) | ~3 GB |
| modality wrapper(rapidocr + paddleocr + piper) | ~1.7 GB |
| 合计 | **~5.7 GB** |

## 可选扩展(extras)

仅在用户明确需要时装:

```bash
pip install "chayuan-server[rag-graph]"   # RAPTOR + GraphRAG (umap-learn / louvain)
pip install "chayuan-server[ks-async]"    # 异步 SQL 驱动(asyncpg / asyncmy / aiosqlite / motor)
pip install "chayuan-server[ks-mssql]"    # SQL Server (pyodbc / pymssql)
pip install "chayuan-server[ks-oracle]"   # Oracle (oracledb)
pip install "chayuan-server[ks-clickhouse]"  # ClickHouse
pip install "chayuan-server[tools-all]"   # T1/T2 工具全套(yfinance / PyGithub / ...)
pip install "chayuan-server[storage-minio]"  # MinIO 对象存储
pip install "chayuan-server[image]"       # 图像知识源
pip install "chayuan-server[eval]"        # RAGAS 评估
```

## docker 类(完全可选)— 走 docker compose

放在 `<CHAYUAN_ROOT>/compose/*.yaml`,**完全不进 Python 依赖**:

| Service | yaml 模板位置 | 何时启动 |
|---|---|---|
| vllm | `compose/vllm.yaml` | 用户在 UI 点 ▶ 启动 |
| infinity | `compose/infinity.yaml` | 同上 |
| comfyui | `compose/comfyui.yaml` | 同上 |
| llamacpp | `compose/llamacpp.yaml` | 同上 |
| ollama | `compose/ollama.yaml` | 同上 |
| onlyoffice | `compose/onlyoffice.yaml` | 同上 |
| 用户自定义 | `compose/<service>.yaml` | UI 自动发现卡片 |

## funasr / cosyvoice / voxcpm2 / vllm 的处理

这些**不在基础必装**清单里(模型权重 5-10 GB,占空间太大),用户可以:

1. **走 docker**(推荐):用 vllm.yaml(已内置)启动
2. **手动 pip 装**:`pip install funasr` / `pip install cosyvoice` 等(extras 可选)
3. **暂不需要**:不装也不影响 chayuan 主流程(对应能力降级为不可用)

## 一次性安装命令

```bash
# 基础环境(必装)
pip install chayuan-server

# 加几个常用 extras
pip install "chayuan-server[ks-async,tools-all]"
```

整个 chayuan-server 主依赖装完即可使用大多数功能,**不需要任何手动 pip install
funasr / paddleocr / sentence-transformers / 等"经常被需要"的包**。
