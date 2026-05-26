# 察元 AI 助手 用户使用手册

> 版本：v3.0 · 文档形态：随包预置 · 路径：`<CHAYUAN_ROOT>/manuals/察元使用手册.docx`
>
> 本手册随察元 AI 助手一同安装。**全量版**首次启动时，必需的对话 / 嵌入 / 重排
> 模型已自动选好，**无需手动配置即可使用全部功能**。**轻量版**装机不带本地大
> 模型，但首次启动会在设置页给出一键下载、去模型广场配厂商、或直接刷新本地已有
> 模型三条路径。

---

## 目录

1. 第一章 快速上手
2. 第二章 知识库管理
3. 第三章 模型管理
4. 第四章 结构化数据查询
5. 第五章 向量库与外部检索源
6. 第六章 办公私库
7. 第七章 多模态：图像 / OCR / 语音
8. 第八章 MCP 工具与扩展
9. 第九章 评测与模型对抗
10. 第十章 隐私 / 离线 / 国产化
11. 附录 A 路径与配置
12. 附录 B 故障排查

---

## 第一章 快速上手

### 1.1 打开应用

桌面端双击 `chayuan-desktop` 图标，或 Web 端访问后端 `http://<host>:<port>`
（默认 `127.0.0.1:62581`）。

### 1.2 直接对话

主界面左侧栏点「新对话」，下方输入框打字即可。全量版第一次提问会用预置的
**Qwen3-4B-Instruct**（默认对话模型）；轻量版若尚未配置，会提示先去
「设置 → 默认模型」补默认值。

### 1.3 与文档对话

把任意 PDF / Word / Markdown / 网页拖到主界面，应用自动：

1. 解析正文 + 抽取段落；
2. 用 **gte-multilingual-base**（mGTE，默认文本嵌入模型）把每个段落切片做向量；
3. 进入「临时会话知识库」；
4. 下一句提问就会引用这份文档作答。

引用条目会显示来源文件 / 页码 / 评分 / 命中片段，点开可以跳回原文。

### 1.4 三句话学完

* 装上即用：默认模型自动选好。
* 拖文件就有 RAG：文档 → 向量 → 引用，链路自动接好。
* 数据不出本机：除非你主动用云模型，本地知识库内容不会上传。

---

## 第二章 知识库管理

### 2.1 知识库的五类知识源

* **文档知识库 (`doc:<name>`)** — 适合 PDF / Word / Excel / Markdown / HTML 等普通文档。
* **结构化数据库 (`src:<id>`)** — 接入 SQLite / MySQL / PostgreSQL / Oracle / 达梦 /
  金仓 / Doris 等，支持 `text2sql` 自然语言查询。
* **外部向量库 (`vec:<id>`)** — 接入你自己的 Milvus / Chroma / Zilliz / PGVector /
  Relyt 集合，察元只负责连接与查询。
* **办公私库 (`office:<owner>[:<group>]`)** — 个人 / 团队隐私场景，永不上云。
* **图像知识库 (`img:`)** — 上传图片做 CLIP 图像向量，支持以图搜图 / 以文搜图。

### 2.2 创建文档知识库

侧边栏「知识中心 → 创建知识库」。表单需要填：

| 字段 | 说明 |
|---|---|
| 名称 | 用作 `doc:<name>` 唯一标识 |
| 嵌入模型 | 默认 `gte-multilingual-base`；KB 一旦索引完成，**查询阶段不能更改** |
| 切分参数 | 段落 / 字符 / 中文分句三选一；推荐先用默认 |
| 重排开关 | `bge-reranker-v2-m3` 默认启用；提升相关性 ≈ 8-15% |

### 2.3 导入文档

* 单文件：知识库主页「+ 上传」按钮。
* 批量：拖一个文件夹进窗口，自动按目录结构落到子分类。
* WPS：装好「察元 AI」插件后，WPS 内右键 → 加入知识库。

支持格式：`.pdf .docx .doc .md .txt .html .pptx .xlsx .csv` 以及 zip / tar
内嵌的同类文件（自动解包）。

### 2.4 检索 / 引用

聊天框左下角「绑定知识库」按钮可挑一个或多个 KB 作为当前对话的检索源。

* `ku_ids` 是真源（前端 / WPS 都用同一份契约），格式：
  - `doc:my-handbook`
  - `src:internal-pg-warehouse`
  - `office:alice:r&d`
* 检索结果以引用条目展示；点击条目可跳回原文段落定位。

### 2.5 删除 / 重建

知识库主页右上角「设置」可重建索引（换嵌入模型 / 调切分参数）或彻底删除。
彻底删除会同时清掉向量库分区 + SQLite 元数据 + 原始文件。

---

## 第三章 模型管理

### 3.1 默认模型

「设置 → 默认模型」展示 6 类 capability：对话 (chat) / 文本嵌入 (embedding) /
重排 (rerank) / 语音识别 (asr) / 图像嵌入 (image-embedding) / 图像识别文字 (ocr)。

每类下拉框列出所有可选模型：

* 标有「· 本地」的是装机时随包释放或你手动放入 `<CHAYUAN_ROOT>/models/`
  的模型。
* 其余条目来自「模型广场」配的云厂商。

### 3.2 模型广场

侧边栏「模型广场」是云 / 本地厂商的统一配置入口：

* **添加平台**：选 `ollama / xinference / openai / dashscope / deepseek /
  ...` 填 endpoint + api_key。
* **测试连通**：点「测试」会发一次轻量 ping，失败会给出诊断（错误码 / DNS /
  鉴权）。
* **可用模型**：每个平台展开后列出已注册模型，勾选「启用」让它进默认下拉。

### 3.3 模型库自检（BootstrapBanner）

「设置 → 默认模型」顶部若出现黄条「模型库不完整 — 缺失 文本向量 / 重排」，意味
着系统检测到某些必需 capability 没有可用模型。三个按钮分别处理：

1. **下载轻量版 / 全量版包** — 后端 subprocess 跑 `chayuan_packaging
   fetch+stage`，进度可见，可取消，完成后自动 promote 默认。
2. **去模型广场配厂商** — 跳转到模型广场配云模型。
3. **我已有模型，刷新扫盘** — 已经手动把模型放进
   `<CHAYUAN_ROOT>/models/`？点这个就立刻重新扫盘 + 自动指派默认。

### 3.4 命令行查看链路

运维场景不打开 GUI 也能 debug：

```bash
chayuan model status           # 人类可读三段输出
chayuan model status --json    # 给脚本消费
chayuan model status --no-scan # 不扫盘
chayuan model scan             # 强制重扫
chayuan model list             # 列出已知本地模型
chayuan model import <path>    # 把任意路径软链入 <CHAYUAN_ROOT>/models/custom/
chayuan model download <id>    # 按 catalog 下载
```

### 3.5 推理引擎进程

llama-server / infinity / ollama 由 supervisor 进程编排。`chayuan model
status` 的「推理引擎启动参数」段会显示每个进程现在挂的具体模型路径。如果
缺项（`missing`），说明 capability default 还没指好。

### 3.6 自带模型与替换工作流

全量版安装包内带了"装机自带"模型，存放约定如下：

```
<CHAYUAN_ROOT>/models/bundled/
├── chat/         对话模型（gguf / safetensors / ...）
├── embedding/    文本向量
├── rerank/       重排
├── asr/  ocr/  image/  custom/
```

首启时服务端会自动完成三件事：

1. **Seed** — 从安装包内 `vendor/bundled_models/` 复制到上面这个目录；
   已有同名文件且大小一致 → 跳过（保留用户改动）。
2. **Scan** — 扫盘把这些文件登记进本地模型索引（relpath 以 `bundled/` 开头）。
3. **Auto-assign** — 对 yaml 里还没指派的 capability，自动选用扫到的对应模型
   作为默认；用户已手动配过的 default 不会被覆盖。

**想替换某个自带模型**：直接把新文件放到对应 `<CHAYUAN_ROOT>/models/bundled/<cap>/`
路径下，覆盖同名文件即可。下一次 server 启动（或运行 `chayuan model scan`）
就会自动识别新版本；如果模型 ID 变了，可能需要在「设置 → 默认模型」里重新选。

**完全不想用自带模型**：删掉 `<CHAYUAN_ROOT>/models/bundled/` 下对应子目录的
文件，扫盘后那条记录从索引消失，default 重新空缺时 BootstrapBanner 会弹出
引导你下载或配云厂商。注意：仅删除用户数据目录里的副本不会影响安装包里的
原始文件；如果再次首启又把同样内容种回来，可以用环境变量
`CHAYUAN_BUNDLED_MODELS_DIR` 指向一个空目录来阻止 seed。

---

## 第四章 结构化数据查询

### 4.1 添加 SQL 数据源

「知识中心 → 添加结构化数据库」表单：

| 字段 | 说明 |
|---|---|
| 名称 | 用作 `src:<id>` |
| 数据库类型 | PostgreSQL / MySQL / SQLite / DM / KingbaseES |
| 连接串 | `postgresql://user:pwd@host:port/db` |
| 白名单表 | 用逗号分隔；为空时取所有表 |

### 4.2 自然语言查询

绑定 `src:*` 知识源后，问："最近 30 天订单总额是多少？"

系统走结构化查询链路：

```
intent detection
  → schema linking (取相关表 / 列)
  → SQL planning (LLM 生成候选 SQL)
  → read-only AST validation (拒绝 INSERT/UPDATE/DELETE/DROP)
  → execution
  → result verification
  → grounded answer (自然语言总结)
```

### 4.3 诊断字段

聚合类查询的结果默认带：

* `sql`：实际跑的 SQL
* `table`：命中的表
* `rows_returned`：行数
* `intent`：识别意图（`document_qa / structured_aggregate / structured_lookup
  / vector_semantic / multi_source`）
* `validation`：通过 / 拒绝 + 原因

### 4.4 不要做的事

* **不要**给结构化查询挂普通文档 RAG —— 数字查询走 SQL 才是确定性正确。
* **不要**让 LLM 直接编 SQL 而不走 AST 校验 —— 生产环境严格只读。

---

## 第五章 向量库与外部检索源

### 5.1 接入外部向量库

支持 Milvus / Chroma / Zilliz / PGVector / Relyt，配置后以 `vec:<id>` 暴露。

向量库返回的结果与文档 KB 区别：

* 字段：`collection / vector_id / payload / metadata / score`
* **不一定有可下载的文件**——展示时不要显示"下载"按钮。

### 5.2 嵌入维度对账

每个外部向量库的 embedding 模型在创建时就钉死，**查询阶段不能覆盖**。维度
不一致会被拒绝。

### 5.3 混合检索

文档 KB 默认走「向量 + BM25 + 重排」混合路径。开关在 KB 设置页：

* `use_hybrid` — 启用 BM25 与向量融合
* `use_rerank` — 用 bge-reranker-v2-m3 二次精排
* `use_expand` — 邻居 chunk 合并（简化版 Parent-Child）

---

## 第六章 办公私库

### 6.1 隐私场景

`office:*` 私库代表"本机硬性不上云"的隐私域。任何走云模型的 prompt 不会
带入此私库的内容，除非用户在弹窗里明确「我知道风险」。

### 6.2 创建 office 私库

「知识中心 → 创建办公私库」三选项：

* 个人私库：`office:<your-username>`
* 团队私库：`office:<owner>:<group>`
* 一次性私库：会话结束自动销毁

### 6.3 访问规则

* 仅 owner / group 成员可读。
* 不出现在跨用户检索范围。
* 删除 = 彻底擦除（双重确认 + 安全擦除）。

---

## 第七章 多模态：图像 / OCR / 语音

### 7.1 图像知识库

「知识中心 → 创建图像知识库」上传图片后，系统用 CLIP-style 模型生成图像
向量。提问"找几张红色背景的产品图"会以图像向量检索。

### 7.2 OCR

PDF 含扫描页时自动走 OCR（RapidOCR / PaddleOCR），中英文混排可达 95%+。

### 7.3 语音识别 (ASR)

侧边栏麦克风按钮：

* 离线场景走 whisper-tiny / FunASR
* 在线场景可配通义 / 百度等云端 ASR

---

## 第八章 MCP 工具与扩展

### 8.1 什么是 MCP

Model Context Protocol：让 AI 助手调用外部工具的标准协议。察元内置常用工
具（计算 / 时间 / 网页抓取 / 文件读写），也可外接你自家的 MCP server。

### 8.2 添加 MCP server

「设置 → 高级 → MCP server」填写 URL + transport（stdio / sse / http），
保存即接入。

### 8.3 工具调用

对话中可显式说："用 web_fetch 工具看一下 https://example.com" 触发工具调
用。结果以引用形式回到答案里。

---

## 第九章 评测与模型对抗

### 9.1 评测中心

「评测 → 新评测」上传一份 jsonl 或 csv（query / golden_answer 对），选一
个或多个待评模型，跑完会出每条样本的得分 + 总体指标（accuracy / NDCG /
groundedness / hallucination_rate）。

### 9.2 模型对抗

「评测 → 模型对抗」让两个模型对同一组 prompt 输出，盲评模式由你或 LLM
裁判选哪个更好。结果以 ELO 风格记分。

### 9.3 监督

跑过的所有 eval 都进 Langfuse 追踪；管理员可在「Admin → Langfuse Trace」
列表查看 trace 详情。

---

## 第十章 隐私 / 离线 / 国产化

### 10.1 数据落地

* 聊天历史：`<CHAYUAN_ROOT>/data/chat_history.db`（SQLite，加密可选）
* 知识库元数据：`<CHAYUAN_ROOT>/data/knowledge_base/info.db`
* 向量分区：`<CHAYUAN_ROOT>/data/knowledge_base/vector_store/`
* 模型权重：`<CHAYUAN_ROOT>/models/`

所有数据**都在本机**。仅当你主动用云模型时，对应 prompt 才会发到该厂
商。

### 10.2 完全离线

全量版装机自带 Qwen3-4B / gte-multilingual-base / bge-reranker-v2-m3 /
whisper.cpp tiny / CLIP ViT-B/32 / RapidOCR 等。断网后所有功能正常，**除非**你
显式选了云模型。

### 10.3 验证方法

* **断网测试**：拔网线后聊天 / 检索 / 引用是否正常 —— 应该全过。
* **抓包**：Wireshark 抓 chayuan-desktop 流量 —— 完全离线场景应无外部
  请求。
* **代码与行为**：桌面版闭源、免费下载；察元 WPS 加载项 Apache-2.0 开源
  （GitHub / Gitee），其离线行为可自行审阅。

### 10.4 国产化栈

* CPU：鲲鹏 / 飞腾 / 龙芯
* OS：麒麟 V10 / 统信 UOS / openKylin / loongnix
* 数据库：达梦 / 金仓 / 神通 / OceanBase
* 模型：Qwen / 文心 / 智谱 / DeepSeek 等

完整国产化栈无外国组件。详见 `articles/16-localization/` 系列文章。

---

## 附录 A 路径与配置

### A.1 关键路径

| 路径 | 内容 |
|---|---|
| `$CHAYUAN_ROOT` | 数据根目录；默认 `~/chayuan_data`，可在 `chayuan init` 时改 |
| `$CHAYUAN_ROOT/basic_settings.yaml` | 主配置（端口 / 鉴权 / 日志） |
| `$CHAYUAN_ROOT/model_settings.yaml` | 6 类 capability 默认模型 + 平台清单 |
| `$CHAYUAN_ROOT/models/` | 本地模型仓库（自动扫盘） |
| `$CHAYUAN_ROOT/data/` | 用户数据（聊天 / 知识库 / 评测） |
| `$CHAYUAN_ROOT/manuals/` | 本手册及未来扩展 |

### A.2 切换 CHAYUAN_ROOT

```bash
# 临时
export CHAYUAN_ROOT=/srv/chayuan
chayuan service start

# 持久（写 ~/.chayuan/root）
chayuan init --profile prod
```

---

## 附录 B 故障排查

### B.1 启动后聊天返回 "no LLM"

打开「设置 → 默认模型」，看「对话」一栏是否为空。

* 全量版：本应自动指派；若空，跑 `chayuan model status` 看 local_index
  是否扫到模型；若已扫到但 default 没填，重启 server 让 first_launch hook
  再跑一次自动指派。
* 轻量版：点「下载全量包」或去模型广场配云厂商。

### B.2 知识库检索返回 0 条

KB 主页「设置」看「索引状态」：

* `empty` — 还没导入文档
* `indexing` — 正在切分 / 向量化
* `stale` — 模型 / 切分参数变了，需要重建
* `failed` — 嵌入模型不可达；用 `chayuan model status` 排查

### B.3 下载任务卡住

「设置」顶部 Banner 显示"下载中"超过 30 分钟仍未完成：

1. 看 log_tail（每行进度），可能某个文件在 hf-mirror 上拉慢；
2. 点「取消下载」终止；
3. 重新发起，packaging 自带断点续传（`packaging/.cache` 缓存已下载部
   分）；
4. 切镜像（在设置页 "下载" 按钮的镜像选项）。

### B.4 推理引擎进程异常

`chayuan model status` 看「推理引擎启动参数」段。常见：

* `llamacpp: missing=chat` — chat 默认模型空，进 GUI 选一个。
* `infinity: missing=embedding,rerank` — 嵌入 / 重排默认空，同上。
* `ollama: missing=ollama_models_dir` — CHAYUAN_ROOT 不可写或路径不存
  在。

修好默认后跑 `chayuan service restart` 让 supervisor 重启对应子进程。

### B.5 获取帮助

* 命令行：`chayuan doctor` 一键体检
* 论坛 / Issue Tracker：见项目 README

---

> 本手册随版本演进，详细 API / 架构请参考 `articles/` 与
> `chayuan-server/libs/chayuan-server/chayuan/server/model_registry/*.py` 模
> 块 docstring。
