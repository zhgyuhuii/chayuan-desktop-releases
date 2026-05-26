<div align="center">

<img src="chayuan-client/images/logo.png" alt="Chayuan AI · 察元 AI" width="120" height="120" />

# 察元 AI · Chayuan AI 桌面单机版

**离线优先 · 国产系统适配 · 全栈本地知识库 · 多模型对抗**

[![Tauri 2](https://img.shields.io/badge/Tauri-2-24c8db?logo=tauri&logoColor=white)](https://tauri.app/)
[![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Made in China · 适配国产](https://img.shields.io/badge/Made%20in%20China-%E9%80%82%E9%85%8D%E5%9B%BD%E4%BA%A7-red)](#六支持的操作系统)

</div>

---

## Language / 语言

| Language | Document |
|----------|----------|
| **简体中文(完整说明,见下文)** | **本文档 [README.md](README.md)** |
| English | [README.en.md](README.en.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| Deutsch | [README.de.md](README.de.md) |
| Français | [README.fr.md](README.fr.md) |

技术性的打包流程文档:[PACKAGING.md](PACKAGING.md)

---

## 一句话定位

**察元 AI 桌面单机版**是一款**装到电脑里就能用、不联外网也能跑**的 AI 助手 ——
内置**多供应商大模型网关 · 文档/结构化/向量/办公/外部库五类知识源 · 30+ 内置工具 · MCP 协议 · 模型对抗**,
原生适配 **Windows / macOS / Linux / 麒麟 / 统信 UOS / openKylin** 等系统;
所有数据落到用户自选目录,**密钥与文档永不出域**,真正的"自有 AI"。

---

## 目录

- [一、版权声明与开源许可(AGPL-3.0)](#一版权声明与开源许可)
- [二、品牌标识保留要求](#二品牌标识保留要求)
- [三、与 chayuan-wps WPS 加载项的关系](#三与-chayuan-wps-wps-加载项的关系)
- [四、产品概述与系统架构](#四产品概述与系统架构)
  - [4.4 用户画像与典型场景](#44-用户画像与典型场景) ⭐ 多视角导读
- [五、核心特性](#五核心特性)
  - [5.7 图转文](#57-图转文image-to-text) · [5.8 音转文](#58-音转文audio-to-text) · [5.9 安装与首启动行为](#59-安装与首启动行为)
- [六、支持的操作系统](#六支持的操作系统)
- [七、与豆包 / Cherry Studio 等的 60+ 项细致对比](#七与豆包--cherry-studio-等的-60-项细致对比)
- [八、详细功能清单](#八详细功能清单)
  - [8.1 对话与流式](#81-对话与流式)
  - [8.2 知识库类型与文档格式](#82-知识库类型与文档格式)
  - [8.3 数据库连接器(结构化数据)](#83-数据库连接器结构化数据)
  - [8.4 向量库支持](#84-向量库支持)
  - [8.5 模型供应商](#85-模型供应商)
  - [8.6 嵌入模型 / 重排 / OCR](#86-嵌入模型--重排--ocr)
  - [8.7 多模态](#87-多模态)
  - [8.8 内置工具(30+)](#88-内置工具30)
  - [8.9 MCP(Model Context Protocol)](#89-mcpmodel-context-protocol)
  - [8.10 模型对抗(Model Arena)](#810-模型对抗model-arena)
  - [8.11 自动构建文档知识结构(Folder Sync)](#811-自动构建文档知识结构folder-sync)
  - [8.12 引用展示与原文回链](#812-引用展示与原文回链)
- [九、HTTP API 接口总览](#九http-api-接口总览)
- [十、开发者搭建指南](#十开发者搭建指南)
  - [10.4 本地打包(Windows / macOS / Linux 命令)](#104-本地打包默认双产物轻量版--集成版)
  - [10.8 装完后诊断与故障排查](#108-装完后诊断与故障排查) ⭐ services 目录空 / 服务起不来必读
- [十一、使用教程入口](#十一使用教程入口)
- [十二、安全 · 隐私 · 离线](#十二安全--隐私--离线)
- [十三、路线图](#十三路线图)
- [十四、社区 / 反馈 / 商业合作](#十四社区--反馈--商业合作)
- [十五、致谢与第三方组件](#十五致谢与第三方组件)
- [附录甲:常见问题(FAQ)](#附录甲常见问题faq)
- [附录乙:术语表](#附录乙术语表)

---

## 一、版权声明与开源许可

### 软件名称

- **中文全称:** 察元 AI · 桌面单机版
- **英文名:** Chayuan AI · Desktop (Single-Machine Edition)
- **包名 / 二进制名:** `chayuan-desktop`
- **官网:** [https://aidooo.com](https://aidooo.com)
- **出品方:** 北京智灵鸟科技中心

### 开源许可:AGPL-3.0

本仓库源代码依照 **[GNU Affero General Public License v3.0](LICENSE)** 授权。

> AGPL-3.0 与 Apache-2.0 / MIT 的关键区别在于 **网络传播条款(§13)**:
> 一旦您把基于本软件构建的版本通过网络对**外部用户**提供服务(SaaS / 私有云 / 公网部署),
> 您**必须以相同的 AGPL-3.0 许可向这些用户提供完整的对应源代码**(包括您自己的修改)。
> 仅在**单位内部、内网部署、本机使用**这一类 "不构成公开网络传播" 的形态下,
> 您可以保留对源代码的封闭修改。

**这意味着:**
- ✅ 个人 / 团队 / 企业内部部署、内网使用、本地修改:**自由,无任何商业限制**
- ✅ 二次开发后再分发安装包(保留 AGPL 与版权声明):**允许**
- ⚠ 把修改后的版本部署成 **SaaS / 公网服务** 提供给外部用户:**必须开源您的修改**
- ⚠ 集成到您的闭源商业产品中并对外销售:需先取得**单独的商业许可**

如需 **企业商业授权 / OEM 白标 / 技术支持服务合同**,请通过官网 [https://aidooo.com](https://aidooo.com) 商务渠道联系。

### 著作权与免责

产品由 **北京智灵鸟科技中心** 研发与运营,涉及的界面文案、默认提示词、图标、品牌素材等
除第三方组件按其各自许可外,均受著作权与相关知识产权法保护。

**免责声明(摘要):** 本软件按 "原样" 提供;大模型生成内容可能存在不准确或不适用情形,
涉密、合规与法律判断应以人工与正式制度为准;任何检查类辅助功能(保密检查、AI 痕迹检查等)
**仅作辅助参考**,不构成司法鉴定或保密定密结论。

---

## 二、品牌标识保留要求

为保证用户知情权、来源可追溯性与品牌一致性,**面向最终用户的界面中**,以下位置出现的
**"察元"** 及其固定搭配的产品称谓(包括但不限于 "察元 AI"、"察元 AI 助手"、"察元智库"、
"察元对抗"、"关于察元" 等)属于**产品来源与品牌标识**的重要组成部分:

- 应用窗口标题、Splash、关于页、设置 → 关于
- 系统托盘图标提示、桌面快捷方式名称(默认 `察元AI.lnk`)
- 帮助中心 / 反馈弹窗中的固定品牌表述
- 与上述同语义链路的用户可见字符串

**未经权利人书面授权,任何再分发或定制版本不得对上述位置的"察元"相关固定文案进行
替换、删减、遮挡、淡化或误导性改写**(例如改为其他商业名称却仍指向本软件,使用户误认为来源已变更)。

此约束**不构成**对 AGPL-3.0 所允许之 "修改源代码" 本身的禁止 —— 您仍可在内部构建中调整代码逻辑;
但若您向第三方提供**可安装的、面向最终用户的构建产物**,需保留品牌标识的显著性,
或事先取得权利人书面同意按约定方式标注来源。**白标 / 整体本地化** 涉及品牌字段调整,
请通过商务渠道获取**单独授权条款**。

---

## 三、与 chayuan-wps WPS 加载项的关系

察元 AI 是一个**端到端办公 AI 平台**,目前由两个互补的开源项目共同覆盖:

| 项目 | 仓库 | 形态 | 主要用户 |
|---|---|---|---|
| **chayuan-desktop**(本仓库) | (内部) | **桌面单机应用** —— Tauri 2 外壳 + 嵌入式 Python 后端 | 不愿暴露密钥/文档到外网的个人、政企单机用户 |
| **chayuan-wps** | <https://github.com/zhgyuhuii/chayuan.git> | **WPS 文字加载项** —— Vue 3 加载项,运行在 WPS 内部 | 重度使用 WPS 编辑公文 / 合同 / 标书的政企作者 |

### 二者如何协作

```
                ┌────────────────────────────────────────────┐
                │   察元 AI 桌面单机版 (chayuan-desktop)      │
                │   ─────────────────────                    │
                │   • 嵌入式 chayuan-server(Python)         │
                │   • 知识库 / 模型网关 / 工具 / MCP          │
                │   • 监听 127.0.0.1:62581                   │
                │   • 数据落用户目录 (CHAYUAN_ROOT)          │
                └─────────────────┬──────────────────────────┘
                                  │  HTTP/REST(同机或内网)
                                  │  /api/v1/kb-query/search
                                  │  /api/chat/completions
                                  │  /openapi/v1/*  (HMAC 签名)
                                  ▼
                ┌────────────────────────────────────────────┐
                │   chayuan-wps  (WPS 加载项, Vue 3 + Vite)   │
                │   ─────────────────────                    │
                │   • 运行在 WPS 文字内,任务窗格 + Ribbon    │
                │   • 选区/全文上下文感知                     │
                │   • 写回链路:插入/替换/批注/链接批注       │
                │   • 调用桌面单机版的 /api/* 拿模型与知识    │
                └────────────────────────────────────────────┘
```

**典型用法**:用户在公司电脑上装好 **chayuan-desktop**,作为本机 "AI 服务器",
后端 sidecar 起在 `127.0.0.1:62581`;再在 WPS 里安装 **chayuan-wps** 加载项,
加载项的 "服务器地址" 配置成 `http://127.0.0.1:62581`,**两边共用同一份知识库、
同一套模型配置、同一份对话历史** —— 在 WPS 里发起的对话也会出现在桌面客户端的历史里。

### 现状

- 当前 **chayuan-wps v3.0** 已落地远程知识库 RAG 集成(JWT 用户态 + HMAC 应用态),
  完整支持把 chayuan-desktop 当作后端使用。
- 桌面单机版默认 **关闭鉴权**(单机用户没有用户的概念),WPS 加载项侧已加 `authMode: 'none'`
  分支,可以无密钥直连本机后端。

详见 chayuan-wps 仓库的 [README](https://github.com/zhgyuhuii/chayuan/blob/main/README.md) 与 RELEASE_NOTES_v3.0。

---

## 四、产品概述与系统架构

### 4.1 它是什么

**察元 AI 桌面单机版**把一个完整的"企业级 AI 后端"塞进了你的电脑里 —— 安装包打开就能用,
不需要 Docker、不需要单独装 Python、不需要起 Redis / RabbitMQ / Postgres。所有重活
(语言模型调用、知识库索引、向量检索、工具执行、流式编排)都在本机进程里完成,
前端是一个原生窗口的 React 应用,后端是一个嵌入到安装包里的 Python sidecar。

### 4.2 三层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend  Tauri 2 + React 19 + Tailwind                              │
│  ───────────────────────────────────────────────────                  │
│  • 多 Tab 浏览器式外壳(主页 / 聊天 / 知识库 / 模型广场 / MCP / 工具)  │
│  • 多泳道模型对抗 (Model Arena)                                       │
│  • 折叠式工具调用展示 / 引用面板 / 流式 reasoning                     │
│  • 本地 SQLite 持久化对话(Tauri sql 插件)                          │
│  • Stronghold 凭据保险箱(ChaCha20-Poly1305 + Argon2id)             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │  spawn + /healthz 探活
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Sidecar  chayuan-server (Python 3.12, FastAPI)                       │
│  ───────────────────────────────────────────────────                  │
│  • 单机 profile:无 Redis / 无 Celery / 无 PostgreSQL                  │
│  • 向量库:sqlite-vec (内嵌 SQLite 扩展) / 可选 FAISS                 │
│  • 缓存:cachetools TTLCache / 队列:asyncio.Queue                    │
│  • 嵌入模型:ONNX 本地(默认 bge-m3-onnx)/ Ollama / OpenAI 兼容     │
│  • OCR:RapidOCR-ONNX(纯 CPU,~70 MB)                              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │  HTTP / OpenAI 兼容
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  External  用户自选的模型与知识源                                     │
│  ───────────────────────────────────────────────────                  │
│  • 本地推理:Ollama / LM Studio / vLLM / Xinference                  │
│  • 云端 LLM:OpenAI / DeepSeek / 通义千问 / 智谱 / 文心 / Moonshot…  │
│  • 业务数据库:MySQL / PostgreSQL / Oracle / 达梦 / 金仓 / Doris…    │
│  • 外部向量:Milvus / Chroma / Elasticsearch / Zilliz                │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 启动时序

1. 用户双击 `察元AI` 桌面图标
2. Tauri 主窗口(`window.backgroundColor = #0f172a`)瞬间出现,**首屏即有炫酷 splash 动画**
   (五层零延迟挂载:OS 窗口背景 → HTML inline CSS → splash 节点 → JS 自检注入 → React 接管时同步淡出)
3. Tauri 主进程 spawn 嵌入的 `chayuan-server` 子进程,通过 `CHAYUAN_ROOT=<用户首启动选定的目录>`
   注入数据路径
4. 前端 SidecarGate 轮询 `/healthz`,后端就绪后渲染主界面
5. 首次启动用户自选 "数据目录"(默认平台标准目录),后续启动直接复用
6. **卸载重装会重新弹首启向导** —— 通过 exe 路径 + 修改时间 hash 出 install_id,
   绑定到 `desktop.json`;MSI 卸载会清掉 install dir,重装后 mtime 变 → install_id 变 →
   跳不过向导,确保新装实例不会"幽灵复用"老数据目录

### 4.4 用户画像与典型场景

> README 这一节专为不同身份的读者提供 "**这东西能给我做什么**" 的视角索引。
> 同样一个功能,从安全官 / 法务 / 开发者 / 工科学生 / 政企文员 视角看到的重点完全不同。

#### 4.4.1 政企单位文员 / 涉密单位作者

**痛点**:文档涉敏感数据,密钥与原文**不允许出域**;Windows 上还要面对火绒 / 360 / 腾讯管家的拦截。

| 关注点 | 察元 AI 的对应能力 |
|---|---|
| 全程本地 | 嵌入式 Python sidecar、嵌入式 ONNX 嵌入模型、嵌入式 OCR、嵌入式 sqlite-vec,**装机即用、无网络出口** |
| 杀软兼容 | Windows 安装包 MSI + 外置 CAB(`EmbedCab="no"`)+ ISO 单文件分发,绕开杀软对 `C:\Windows\Installer\` 的拦截 |
| 中文文档 | RapidOCR-ONNX 默认带 PaddleOCR 转 ONNX 的 PP-OCRv4 中文权重 |
| 国产 OS | 麒麟 V10 / UOS / openKylin / deepin,x86_64 + aarch64 + 龙芯三种架构发版 |
| 与 WPS 协作 | 同机装 chayuan-wps 加载项,WPS 内编辑公文/合同/标书时**同一份知识库一键复用** |

**典型场景**:把单位过去三年的合同 / 标书 / 公文整成一个本地 KB,
WPS 内写新文件时让察元 AI 引用最相关的几段历史措辞,**所有数据在本机硬盘,从未离开**。

#### 4.4.2 个人知识库爱好者 / 研究生 / 自由职业

**痛点**:Notion / Obsidian 各家工具的搜索能力局限,跨格式跨语言效果一般;想自己跑 Llama / Qwen 但工具链门槛高。

| 关注点 | 察元 AI 的对应能力 |
|---|---|
| 文档格式覆盖广 | PDF / Word / Excel / PPT / MD / HTML / 图像 / 邮件 / 电子书一齐通吃 |
| 跨工具知识联邦 | 同一个查询同时打文档库 + Mongo 笔记 + Notion + GitHub Issues + arXiv |
| 本地 LLM 一键对接 | 你装好 Ollama / LM Studio,模型广场添加后**自动拉模型清单** |
| 模型对抗 | 同时跑 Qwen / DeepSeek / Llama 看哪家答得最准,横向对比 |
| 多语种嵌入 | bge-m3 默认 1024 维,中英德法日韩多语单 KB 检索效果都稳 |

**典型场景**:把 Anki 整年读过的论文 + 个人 Obsidian 笔记 + 关注的 arXiv RSS 全做成 KB,
模型对抗 "qwen2.5:14b vs deepseek-r1 vs llama3.3" 三家同时回答 "这个领域 2025 年最重要的三篇论文是什么?"。

#### 4.4.3 开发者 / 二开商 / OEM 合作伙伴

**痛点**:想做一个"自己的 AI 产品",但从 0 到 1 搭后端、做 RAG、对接十几家模型平台、过国产化适配,投入太大。

| 关注点 | 察元 AI 的对应能力 |
|---|---|
| OpenAI 兼容 | `/openai/v1/chat/completions`,任何 OpenAI SDK 把 base_url 改一下就连本机 |
| OpenAPI 鉴权 | HMAC 签名 + 应用 ID + 权限策略,给二开应用做"自带后端" |
| MCP 双角色 | 既是 MCP 客户端(接外部工具),也是 MCP 服务端(把自己当工具暴露给 Cursor / Claude Desktop) |
| 30+ 内置工具 | 文档/SQL/图像生成/搜索/钉钉飞书/Office,可以直接复用 |
| 全栈源码 | AGPL-3.0,审计无死角;商业授权可通过商务渠道获取 OEM 白标 |
| 三平台 CI | GitHub Actions 5-runner 矩阵开箱即用 |

**典型场景**:基于察元做一个"金融行业问答机器人"。直接复用模型网关、KB 检索、SQL 查询、HMAC 鉴权;
你的代码只关心"行业知识"(prompt 模板 + 行业数据源 + 行业工具)。**从 0 到 1 砍掉 80% 工作量**。

#### 4.4.4 法务 / 合规 / 安全官

**痛点**:AI 工具引入企业后,数据流向、审计、PII 风险、模型行为合规性都得有解释。

| 关注点 | 察元 AI 的对应能力 |
|---|---|
| 数据驻留 | 用户首启动选定的目录(本机 / 内网共享盘),**0 出网默认**;遥测显式 opt-in |
| 凭据加密 | Tauri Stronghold,**ChaCha20-Poly1305 + Argon2id**,模型 Key 永不进日志 |
| 审计 | `governance/` 模块的 audit / quota / PII redact / lineage 四件套 |
| 只读 SQL | text2sql 强制只读 AST 校验,白名单表/列,LLM 不可能"误删数据" |
| 开源许可 | AGPL-3.0 全栈源码可审计,二次开发的修改在 SaaS 场景下也得开源 |
| 离线运行 | 本地 LLM(Ollama 等)+ 本地嵌入(bge-m3)+ 本地 OCR(RapidOCR)= 完全无外部依赖 |

**典型场景**:为单位上线 AI 助手前,法务要求"列出本软件所有外部网络出口"。
答:本地全装时**为 0**;配云端 LLM 时**仅模型 API 域名**;审计日志默认本地文件,可对接 SIEM。

---

## 五、核心特性

### 5.1 离线优先(Offline-First)

- **嵌入式后端**:Python 解释器 + 全部 wheels + sqlite-vec 扩展 + 资源文件全部进安装包,装机即可离线启动
- **嵌入式模型**:默认带 ONNX 量化的 bge-m3 嵌入模型(~120 MB),不需要外网下载
- **嵌入式 OCR**:RapidOCR-ONNX 模型权重 bundle 进 sidecar,文档解析全本地
- **可选本地 LLM**:与 Ollama / LM Studio / vLLM / Xinference 一键对接,大模型推理也能完全断网
- **离线 Doctor 命令**:自检数据目录、sqlite-vec 扩展、嵌入模型完整性

### 5.2 察元智库(Knowledge Universe)

> **统一查询路径** —— 把文档、结构化数据库、外部向量库、办公私库、图像库五类知识源
> 抽象为 `ku_id` (Knowledge-Universe ID),前端选什么类型的源,服务端自动路由到对应的检索 adapter。

- **`POST /api/v1/kb-query/search`** 一个端点搞定多源混合检索
- 五类源:
  - **`doc:<kb_name>`** 文档知识库(PDF / Word / Excel / Markdown / HTML / 图像 …)
  - **`src:<source_id>`** 结构化数据源(SQL / MongoDB / Elasticsearch)
  - **`vec:<collection>`** 外部向量库(Milvus / Chroma / Zilliz)
  - **`office:<owner>[:<group>]`** 办公私库(企业 / 团队 / 个人三层)
  - **`img:<kb_name>`** 图像知识库(CLIP 嵌入)
- 统一返回 `RetrievalChunk` + `Citation`,带原文回链 / 信任度 / 生成的 SQL DSL / collection / vector id
- **混合检索**:向量 + BM25 关键词同跑,加权打分
- **重排**:可选 BAAI/bge-reranker-v2-m3 等 cross-encoder
- **诊断**:每次查询返回 `Diagnostic[]`,排查 "答非所问" 时一目了然

### 5.3 察元 AI 对话(Chayuan Chat)

- **多 Tab 浏览器式外壳**:像浏览器一样打开多个对话,每个 Tab 独立持有 conversationId / 模型 / KB 选择
- **流式 markdown 渲染**:Shiki 代码高亮 + reasoning(深度思考)token 折叠展示
- **工具调用三层折叠**:
  - 第一层:摘要 chip(图标 + 工具名 + "已调用 N 次")
  - 第二层:展开看 args / output 摘要
  - 第三层:再点开看完整 JSON
- **引用面板**:KB 来源列表 + 信任度星级 + 一键打开原文 / 下载附件
- **附件上传**:拖拽 / 粘贴 / 点击,自动 OCR 入库后参与对话上下文
- **对话本地持久化**:Tauri SQLite 插件,删账号也不丢历史

### 5.4 多模型对抗(Model Arena)

- 一个对话页面**最多 N 个泳道**(无上限),每个泳道独立选模型
- **统一发送**:打勾后,在任何一道输入,会同时发到所有泳道,横向对比生成质量
- **泳道操作**:折叠 / 调宽 / 拖动重排 / 添加 / 删除
- **折叠条标题**:自动取该泳道的**首条用户提问**作为竖向标签(如 "你是谁"),不是模型名

### 5.5 模型广场(Model Marketplace)

- 7 个分类标签:推荐 / 全部 / 本地 / 国内 / 国外 / 聚合 / 自定义
- 厂商卡片网格:logo + 名称 + 标签 + 启用开关 + 设置齿轮
- **自动拉取模型清单**:填了 API Key 失焦自动调供应商 `/v1/models` 拉模型并归类
- **默认模型自动落候选**:模型广场配好后,如果设置页没设默认模型,自动按类别提升候选第一个为默认
- 模型类型分类:对话 / 嵌入 / 图像生成 / 视觉理解 / 重排 / 语音合成 / 语音识别 / 视频

### 5.6 本地 OS 适配 + 国产化

详见 [六、支持的操作系统](#六支持的操作系统)。

### 5.7 图转文(Image-to-Text)

> 一键 OCR 提取文字 → TipTap 富文本编辑器 → 多格式导出。专为需要"扫描件转可编辑文档"
> 的政企作者、学生、研究者设计。

- **支持格式**:`.jpg / .png / .webp / .bmp / .tiff` 多图批量
- **OCR 引擎**:RapidOCR-ONNX(默认,纯 CPU,~70 MB,中文 PaddleOCR 转 ONNX),
  独立 Python daemon 起在 18380 端口
- **富文本编辑**:TipTap 框架(StarterKit + Image + Link + Placeholder),
  保留 OCR 段落结构,可校对编辑
- **多格式导出**:Word(`.docx`) / Markdown / 纯文本三选一
- **批量串行**:多张图按上传顺序串行 OCR,避免并发冲击 sidecar
- **进度透明**:每张图独立状态条 + OCR 命中字符数实时回写
- **入口**:主页 → 图转文

**典型场景**:把会议白板拍照、扫描合同、PDF 截图等"非可选中文本"的图像,
一键 OCR + 直接在编辑器里改正错字 + 导成 Word 交付。

### 5.8 音转文(Audio-to-Text)

> 上传录音 → ASR 识别 → 自动分人(A/B/C 三色标签)→ 富文本编辑 → 导出。

- **支持格式**:`.mp3 / .wav / .m4a / .flac / .ogg / .opus` 主流音频格式
- **ASR 引擎**:**FunASR**(默认,阿里 paraformer-zh,中文识别强)+ **CAM++ 说话人分离**;
  若 FunASR 不可用自动回退到 **whisper.cpp**(sidecar 18380)/ **faster-whisper**
- **说话人分离**:CAM++ 嵌入 + 聚类,自动识别多人对话,标 "A / B / C" 三色
- **语言跟随系统**:从 `useSettingsStore.locale` 拉,zh-CN 自动走简体中文 + zhconv 繁→简
- **段落级编辑**:每个说话人段落独立可编辑,改完导出
- **多格式导出**:Word / Markdown / TXT
- **入口**:主页 → 音转文

**典型场景**:会议录音 / 课程录音 / 访谈录音转写,**完全离线**,涉敏感话题也不出域。

### 5.9 安装与首启动行为

针对 "装一次后想换数据目录" / "卸载重装" / "迁移到新机" 三类场景的明确行为契约:

| 场景 | 数据目录 | 用户配置 (`desktop.json`) | 数据本身 | 行为 |
|---|---|---|---|---|
| **第一次安装** | 不存在 | 不存在 | 不存在 | 首启动弹向导,用户选目录,默认建议 `%APPDATA%\chayuan`(Win)/ `~/Library/Application Support/chayuan`(macOS)/ `~/.local/share/chayuan`(Linux) |
| **同一份安装再次启动** | 已选 | 已写入 + `linked_install_id` 匹配当前 exe | 完整 | **直接进主界面**,不再弹向导 |
| **在 Settings 切换数据目录** | 用户在 Settings → 数据目录 → 切换 | 重写并绑定当前 install_id | 旧目录数据**保留不删**,新目录从空开始 / 沿用预存 | 仅重启 sidecar 切到新 `CHAYUAN_ROOT` |
| **卸载后再装(同电脑)** | 数据**保留**在用户目录 | desktop.json 也保留(MSI 不清用户目录) | 完整 | **新装 exe 的 mtime 变 → install_id 变 → 跟旧 desktop.json 对不上 → 重新弹向导**,用户可选"沿用现有数据"或换新目录 |
| **从老机迁移到新机** | 用户手动拷数据目录到新机 | 不存在(新机干净) | 完整 | 首启弹向导,选"指向"那个迁过来的目录,向导自动识别 `data_dir_marker` 显示"沿用现有数据" |

**关键不变式**:数据本身**永远不被卸载 / 重装动作影响**;只有"指向哪个目录"这件事会在重装时要求用户重新确认。
这是有意设计的:防止用户因为某次重装就"丢"了过去的对话历史和 KB,但又确保新装时不静默地"幽灵复用"老路径。

---



---

## 六、支持的操作系统

| 类别 | 系统 | 架构 | 状态 |
|---|---|---|---|
| **Windows** | Windows 10 (1809+) / Windows 11 | x86_64 | ✅ 完整支持(NSIS 安装包 + WebView2 自动安装) |
| **macOS** | macOS 11 (Big Sur) 及更高 | Apple Silicon (arm64) / Intel (x86_64) | ✅ 完整支持(.dmg + 公证就绪) |
| **Linux** | Ubuntu 22.04+ / Debian 12+ | x86_64 / aarch64 | ✅ 完整支持(.deb / .rpm / .AppImage) |
| **国产 Linux** | **麒麟 V10**(Kylin V10) | x86_64 / aarch64 / 龙芯 LoongArch64 | ✅ 兼容(基于 Ubuntu/Debian 共享 webkit2gtk) |
| **国产 Linux** | **统信 UOS**(UnionTech OS) | x86_64 / aarch64 | ✅ 兼容 |
| **国产 Linux** | **openKylin**(开放麒麟) | x86_64 / aarch64 | ✅ 兼容 |
| **国产 Linux** | **deepin** | x86_64 | ✅ 兼容 |
| **国产 Linux** | **银河麒麟服务器版** | x86_64 / aarch64 | ⚠ 需手动装 webkit2gtk-4.1 |
| **国产 Linux** | **欧拉 openEuler** | x86_64 / aarch64 | ⚠ 需 RPM 包(包含在 build 矩阵) |
| **CPU 架构** | x86_64 / aarch64 (arm64) / loongarch64 | — | ✅ 三种架构均有发版 |

### 国产化对接说明

- **签名工具**:支持中国 SM2/SM3/SM4 算法的代码签名(规划中,详见路线图)
- **国产数据库**:已对接 **达梦 DM**、**人大金仓 KingbaseES**、**Apache Doris** —— 详见 [§8.3](#83-数据库连接器结构化数据)
- **国产模型**:已对接 **DeepSeek**、**通义千问 (Qwen)**、**智谱 GLM**、**文心一言**、**Moonshot Kimi**、
  **豆包 (Doubao)**、**SiliconFlow**、**百川**、**MiniMax** —— 详见 [§8.5](#85-模型供应商)
- **国产 OCR**:RapidOCR-ONNX(原 PaddleOCR 模型转 ONNX,纯 CPU 即可跑)
- **国产嵌入**:智源 BAAI/bge-m3-onnx(默认嵌入模型)、bge-reranker-v2-m3(默认重排)

---

## 七、与豆包 / Cherry Studio 等的 60+ 项细致对比

> 以下对比基于 **2026-05** 各家产品**常见公开形态**做归纳,仅作选型参考,**不构成**对任何第三方的功能承诺或排名。
> 计费、数据驻留、企业私有化、监管认证等**以各厂商官方文档与合同为准**。

### 7.1 具名概要对照(横向)

| 对比项 | **察元 AI 桌面单机版** | **豆包 (Doubao)** 桌面 | **Cherry Studio** | **ChatGPT Desktop** | **LM Studio** | **Open WebUI** | **AnythingLLM** | **Chatbox** |
|---|---|---|---|---|---|---|---|---|
| **形态** | Tauri 桌面 + 嵌入式 Python 后端,**全栈本地** | 字节 Doubao 客户端(对接字节云) | Electron 多供应商客户端 | OpenAI 官方桌面(走 OpenAI 云) | Electron 本地推理工作台 | Web UI(配合 Ollama 等本地推理) | Electron + Node 后端 | Tauri 跨平台对话客户端 |
| **后端形态** | **嵌入式 Python sidecar**(单机包) | 字节云 | 各家 API 中转 | OpenAI 云 | 本机 llama.cpp | 独立 Web 服务 | Node + 嵌入式向量库 | 客户端直连厂商 API |
| **离线 LLM** | ✅ Ollama / LM Studio / vLLM / Xinference 一键对接 | ❌ 必须联网 | ✅ 部分支持 | ❌ | ✅ 主打离线 | ✅ 配合 Ollama | ✅ 部分支持 | ✅ 部分支持 |
| **本地知识库**(RAG) | ✅ 五类源统一编排,sqlite-vec 内嵌 | ✅ 但走云端检索 | ✅ 单类(向量) | ❌ | ❌ | ✅ 配合外部 | ✅ 单类(向量) | 部分 |
| **结构化数据库连接(text2sql)** | ✅ 17 种方言:MySQL/PG/Oracle/达梦/金仓/Doris/ClickHouse/Hive… | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **外部向量库连接** | ✅ Milvus/Chroma/ES/Zilliz/PG-vector/Relyt | ❌ | ❌ | ❌ | ❌ | 部分 | 部分 | ❌ |
| **办公私库**(企业/团队/个人三层) | ✅ `office:owner[:group]` 命名空间 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP 协议** | ✅ stdio + sse,服务端 + 客户端双角色 | ❌ | ✅ 客户端 | ❌ | ❌ | 部分 | ❌ | 部分 |
| **内置工具数量** | **30+**(text2sql / web / 图像生成 / 钉钉飞书 / GitHub / arXiv …) | 少量 | 少量 | OpenAI 官方插件 | ❌ | ❌ | 少量 | ❌ |
| **模型对抗(多泳道)** | ✅ N 道无上限 + 统一发送 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **多模态** | ✅ T2I / T2V / TTS / ASR | 部分 | 部分 | 部分 | ❌ | ❌ | ❌ | 部分 |
| **国产化** | ✅ 麒麟/UOS/openKylin + 达梦/金仓/Doris + 国产 LLM | ✅ 字节产品 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **数据落地** | **本机用户目录**,完全本地 | 字节云 | 本机 + 各家云 | OpenAI 云 | 本机 | 自建服务器 | 本机 | 本机 |
| **开源许可** | **AGPL-3.0** | 闭源 | Apache-2.0 | 闭源 | 闭源 | MIT | MIT | GPL-3.0 |
| **可审计源码** | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |

### 7.2 60 项细致对比(察元 AI vs 通用桌面 AI 客户端常态)

> "通用桌面 AI 客户端常态" 指 Cherry Studio / Chatbox / Doubao / Kimi 桌面 / 通义千问桌面 / Open WebUI / LM Studio 等
> 一类产品的常见能力组合,避免对单一产品做绝对结论。

| # | 对比维度 | 察元 AI 桌面单机版 | 通用桌面 AI 客户端常态 |
|---:|---|---|---|
| 1 | 安装包形态 | Tauri 原生窗口 + 嵌入式 Python sidecar | Electron / 浏览器壳 |
| 2 | 安装包体积 | 中(单机版包含完整 Python 运行时与默认模型) | 小(仅前端壳,模型/服务靠外部) |
| 3 | 启动后是否需要外网 | ❌ 不需要 | ✅ 多数需要 |
| 4 | 启动 Splash 动画 | 五层零延迟挂载,首帧即动画 | 普遍有 |
| 5 | 嵌入式向量库 | ✅ sqlite-vec | ❌ 多依赖外部 Milvus / FAISS |
| 6 | 嵌入式嵌入模型 | ✅ ONNX bge-m3(默认) | ❌ 用户自配 |
| 7 | 嵌入式 OCR | ✅ RapidOCR-ONNX | ❌ |
| 8 | 嵌入式 TTS / ASR | ✅ Piper / FunASR(可选) | ❌ |
| 9 | 文档 RAG 完整度 | ✅ PDF/Word/Excel/PPT/Markdown/HTML/图像 | 有限 |
| 10 | 多供应商网关 | ✅ 18+ 家 LLM 厂商 | 5–10 家 |
| 11 | OpenAI 兼容路由 | ✅ 自带 `/openai/v1/*` | 部分 |
| 12 | 模型自动探测 | ✅ 填 Key 自动拉 `/v1/models` | 部分 |
| 13 | 默认模型按类别自动提升候选 | ✅ chat/embed/image/rerank 各自第一个候选 | ❌ |
| 14 | 模型分类 | 对话/嵌入/视觉/图像生成/重排/语音/视频 | 多数仅对话 + 嵌入 |
| 15 | 模型对抗(多泳道) | ✅ 无上限 + 统一发送 | ❌ |
| 16 | 工具调用三层折叠展示 | ✅ summary→args/output→full JSON | ❌ |
| 17 | 流式 reasoning(深度思考)折叠 | ✅ | ❌ |
| 18 | 引用气泡(KB Source) | ✅ 信任度 + 一键回原文 / 下载 | 部分 |
| 19 | 引用按源类型分类 | ✅ doc/struct/vec/office/web | ❌ |
| 20 | text2sql 安全性 | ✅ 只读 AST 校验,白名单表/列 | ❌ |
| 21 | 国产数据库支持 | ✅ 达梦 DM / 金仓 KingbaseES / Doris | ❌ |
| 22 | 国际数据库支持 | ✅ MySQL/PG/Oracle/SQLServer/ClickHouse/Hive/SQLite | 多数 0–1 种 |
| 23 | MongoDB 连接器 | ✅ | ❌ |
| 24 | Elasticsearch 连接器 | ✅ | 部分 |
| 25 | 外部向量库连接器 | ✅ Milvus / Chroma / Zilliz / PG-vector / ES / Relyt | ❌ 或 1 种 |
| 26 | MCP 协议(客户端) | ✅ stdio + sse | 部分 |
| 27 | MCP 协议(服务端) | ✅ 自身可作为 MCP server | ❌ |
| 28 | 内置工具数量 | 30+ | 0–10 |
| 29 | 自定义 HTTP 工具(OpenAPI 导入) | ✅ Swagger 解析 | 部分 |
| 30 | 自定义脚本工具 | ✅ Python REPL / Shell | 部分 |
| 31 | 知识库类型数量 | 5(doc/struct/vec/office/img) | 多数 1 |
| 32 | 文档格式覆盖 | PDF/Word/Excel/PPT/MD/HTML/图像/CSV | 多数 PDF + Word |
| 33 | 增量同步文件夹 | ✅ folder-sync 路由 | 部分 |
| 34 | 自动构建知识结构 | ✅ 文件夹监听 + 解析 + 入库一条龙 | ❌ |
| 35 | KB 多选并联检索 | ✅ `selectedKuIds` 携带多源 | 部分 |
| 36 | 国产 OS 适配 | ✅ 麒麟 / UOS / openKylin / deepin | ❌ |
| 37 | 多架构 | x86_64 / aarch64 / loongarch64 | 多数仅 x86_64 |
| 38 | 数据目录可选 | ✅ FirstRunSetup 向导 | 多数固定 |
| 39 | 鉴权可关 | ✅ 单机模式自动关 | ❌(无概念) |
| 40 | 凭据加密 | ✅ Tauri Stronghold + ChaCha20-Poly1305 | 部分 |
| 41 | API 可被外部调用 | ✅ 暴露 `/api/*`,WPS 加载项可直连 | ❌ |
| 42 | 同机 WPS 加载项联动 | ✅ chayuan-wps v3.0 直连 127.0.0.1 | ❌ |
| 43 | OpenAPI 鉴权(HMAC) | ✅ X-App-Id / X-Sign / X-Timestamp | ❌ |
| 44 | OpenAI SDK 可直接连本机 | ✅ `base_url=http://127.0.0.1:62581/openai/v1` | 部分 |
| 45 | Tab 多对话并行 | ✅ 浏览器式 | 部分 |
| 46 | Tab 拖拽 / 上下文菜单 | ✅ 关闭 / 关闭其他 / 关闭全部 | 部分 |
| 47 | 主题(深 / 浅 / 跟随系统) | ✅ | 多数 |
| 48 | 字号自定义 | ✅ 持久化 | 部分 |
| 49 | i18n 多语言 | ✅ 中 / 英 / 日 / 德 / 法 | 部分 |
| 50 | 帮助中心(内嵌 markdown) | ✅ | 部分 |
| 51 | 反馈通道 | ✅ 内嵌二维码 + GitHub Issue | 部分 |
| 52 | 自动更新 | ⏳ 规划中 | 多数 |
| 53 | 系统托盘 | ✅ tray-icon 插件 | 多数 |
| 54 | 全局快捷键 | ✅ global-shortcut 插件 | 部分 |
| 55 | 桌面通知 | ✅ notification 插件 | 多数 |
| 56 | 文件拖拽上传 | ✅ | 多数 |
| 57 | 剪贴板集成 | ✅ clipboard-manager 插件 | 多数 |
| 58 | 可观测性(Langfuse) | ✅ 可选,默认关 | 部分 |
| 59 | 评估模块(eval) | ✅ KB recall 评估 | ❌ |
| 60 | 审计 / 配额 / RBAC | ✅ governance 模块 | ❌ |
| 61 | PII 脱敏 | ✅ governance/redact | ❌ |
| 62 | 数据血缘 | ✅ lineage 跟踪 | ❌ |
| 63 | 多用户 / 多租户 | ✅(单机版默认关闭,可切换 profile) | ❌ |
| 64 | 开发者级 doctor 自检 | ✅ `chayuan doctor` | 部分 |
| 65 | 开源许可 | **AGPL-3.0** | 各异 |

---

## 八、详细功能清单

### 8.1 对话与流式

- **流式 SSE 输出**:每个 token 立即返回,代码块 / 表格 / 引用同步渲染
- **深度思考 (deep thinking) 折叠**:模型 `<think>` 段落以 collapsible 详情展示,点击展开看推理过程
- **工具调用三层展示**:见 [§5.3](#53-察元-ai-对话chayuan-chat)
- **附件处理**:拖拽 / 粘贴 / 选择文件,自动 OCR / 解析 / 嵌入,作为对话上下文
- **对话本地持久化**:Tauri SQLite 插件,`conversations` + `messages` 双表,upsert 语义
- **编辑 / 重生成 / 分支**:消息气泡级操作,分支不破坏原线
- **统一发送(Unified Send)**:模型对抗下,在任一道输入,文本同时进所有泳道
- **对话历史虚拟滚动**:60+ 消息时切到 windowing,OVERSCAN=6,流畅
- **IME 安全 Enter**:中文 / 日文输入法 composition 期间 Enter 不发送

### 8.2 知识库类型与文档格式

| 知识库类型 | `ku_kind` | 描述 | 索引方式 |
|---|---|---|---|
| **文档库** | `doc` | PDF / Word / Excel / PPT / Markdown / HTML / 图像 | 切片 + 嵌入 + BM25 双索引 |
| **结构化库** | `struct` | SQL / MongoDB / ES 数据库 | schema + sample rows + DDL hints |
| **向量库** | `vec` | 外部 Milvus / Chroma / Zilliz / PG-vector | 直连外部 collection |
| **办公私库** | `office` | 企业 / 团队 / 个人三级私库 | 文档库的命名空间 |
| **图像库** | `img` | 图像 + 图像描述 | CLIP 嵌入 |

**文档格式覆盖**:

| 格式 | 解析器 | OCR 触发 | 备注 |
|---|---|---|---|
| `.pdf` | RapidOCRPDFLoader / pypdf | 扫描件自动 OCR | 全文 + 图片层 |
| `.docx` | RapidOCRDocLoader / python-docx | 内嵌图自动 OCR | 表格保留 |
| `.doc` (老格式) | unstructured / antiword | ✅ | |
| `.xlsx` / `.xls` | openpyxl / xlrd | ❌ | 每 sheet 单独切片 |
| `.csv` | FilteredCSVLoader | ❌ | 列名感知 |
| `.pptx` | RapidOCRPPTLoader | ✅ | 每 slide 切片 |
| `.md` / `.markdown` | Markdown loader | ❌ | 标题层级保留 |
| `.html` / `.htm` | BeautifulSoup + html2text | ❌ | DOM 清洗 |
| `.txt` | PlainTextLoader | ❌ | |
| `.png` / `.jpg` / `.jpeg` / `.bmp` / `.tiff` | RapidOCRLoader | ✅ | |
| `.json` / `.yaml` / `.toml` | 结构化文本 loader | ❌ | |
| `.eml` / `.msg` (邮件) | unstructured.email | ❌ | |
| `.epub` (电子书) | unstructured.epub | ❌ | |

### 8.3 数据库连接器(结构化数据)

通过 `chayuan/server/knowledge_source/sql/dialects.py` 提供 17 种 SQL 方言适配:

#### 国际主流

| 数据库 | 驱动 | 方言 | text2sql 支持 |
|---|---|---|---|
| **MySQL** | pymysql | mysql+pymysql | ✅ |
| **PostgreSQL** | psycopg2 / psycopg | postgresql | ✅ |
| **SQLite** | pysqlite | sqlite | ✅ |
| **Microsoft SQL Server** | pyodbc / pymssql | mssql | ✅ |
| **Oracle** | oracledb / cx_Oracle | oracle | ✅(thin / thick mode 自动) |
| **ClickHouse** | clickhouse-sqlalchemy | clickhouse+http / clickhouse+native | ✅ |
| **Hive / Impala** | PyHive | hive | ✅ |

#### 国产数据库

| 数据库 | 驱动 | 方言 | 备注 |
|---|---|---|---|
| **达梦 DM** | dmPython | dm | sqlalchemy-dm 适配 |
| **人大金仓 KingbaseES** | ksycopg2 / kingbase8 | kingbase | PG 兼容 + 专用方言 |
| **Apache Doris** | pymysql(MySQL 兼容协议) | doris | 端口 9030 |

#### 文档型 / 全文型

| 数据库 | 协议 | 备注 |
|---|---|---|
| **MongoDB** | pymongo | aggregate pipeline 生成 |
| **Elasticsearch** | elasticsearch-py | DSL 生成,支持聚合 |

**安全机制**:
- text2sql 链路强制 **只读 AST 校验**,任何 INSERT / UPDATE / DELETE / DROP / CREATE 在执行前被拦截
- 表名 / 列名走 schema cache 白名单,LLM 输出不在白名单的标识符直接报错
- 聚合类问题(有几个用户 / 总销售额 / TOP 10 …)走 `structured_aggregate` 意图,
  必须命中 `COUNT` / `SUM` / `AVG` 等聚合函数,结果带 SQL 文本 + 行数 + 列定义,人能审计

### 8.4 向量库支持

| 向量库 | 单机适用 | 服务化适用 | 备注 |
|---|---|---|---|
| **sqlite-vec** | ✅ 默认 | ⚠ 单机 | 内嵌扩展,数据落 SQLite 文件 |
| **FAISS** | ✅ | ⚠ 单机进程内 | flat / IVF / HNSW |
| **Milvus** | ❌ | ✅ | standalone / cluster |
| **Milvus Lite** | ✅ | — | in-process 嵌入式 |
| **Chroma** | ✅ | ✅ | persistent / HTTP |
| **Zilliz** | ❌ | ✅ | Milvus 云 |
| **Elasticsearch** | ❌ | ✅ | 8.x 及以上,dense_vector |
| **PostgreSQL + pgvector** | ❌ | ✅ | |
| **Relyt** | ❌ | ✅ | 国产分布式向量 |

### 8.5 模型供应商

通过 `model_platform.platform_type` 字段路由,默认支持的 `platform_type` 以及对应厂商:

#### 云端 LLM(国外)

| 厂商 | platform_type | 备注 |
|---|---|---|
| **OpenAI** | `openai` | 官方 |
| **Anthropic Claude** | `anthropic` | OpenAI SDK 兼容层 |
| **Google Gemini** | `gemini` / `custom openai` | OpenAI 兼容适配器 |
| **Mistral AI** | `mistral` | |
| **Together AI** | `together` | OpenAI 兼容 |
| **Groq** | `groq` | OpenAI 兼容 |

#### 云端 LLM(国内)

| 厂商 | platform_type | 备注 |
|---|---|---|
| **DeepSeek 深度求索** | `deepseek` | OpenAI 兼容 |
| **通义千问 Qwen / 阿里百炼 Dashscope** | `dashscope` / `qwen` | 原生 + OpenAI 兼容 |
| **百度 文心一言 / 千帆** | `qianfan` / `wenxin` | |
| **智谱 GLM / ChatGLM** | `zhipu` / `glm` | 原生 ZhipuAI SDK |
| **Moonshot Kimi** | `moonshot` | OpenAI 兼容 |
| **字节豆包 Doubao** | `doubao` | 火山引擎 OpenAI 兼容 |
| **百川 Baichuan** | `baichuan` | OpenAI 兼容 |
| **MiniMax** | `minimax` | |
| **零一万物 Yi** | `yi` / `lingyiwanwu` | |
| **SiliconFlow** | `siliconflow` | 推理服务 |

#### 本地推理 / OpenAI 兼容自建

| 软件 | platform_type | 备注 |
|---|---|---|
| **Ollama** | `ollama` | 自动从 `/api/tags` 拉模型清单 |
| **LM Studio** | `lmstudio` / `custom openai` | OpenAI 兼容 |
| **Xinference** | `xinference` | 自动探测 |
| **vLLM** | `vllm` / `custom openai` | OpenAI 兼容 |
| **OneAPI / New API** | `oneapi` | API 聚合层 |
| **FastChat** | `fastchat` | OpenAI 兼容 |
| **LocalAI** | `localai` | OpenAI 兼容 |
| **自定义 OpenAI 兼容** | `custom openai` | 任意 base URL |

### 8.6 嵌入模型 / 重排 / OCR

#### 嵌入模型

| 嵌入器 | 类型 | 默认 | 备注 |
|---|---|---|---|
| **bge-m3-onnx**(BAAI) | ONNX 本地 | ✅ 默认 | 中英多语,1024 维,~120 MB |
| **Ollama embedding** | 远程 OpenAI 兼容 | — | `nomic-embed-text` 等 |
| **Infinity** | 远程 OpenAI 兼容 | — | HuggingFace 推理服务 |
| **OpenAI text-embedding-3** | 云端 | — | small / large |
| **Dashscope text-embedding** | 云端 | — | 通义千问 |
| **ZhipuAI embedding** | 云端 | — | embedding-2 / embedding-3 |
| **Jina embedding** | 云端 | — | jina-embeddings-v3 |
| **Cohere embed** | 云端 | — | embed-multilingual-v3 |

#### 重排器(Reranker)

- **BAAI/bge-reranker-v2-m3**(默认推荐)
- **BAAI/bge-reranker-large**
- **任意 sentence_transformers cross-encoder**

#### OCR

- **RapidOCR-ONNX**(默认):纯 CPU,~70 MB,中英 PaddleOCR 模型转 ONNX
- **RapidOCR-Paddle**(可选):GPU 加速版本

### 8.7 多模态

| 模态 | 接口 | 默认实现 | 可选实现 |
|---|---|---|---|
| **T2I 图像生成** | `/api/image/text2image` | OpenAI DALL-E / 火山豆包文生图 | ComfyUI(可选 38188 端口)、SD WebUI |
| **图像理解** | `/api/chat`(vision LLM) | 通义千问 VL / GPT-4V / Claude 3 Vision | |
| **TTS 语音合成** | `/api/voice/tts` | **Piper**(纯 CPU,内置中文音色,~30 MB) | CosyVoice(可选 38280) |
| **ASR 语音识别** | `/api/voice/asr` | FunASR(可选 38180,阿里) | Whisper |
| **T2V 视频生成** | `/api/video/text2video` | 通义千问 Wan / 火山豆包 PixelDance | 各家云端 |

### 8.8 内置工具(30+)

来源:`chayuan/server/agent/tools_factory/`

#### 数据 / 知识

- `search_local_knowledgebase` —— 本地 KB 检索(带 source attribution)
- `search_internet` —— Web 搜索(SearxNG / Tavily / Brave 等)
- `url_reader` —— URL 抓取并提取正文
- `text2sql` —— 任意已注册 SQL 源的自然语言查询
- `text2promql` —— Prometheus 指标查询

#### 代码 / 系统

- `python_repl` —— 沙箱 Python REPL
- `shell` —— Shell 命令(白名单)

#### 学术 / 公开数据

- `arxiv` —— 论文检索
- `pubmed_search` —— 生物医学文献
- `semantic_scholar` —— 学术语义检索
- `wikipedia_search` —— 维基百科
- `stackexchange` —— Stack Exchange 系列
- `wolfram` —— Wolfram Alpha 数学/科学引擎

#### 开发协作

- `github_tool` —— Issue / PR / 代码搜索
- `gitlab_tool` —— GitLab 实例
- `confluence_search` —— Confluence wiki
- `notion_search` —— Notion workspace

#### 即时通讯

- `dingtalk_message` —— 钉钉群机器人
- `wechat_work_message` —— 企业微信
- `lark_message` —— 飞书

#### 实时 / 地理

- `amap_poi_search` —— 高德 POI
- `amap_weather` —— 高德天气
- `openweather` —— OpenWeather
- `news_api` —— 新闻聚合
- `yahoo_finance_news` —— 财经

#### 多模态生成

- `text2image` —— 图像生成
- `search_youtube` —— YouTube 检索

#### 通用

- `calculate` —— 数学表达式
- `http_request` —— 通用 HTTP 客户端(带 auth)
- `openapi_call` —— OpenAPI/Swagger 规范自动调用
- `custom_tools_runtime` —— 用户自定义工具加载

### 8.9 MCP(Model Context Protocol)

- **客户端**:UI 中可注册 stdio 或 sse 形态的 MCP 服务器,工具自动出现在 Composer 的 MCP 多选 picker 中
- **服务端**:察元后端自身可作为 MCP server 暴露内置工具,通过 stdio 接入 Cursor / Claude Desktop / 其它 MCP 客户端

### 8.10 模型对抗(Model Arena)

> 一个对话页内开 N 个独立泳道,每道选不同的模型,**统一发送**一句话,横向对比生成质量、风格、速度、成本。

- **泳道操作**:折叠 / 调宽 / 拖动重排 / 添加 / 删除
- **统一发送 (unified send)**:checkbox 打开后,任一道输入会 broadcast 到所有泳道
- **每道独立 conversationId**:历史互不干扰
- **持久化布局**:zustand persist + per-tab scope,关 tab 不丢
- **折叠条标题**:取该泳道**首条用户提问**作为竖向标签(`writing-mode: vertical-rl + text-orientation: upright`),
  没问话时显示 "无标题"
- **拖出独立窗口**:暂未启用(架构重构中,详见 PACKAGING.md 路线图)

### 8.11 自动构建文档知识结构(Folder Sync)

> 把一个文件夹"挂载"到知识库,文件夹内任何变化(新增 / 修改 / 删除)都会自动:
> 解析 → OCR(如需) → 切片 → 嵌入 → 入库 → 更新引用元数据。

- **路由**:`/api/folder-sync/*`
- **挂载方式**:用户在 KB 详情页选 "从文件夹同步",填本地路径
- **变更检测**:基于文件 mtime + size + hash,启动时增量扫描,运行时 watchdog
- **失败可恢复**:解析失败的文件标记为 quarantine,不阻塞其它文件
- **一键重建**:右键文件夹 → 重新索引,清掉 KB 中该文件夹源的全部 chunks 重跑

### 8.12 引用展示与原文回链

每条 LLM 回复下方挂 **Citation Strip** —— 信任度星级 + 来源类型图标 + 一键操作:

| 来源类型 | 图标 | 操作 |
|---|---|---|
| **`document`** 文档库 | 📄 | 打开原文预览 + 下载附件 |
| **`structured`** 结构化(SQL) | 🗃 | 查看生成的 SQL + 表格结果 + 行数 |
| **`vector`** 外部向量 | 📚 | 显示 collection / vector_id / metadata,**不提供下载** |
| **`office`** 办公私库 | 🏢 | 显示企业/团队/个人归属 + 文档名 |
| **`web`** Web 搜索 | 🌐 | 打开外链 |

---

## 九、HTTP API 接口总览

> 后端默认监听 `127.0.0.1:62581`(单机模式),完整 OpenAPI swagger 在
> `http://127.0.0.1:62581/docs` 在线查看。

### 9.1 主要路由组

| 路由前缀 | 用途 | 文件 |
|---|---|---|
| `/healthz` | 健康检查 | `health_routes.py` |
| `/api/chat/*` | 对话(流式 / 同步、KB 模式、多源) | `chat_routes.py` |
| `/api/file-chat/*` | 文件级对话(上传 + Q&A) | `chat_routes.py` |
| `/api/v1/kb-query/*` | **统一知识查询**(推荐入口) | `kb_query_routes.py` |
| `/api/kb/*` | KB CRUD(创建 / 上传 / 搜索 / 同步) | `kb_routes.py` |
| `/api/kb-collection/*` | Collection 分组(实验) | `kb_collection_routes.py` |
| `/api/knowledge-source/*` | 外部源注册(SQL / Mongo / ES / Vector) | `knowledge_source_routes.py` |
| `/api/knowledge-universe/*` | 多源编排 + 联邦查询 | `knowledge_universe_routes.py` |
| `/api/folder-sync/*` | 文件夹自动同步 | `folder_sync_routes.py` |
| `/api/data-mount/*` | 数据挂载管理 | `data_mount_routes.py` |
| `/api/storage/*` | 文件上传下载 / 预签名 URL | `storage_routes.py` |
| `/api/image/*` | 图像模型(T2I / 图像 embedding) | `image_routes.py` |
| `/api/voice/*` | TTS / ASR | `voice_routes.py` |
| `/api/video/*` | 视频生成 | `video_routes.py` |
| `/api/tool/*` | 工具执行 / 注册 / 自定义 | `tool_routes.py` |
| `/api/mcp/*` | MCP 生命周期 | `mcp_routes.py` |
| `/api/governance/*` | 审计 / 配额 / PII / 策略 | `governance_routes.py` |
| `/api/annotation/*` | 数据标注 / 反馈 | `annotation_routes.py` |
| `/api/admin/*` | 配置 / 用户 / 配额 / 平台覆盖 | `admin_routes.py` |
| `/api/runtime/*` | 服务状态 / 配置热加载 | `runtime_routes.py` |
| `/api/provider/*` | LLM 平台 catalog / 模型清单 / 连通测试 | `provider_routes.py` |
| `/api/auth/*` | 登录 / 刷新(单机模式禁用) | `auth_routes.py` |
| `/openai/v1/*` | **OpenAI 兼容端点**(chat / embedding / image) | `openai_routes.py` |
| `/openapi/v1/*` | 自定义应用鉴权(HMAC) | `openapi_routes.py` |
| `/openapi/ws/*` | WebSocket 应用事件 | `openapi_ws.py` |

### 9.2 OpenAI SDK 兼容(直连本机)

```python
# Python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:62581/openai/v1",
    api_key="anything",   # 单机模式不校验 token
)

resp = client.chat.completions.create(
    model="qwen2.5:7b",   # 任何已在模型广场配置的模型
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content, end="", flush=True)
```

### 9.3 统一知识查询(推荐用法)

```http
POST /api/v1/kb-query/search
Content-Type: application/json

{
  "ku_ids": [
    "doc:产品文档",
    "src:user_db",
    "vec:milvus_research"
  ],
  "query": "上个月销售额最高的三个产品是什么?",
  "top_k": 5
}
```

返回:
```json
{
  "blocks": [
    {
      "ku_id": "src:user_db",
      "kind": "structured",
      "hits": [
        {
          "content": "...",
          "citation": {
            "source_kind": "structured",
            "title": "user_db.orders",
            "generated_query": "SELECT product_id, SUM(amount) ... GROUP BY ... LIMIT 3",
            "rows": 3
          }
        }
      ]
    }
  ],
  "diagnostics": [...]
}
```

### 9.4 OpenAPI HMAC 鉴权(给应用接入)

WPS 加载项等外部应用以 **应用 ID + 共享 Secret** 鉴权,每次请求带:

```
X-App-Id:     <app_id>
X-Timestamp:  <unix_ms>
X-Sign:       <HMAC-SHA256(secret, "{app_id}\n{timestamp}\n{path}\n{body_md5}")>
```

详见 [`chayuan-wps/src/services/kb/authClient.js`](https://github.com/zhgyuhuii/chayuan/blob/main/src/services/kb/authClient.js) 的实现。

---

## 十、开发者搭建指南

完整的端到端打包指南在 **[PACKAGING.md](PACKAGING.md)**,这里给开发循环的速通版。

### 10.1 前置环境

| 工具 | 版本 | 用途 |
|---|---|---|
| Git | ≥ 2.30 | |
| Python | **3.12.x** | 后端 |
| Poetry | ≥ 1.8 | Python 依赖 |
| Node.js | **22.x** | 前端 |
| pnpm | **9.x** | 前端依赖 |
| Rust | stable | Tauri 编译 |

### 10.2 本地开发(不打包)

```bash
# 终端 1:启动后端(单机模式)
cd chayuan-server
poetry install
poetry run chayuan start -a --single-machine

# 终端 2:启动 Tauri dev
cd chayuan-client
pnpm install
pnpm dev:desktop
```

### 10.3 模型与服务下载

下载有**两条路径,目标目录不同,别混**:

| 场景 | 工具 | 落点 |
|---|---|---|
| 应用内下载(终端用户) | 应用里的「下载模型」对话框 | `<CHAYUAN_ROOT>/models/bundled/<cap>/` |
| 打包前备料(开发者) | `scripts/install-vendor.*` | `chayuan-server/vendor/` |

`<CHAYUAN_ROOT>` 默认:Windows `%APPDATA%\chayuan`、macOS `~/Library/Application Support/chayuan`、Linux `~/.local/share/chayuan`。

#### A. 应用内下载模型(终端用户用)

打开应用 → 模型广场 / 设置里的「下载模型」对话框:

- 粘贴 `model_id`(形如 `owner/name`),选下载源:**ModelScope(国内快)** 或 HuggingFace(走 hf-mirror)
- 重排模型已预填 5 个候选(bge-reranker-v2-m3 / gte-multilingual-reranker-base / jina-reranker-v2 / bge-reranker-base / ms-marco-MiniLM),点示例一键填好
- 下载落到 `<CHAYUAN_ROOT>/models/bundled/<cap>/<model>/`,下完自动扫描挂载
- 也可手动把模型文件放进该目录,再点「扫描挂载」。**一个模型一个子文件夹**,多个子文件夹会全部加载

#### B. 打包前下载 vendor 资源(开发者 / 打包用)

一站式脚本,把打全量包要塞进 `vendor/` 的东西一次装齐:

```powershell
# Windows
.\scripts\install-vendor.ps1 -Flavor full
```
```bash
# Linux / macOS
bash scripts/install-vendor.sh --flavor full
```

下三类资源到 `chayuan-server/vendor/`:

| 资源 | 落点 | 说明 |
|---|---|---|
| 模型 | `vendor/bundled_models/<cap>/` | 平台无关,一次全平台通用 |
| 服务二进制 | `vendor/services/<engine>/<platform>/` | llama-server / whisper-server |
| torch wheels | `vendor/torch_wheels/<platform_py>/` | 离线装 torch 兜底 |

**墙内网络**:

- `github.com` 不通时,脚本**自动探测可用镜像**(内置 `gh-proxy.com` / `ghfast.top`);也可手动指定:
  `.\install-vendor.ps1 -GithubMirror https://gh-proxy.com/` / `--github-mirror https://gh-proxy.com/`
- 模型走国内源:加 `-Source modelscope` / `--source modelscope`
- 注:老的 `ghproxy.com` 已废弃,用 `gh-proxy.com` 或 `ghfast.top`

**多平台 llama-server**:`install-vendor.ps1` 默认下全 5 个平台

```powershell
.\scripts\install-vendor.ps1 -Flavor full -Targets win-x64,win-arm64,linux-x64,macos-x64,macos-arm64
```

whisper-server 没有预编译,只能在目标机上 brew/docker/源码编译 —— 非本机平台需到对应机器跑 `install-whisper-server.sh`;llama-server 的 `linux-arm64` upstream 没发 zip,同样需自行处理。

**单独装某一项**:

```bash
# 只装模型(--lite 瘦身集 / 不带 = 全量;--only 单 cap)
python scripts/install-bundled-models.py --source modelscope --only chat

# 只装某平台的 llama-server
.\scripts\install-llama-server.ps1 -Target win-arm64       # Windows
bash scripts/install-llama-server.sh b9174 --target linux-x64   # Linux/macOS

# 只装 whisper-server(本机平台)
.\scripts\install-whisper-server.ps1
bash scripts/install-whisper-server.sh
```

#### C. 校验

```bash
python scripts/check-bundled-models.py          # 模型完整性,能装上几类
python scripts/check-bundled-models.py --json   # 给 CI / 脚本消费
python scripts/check-services.py --target win-x64   # 服务二进制完整性

# 排查"扫描挂载扫不到 / 服务起不来"(Windows)
.\scripts\diagnose-models.ps1
```

> 模型"下载地址 / 格式 / 打包嵌入 / 调试自检"等更细的指南,
> 见 **[chayuan-server/vendor/bundled_models/README.md](chayuan-server/vendor/bundled_models/README.md)**。
> CI 三平台流水线按 layout.yaml release 批量拉模型,详见
> **[PACKAGING.md](PACKAGING.md)**。

### 10.4 本地打包(默认双产物:轻量版 + 集成版)

`build-desktop.{sh,cmd,ps1}` 一次跑出两个产物:

* **轻量版 (lite)**:不带模型。装机包小(< 1 GB),首次启动靠 BootstrapBanner
  引导用户在线下载或扫盘配置。
* **集成版 (integrated)**:`vendor/bundled_models/` 整树打进 **Tauri resources**
  (与主 exe 同目录,**不嵌入 sidecar 内**),用户**装好即用**,无需联网下载。
  本次嵌入哪些模型,构建开始时的"模型体检"段会逐项打印。

> 2026-05-15 起两个 flavor **共用同一份 sidecar exe**:模型从 PyInstaller datas
> 剥到 Tauri `bundle.resources`,解决了集成版 sidecar ≥ 2 GB 撞 32-bit makensis
> mmap 上限的问题。flavor 差异只剩 Tauri resources 同步那一步(`build.py
> --sync-bundled-only`),sidecar 构建一次两边复用。
>
> **Windows installer 两道独立的 2 GB 限制**:
> 1. **单文件 < 2 GB** — NSIS makensis 32-bit mmap;WiX 3 light.exe LGHT0263
>    硬编码 INT32_MAX。
> 2. **NSIS 总 payload 压缩后 < 2 GB** — 仅 NSIS 限制,LZMA solid 32-bit 寻址。
>    LITE_CAPS (~990 MB) + sidecar (~500 MB) + services 总 raw ~1.5 GB 就会撞。
>
> **2026-05-18 起 Windows 一律走 WiX MSI**(lite 也切了,不再用 NSIS),
> 单文件 ≥ 2 GB 的大模型(如 bge-m3 的 .bin / .onnx_data ~2 GB+)
> 由 `sync_bundled_models` 按 2 GB 阈值分流:小文件嵌 MSI,大文件落
> `chayuan-server/dist/models_seed/`,通过 ISO **旁挂**(不进 MSI)给用户。
>
> **ISO 单文件分发**:
> - `.msi + .cab + models_seed/` 打成 `chayuan-desktop_{lite,full}_setup.iso`
>   (Windows 10+ 双击 ISO 自动挂载成 `CHAYUAN_{LITE,FULL}` 虚拟光驱)
> - 用户进光驱目录双击 MSI 即装,MSI 在光驱根能找到外置 CAB(`EmbedCab="no"` 绕国产杀软)
> - 装包后**保持光驱挂载** —— chayuan-server 首次启动会扫挂载光驱的 `models_seed/`,
>   把 ≥ 2 GB 大文件种到 `<CHAYUAN_ROOT>/models/bundled/`;复制完成才能正常对话
> - 若用户提前弹了光驱,UI 显示 "external_pending,请重挂 ISO 后重启",用户重挂 + 重启即可
>
> **冗余权重剪枝**:同 model 目录已有 `.gguf` / `.safetensors` 时,
> `.bin` / `.onnx_data` / `.pt` / `.pth` 自动跳过(老版 install 或手动 HF snapshot 残留,装机不需要),
> 省 ISO 体积。
>
> 一键换装瘦身默认集:`.\scripts\install-bundled-models.ps1 -Source modelscope -Clean`
> 或 `python scripts/install-bundled-models.py --source modelscope --clean` — chat /
> embedding / rerank / asr 全部 < 2 GB,自带 ModelScope fallback 绕 hf-mirror Xet 大文件坑。

产物各自落到:

```
dist-lite/          ← 轻量版 .dmg / .deb / .AppImage / .msi / .exe
dist-integrated/    ← 集成版 .dmg / .deb / .AppImage / .msi / .exe
```

#### Windows

打包入口固定是 `.\build-desktop.ps1`(或 `.\build-desktop.cmd`,内部转发到 ps1)。
三类用例按需选:

##### 用例 A:一次打 **lite + full** 双产物(完整发布)

```powershell
# 完整双产物 — 第一次构建 / release 切版本前用,约 30 分钟
.\build-desktop.ps1
```

不传任何 `-LiteOnly` / `-FullOnly` 时,脚本会按 lite → full 顺序各跑一轮完整流程:
PyInstaller sidecar 编译一次两个 flavor 复用,只有 Step 1b(`sync_bundled_models`)
按 flavor 各做一次 — lite 只同步 LITE_CAPS(embedding/rerank/asr/ocr/tts),
full 加上 chat/image。

产物分别落 `dist-lite\` 和 `dist-full\`(均含 `.msi + cab*.cab + models_seed/`
+ 整包 ISO)。

##### 用例 B:只打 **lite** 或只打 **full**(本机迭代 / 单端发布)

```powershell
# 只出轻量版 — 本机试用首选,约 5-10 分钟
.\build-desktop.ps1 -LiteOnly

# 只出全量版 — 集成版发布前验证用
.\build-desktop.ps1 -FullOnly
```

> `-FullOnly` 的旧名 `-IntegratedOnly` 仍可用,会打 deprecation hint,建议改新名。

##### 用例 C:**分段打包**(迭代时复用上次产物,省 80-90% 时间)

按"这次改了什么"挑对应跳过开关 — 跳过的步骤直接用上次产物,不重做。

| 改的是什么 | 命令 | 耗时 | 说明 |
|---|---|---|---|
| **只改了 Rust / installer.nsh / WiX 模板** | `.\build-desktop.ps1 -LiteOnly -BundleOnly` | 1-3 min | 等价 `pnpm exec tauri build --bundles msi`,**最快**重打 |
| **只改了前端 React 代码** | `.\build-desktop.ps1 -LiteOnly -SkipServer` | 3-5 min | sidecar 不重编,跑 typecheck + vite + tauri bundle |
| **只改了 build.py 同步逻辑**(`sync_services` / `sync_bundled_models`) | `.\build-desktop.ps1 -LiteOnly -SkipServer -SkipHealthcheck` | 3-5 min | sidecar 复用,但 Step 1b/1c 重跑,把同步改动应用进 bundle |
| **改了 Python 后端**(`.py` 文件) | `.\build-desktop.ps1 -LiteOnly` | 5-10 min | PyInstaller 指纹自动失效,完整重编 — **不需要手动加任何 -Force** |
| **怀疑 PyInstaller 缓存过期**(改了 venv / 第三方包但没动 .py) | `.\build-desktop.ps1 -LiteOnly -ForceRebuildServer` | 5-10 min | 强制重编 sidecar,常规改 `.py` 不需要 |
| **想干净重建**(排查打包问题时) | `.\build-desktop.ps1 -LiteOnly -Clean -VerboseSubprocess` | 8-15 min | 删 `dist/` + `bundle/`,顺带清掉 PyInstaller 指纹 |
| **本机调试,不需要 ISO** | `.\build-desktop.ps1 -LiteOnly -NoIso` | -2 min | 只出 `.msi + cab`,不打 `.iso` |
| **本机调试,跳类型检查** | `.\build-desktop.ps1 -LiteOnly -SkipTypecheck` | -30s ~ -1 min | **业务代码改动不要跳** — 只在调试 build 流程时用 |

**分段打包注意事项:**

1. **`-BundleOnly` 不能切 flavor** — 它跳过 Step 1b(`src-tauri/bundled_models/`
   同步),用的是上次 flavor 的 bundled_models。如果上次 `-LiteOnly`、这次想换
   `-FullOnly`,必须先跑一次不带 `-BundleOnly` 的完整流让 Step 1b 重做,然后才能
   `-BundleOnly` 迭代。

2. **改了 build.py 同步逻辑别用 `-BundleOnly`** — `-BundleOnly` 也跳 Step 1b。
   要用 `-SkipServer -SkipHealthcheck` 这种组合,sidecar 复用但 Step 1b/1c 仍跑。

3. **跨 flavor 串行用 `;` 而不是 `&&`** — PowerShell 没有 `&&`(PS 7+ 才有);
   要一次跑两个独立 flavor 命令用 `;`:
   ```powershell
   .\build-desktop.ps1 -LiteOnly; .\build-desktop.ps1 -FullOnly
   ```
   但不推荐 — 直接不传 `-*Only` 让脚本内置双循环更省时间(sidecar 复用)。

4. **MSI/cab 一致性自动校验** — 2026-05-19 起脚本在 Tauri bundle 收尾会自动跑
   `Test-MsiCabIntegrity`:用 `WindowsInstaller` COM 读 `File`/`Media` 表 + `expand -D`
   列 cab stream,交叉对一遍。若 File 表里某条 File ID 跟对应 cab 内的 stream 错位
   (装机时必报 1334"找不到 cab1.cab"),脚本会 fail 并打印前 10 条错位明细 —
   **坏包不会进 ISO,也不会到用户手里**。无需手动跑,无需关闭。

#### macOS / Linux

```bash
# 默认 = 双产物
./build-desktop.sh

# 只打一种
./build-desktop.sh --lite-only
./build-desktop.sh --integrated-only

# 跳过部分阶段
./build-desktop.sh --skip-server       # 同 -SkipServer
./build-desktop.sh --bundle-only       # 同 -BundleOnly
./build-desktop.sh --skip-model-check  # 跳模型体检
./build-desktop.sh --verbose           # 看完整 stdout
```

#### 构建命令在做什么

每次构建按顺序跑:

1. **模型体检** — 调 `scripts/check-bundled-models.py`,打印 7 类 capability
   各自有几个文件 / 体积合计多大,集成版会随 Tauri resources 一起装机。
2. **双 flavor 循环**(默认):每个 flavor 各跑一次以下 4 步 —
   - **Step 1** PyInstaller 出 sidecar(两个 flavor 共用,**模型不嵌 sidecar**)
   - **Step 1b** `build.py --sync-bundled-only` 按 flavor 同步
     `src-tauri/bundled_models/`(LITE_CAPS = embedding/rerank/asr/ocr/tts;
     FULL_CAPS 再加 chat/image)+ `src-tauri/services/`(`llama-server` +
     `whisper-server` binary,lite/full 都打 — 老版本 lite 清空 services
     导致服务起不来,2026-05-18 修)
   - **Step 1c** 同步 `scripts/diagnose-auto-start.ps1` → `src-tauri/tools/`,
     installer 装机后落到 `<install>/resources/tools/diagnose-auto-start.ps1`,
     用户原地可跑做故障排查(见 §10.8)
   - **Step 2** `/healthz` 探测确认 sidecar 能起来
   - **Step 3** Tauri bundle 出 `.dmg / .deb / .AppImage / .msi / .exe`
     并按 flavor 后缀拷到 `dist-<flavor>/`
3. **本次产物清单** — 总结表列出 `flavor / 文件名 / 大小`。

### 10.5 三平台 CI

GitHub Actions 矩阵 5 个 runner:`macos-13` / `macos-14` / `ubuntu-22.04` / `ubuntu-24.04-arm` / `windows-2022`,
触发条件 + 产物下载 详见 [`chayuan-client/.github/workflows/build-desktop.yml`](chayuan-client/.github/workflows/build-desktop.yml)。

### 10.6 项目结构

```
/chayuan-desktop/
├── README.md                       ← 本文件
├── PACKAGING.md                    ← 详细打包流程
├── LICENSE                         ← AGPL-3.0
├── build-desktop.{cmd,ps1,sh}      ← 一键打包入口(默认双产物:轻量版+集成版)
├── dist-lite/                      ← 轻量版构建产物(运行后生成)
├── dist-integrated/                ← 集成版构建产物(运行后生成)
├── scripts/
│   └── check-bundled-models.py     ← 打包前的模型体检脚本
├── chayuan-server/                 ← Python 后端
│   ├── libs/chayuan-server/
│   │   └── chayuan/server/
│   │       ├── api_server/         ← FastAPI 路由
│   │       ├── chat/               ← 聊天链路
│   │       ├── knowledge_base/     ← 文档 KB(切片 / 嵌入)
│   │       ├── knowledge_source/   ← 结构化 / 向量 / 办公源
│   │       ├── retrieval/          ← 统一检索 orchestrator
│   │       ├── kb_query/           ← 统一查询 service 层
│   │       ├── agent/tools_factory ← 30+ 内置工具
│   │       ├── mcp_server/         ← MCP 服务端
│   │       ├── ai_platform/        ← 模型平台网关
│   │       ├── governance/         ← 审计 / 配额 / RBAC
│   │       └── profiles/single_machine.py
│   ├── vendor/bundled_models/      ← 项目内固定模型槽(详见同目录 README.md)
│   └── packaging/pyinstaller/      ← PyInstaller spec + build.py
└── chayuan-client/                 ← Tauri 前端
    ├── apps/desktop/               ← Tauri 主入口
    │   ├── src-tauri/
    │   │   ├── tauri.conf.json
    │   │   ├── installer.nsh       ← NSIS 中文图标 hook
    │   │   └── src/                ← Rust 主进程(sidecar / data_dir)
    │   └── src/                    ← React 入口(main.tsx / splash.ts)
    └── packages/
        ├── app/                    ← 业务页面与 store
        ├── api/                    ← 后端 API 客户端
        ├── ui/                     ← 设计系统组件
        ├── transport/              ← 聊天 transport
        ├── platform-tauri/         ← Tauri 适配层
        └── platform-web/           ← Web 适配层
```

### 10.7 验证清单(release 前必跑)

```bash
# 后端测试
cd chayuan-server
PYTHONPATH=libs/chayuan-server pytest -q libs/chayuan-server/tests/unit_tests/

# 前端类型检查
cd ../chayuan-client
pnpm typecheck
```

详见 [PACKAGING.md §8](PACKAGING.md)。

### 10.8 装完后诊断与故障排查

> **典型问题**:装完启动 app,本地模型服务(chat/embedding/rerank/asr/image-embedding)
> 全部 `state=stopped` 或 `state=failed`,知识库检索 / 对话拿不到本地推理。

#### 一键诊断脚本

`scripts/diagnose-auto-start.ps1` 自动收齐 8 个 section 的关键信息:安装包版本、
bundled_models 布局、`sidecar_settings.json` / `local_runtime.yaml` 内容、5 个
capability HTTP 状态、最新日志 tail、(可选)直接命令行重启 sidecar 抓完整
`[bootstrap-preload]` / `[auto-start]` stdout 链路。

**两种用法**:

**(A) 开发机有 git checkout** — 直接从仓库跑:

```powershell
cd D:\code\chayuan\chayuan-desktop
.\scripts\diagnose-auto-start.ps1 -OutFile diag.md                  # 基础诊断
.\scripts\diagnose-auto-start.ps1 -CaptureStdout -OutFile diag.md   # 加抓 30 秒 sidecar stdout
```

**(B) 已装用户没 git** — 脚本随 installer 一起装到了
`<install>\resources\tools\diagnose-auto-start.ps1`:

```powershell
cd "$env:LOCALAPPDATA\Programs\chayuan-desktop"
$diag = Get-ChildItem -Path . -Recurse -Filter 'diagnose-auto-start.ps1' | Select-Object -First 1
powershell -ExecutionPolicy Bypass -File $diag.FullName -OutFile $HOME\Desktop\diag.md
# 完整链路(会临时杀掉 sidecar 重启 30 秒,看 [auto-start] state=ready / failed)
powershell -ExecutionPolicy Bypass -File $diag.FullName -CaptureStdout -OutFile $HOME\Desktop\diag.md
```

#### 自启链路概览(出问题时按这条链路对照)

```text
chayuan-server.exe 启动
  ↓
[startup] _model_first_launch
  ├── seed_bundled_models()       — <install>/bundled_models/ → <data>/models/bundled/
  ├── bootstrap_preload_from_bundled()
  │   ├── 检测每个 cap 是否有 bundled 内容(src OR dst,二者任一即 True)
  │   └── 翻 LocalRuntimeSettings.preload_*=True + auto_start.<key>=True + mark_bootstrapped
  ├── scan_once()                 — 索引 <data>/models/bundled/ 内的模型
  ├── promote_defaults_from_local() — 把 candidate 写进 panel yaml 作 default
  └── (step5 preload_map 异步 create_task,作为 redundant 路径)
  ↓
[startup] _auto_start_rapidocr    — 拉起 RapidOCR sidecar (auto_start.rapidocr=True)
  ↓
[startup] _auto_start_capabilities
  ├── 兜底再跑一次 bootstrap_preload_from_bundled(防 _model_first_launch 失败)
  └── for cap in (chat/embedding/rerank/asr/image-embedding):
        if auto_start_store.get(cap) == True:
            await reg.get(cap).start()
              ↓
              find_server_exe()    — 找 <install>/services/llama-server/llama-server.exe
                                     (或 whisper-server.exe / Python infinity sidecar)
              _resolve_args_for()  — 读 panel default_<cap>_model
              spawn subprocess + /health 探测 → state=ready
```

#### 常见症状 → 修复路径

| 症状 | 根因 | 修复 |
|---|---|---|
| `services/` 目录空 | lite 老版本(2026-05-18 前)build.py 把 lite services 清空 | `git pull` 拿 `f3c51d9`,重 build lite installer |
| `state=stopped` 持续不动 | `_auto_start_capabilities` 没跑 / 没翻 auto_start | 跑诊断 `-CaptureStdout`,看有没有 `[auto-start]` stdout 行 |
| `state=failed` + `last_error="llama-server.exe 不在..."` | services 漏打了 launcher binary | 同上,重 build |
| `state=failed` + `last_error="模型未就绪"` | bundled 模型在但 `promote_defaults` 没指派 default | 进 UI 设置 → AI 平台 → 本地运行时,手动选模型;或重 build 让 scan + promote 重跑 |
| UI 看不到 sidecar stdout | Tauri 只 emit `cy:sidecar-log` 事件给前端 banner,不落盘 | 用 `-CaptureStdout` 直跑 sidecar,或看 `<data>/logs/*.log` |

#### `_auto_start_capabilities` 自启 hook 输出范例

服务正常起来时 sidecar stdout 会有类似输出(2026-05-18 后版本):

```text
[bootstrap-preload] checked=['chat','embedding','rerank','asr','image','ocr'] bootstrapped=['embedding','rerank','asr','ocr'] updates=['preload_embedding','preload_rerank','preload_asr']
[auto-start-rapidocr] settings=...sidecar_settings.json exists=True
[auto-start-rapidocr] hook 触发 auto_start=rapidocr → True
[auto-start-rapidocr] 完成 state=ready ...
[auto-start] 兜底 bootstrap 翻好:[]    (上面已经翻过了,这次 no-op)
[auto-start] embedding: auto_start=True,拉起中...
[auto-start] embedding: state=ready pid=22168 endpoint=http://127.0.0.1:62583 last_error=None
[auto-start] rerank: state=ready pid=22192 endpoint=http://127.0.0.1:62584 ...
[auto-start] asr: state=ready pid=22216 endpoint=http://127.0.0.1:62585 ...
[auto-start] image-embedding: auto_start=False,跳过   (lite 没打,正常)
[auto-start] chat: auto_start=False,跳过              (lite 没打,正常)
```

任何一行缺失或卡在 `state=failed` 就把 `last_error` 字段拿出来对照上面"常见症状"表。

---

## 十一、使用教程入口

| 入口 | 内容 |
|---|---|
| **应用内 帮助中心** | 启动后点击侧边栏 → 帮助中心 → 内嵌 markdown(getting-started.md) |
| **应用内 关于页** | 设置 → 关于 → 版本 / 出品方 / 官网 / 微信二维码 |
| **应用内 反馈** | 设置 → 反馈 → 内嵌支付/关注二维码 + GitHub Issue 链接 |
| **官网** | <https://aidooo.com> |
| **微信公众号** | 智灵鸟科技 |
| **WPS 加载项教程** | <https://github.com/zhgyuhuii/chayuan/blob/main/README.md> |
| **打包技术文档** | [PACKAGING.md](PACKAGING.md) |
| **CLAUDE.md** | 给 AI 助手 / 二开开发者看的总架构与任务清单 |

---

## 十二、安全 · 隐私 · 离线

### 12.1 数据驻留

- **所有用户数据落到 `CHAYUAN_ROOT`**(用户首启动选定的目录,默认平台标准目录):
  - macOS: `~/Library/Application Support/chayuan`
  - Windows: `%APPDATA%\chayuan`
  - Linux: `~/.local/share/chayuan`
- 包括:对话 SQLite / KB 向量索引 / 上传文件 / 审计日志 / 模型权重缓存
- **不上传任何数据到察元服务器**;遥测(Langfuse)默认关闭,需用户在设置里勾开

### 12.2 凭据安全

- 模型 API Key 等敏感字段以 **Tauri Stronghold** 保险箱加密落盘:
  - 加密算法:**ChaCha20-Poly1305**
  - 密钥派生:**Argon2id**
- 密钥永不进对话日志,永不进 Langfuse trace

### 12.3 网络出口

- **完全离线场景**(用户全装本地模型):**0 外部网络出口**
- **混合场景**(用户配了云端模型):仅模型 API 端点出网,域名白名单可在企业版限制
- 应用更新检查:可在设置里关闭

### 12.4 审计

- `governance/` 模块提供 **审计日志 / PII 脱敏 / 数据血缘** 三件套
- 单机模式默认启用本地审计文件,内网部署可对接 SIEM

---

## 十三、路线图

| 阶段 | 状态 | 内容 |
|---|---|---|
| Phase 1 | ✅ | Tauri 首启动数据目录向导 |
| Phase 2 | ✅ | PyInstaller spec + build.py |
| Phase 3 | ✅ | Tauri sidecar wiring(spawn / health / kill) |
| Phase 4 | ✅ | sqlite-vec 本地向量库 |
| Phase 5 | ✅ | 单机 profile bootstrap |
| Phase 5.x | ✅ | SqliteVecKBService 适配器 |
| Phase 6 | ✅ | 三平台 CI YAML(5-runner 矩阵) |
| Phase 7 | ✅ | 单机 UX 收尾(隐藏登录 / 切换数据目录向导) |
| Phase 7.5 | ✅ | **图转文 / 音转文** 一键 OCR / ASR → 富文本编辑 → 多格式导出 |
| Phase 7.6 | ✅ | **图像 KB** + CLIP 跨模态搜索(以文字 / 以图搜图) |
| Phase 7.7 | ✅ | **统一 KB 查询路径** `kb_query/orchestrator/adapters/results` 分层 |
| Phase 7.8 | ✅ | **Windows ISO 单文件分发** + 大模型旁挂 + 首启自动种子 |
| Phase 7.9 | ✅ | **install_id 检测**:卸载重装自动弹首启向导,不再幽灵复用老路径 |
| Phase 7.10 | ✅ | bare `confirm()` 全仓 sweep → `platform.dialog.confirm` wrapper,修 Tauri 2 ACL 不稳 |
| Phase 7.11 | ✅ | offline torch wheel rglob 修 — 装机后 PyTorch 离线安装链路真正可用 |
| **Phase 8** | ⏳ | **服务端即真源 + SSE 多播架构**(让 chayuan-wps 与桌面客户端共享同一份对话流) |
| Phase 5.y | ⏳ | Redis → cachetools 缓存层 |
| Phase 5.z | ⏳ | Celery → asyncio.Queue 队列层 |
| Phase 6.x | ⏳ | macOS notarize / Windows EV 签名 / SM2 国密 / Linux ARM runners / GH Release 自动上传 |
| Phase 7.x | ⏳ | 数据目录复制 / 校验自动化 |
| Phase 8.x | ⏳ | 自动更新 / 增量补丁 |
| Phase 9 | ⏳ | 移动端 / Web 客户端共享同一后端 |

---

## 十四、社区 / 反馈 / 商业合作

| 渠道 | 用途 | 地址 |
|---|---|---|
| 官网 | 产品介绍 / 商务联系 | <https://aidooo.com> |
| 微信公众号 | 版本动态 / 技术深读 | 智灵鸟科技 |
| GitHub Issue | 公开技术问题(本仓库) | (内部) |
| GitHub Issue (WPS) | WPS 加载项问题 | <https://github.com/zhgyuhuii/chayuan/issues> |
| 商务邮件 | 企业授权 / OEM / 定制 | 详见官网 |

**贡献指南(简要)**:

1. 任何 PR 请先在 Issue 区开 RFC 讨论方向
2. 遵循 CLAUDE.md 中的代码风格与目录约定
3. 不要触发品牌字符串改动(详见 [§二](#二品牌标识保留要求))
4. 提交前跑 `pnpm typecheck` 与 `pytest -q`
5. PR 描述里说明所属 Phase

---

## 十五、致谢与第三方组件

本项目基于以下杰出开源软件构建,在此一并致谢:

- **[Tauri](https://tauri.app/)** —— 跨平台原生桌面框架
- **[FastAPI](https://fastapi.tiangolo.com/)** —— 高性能 Python Web 框架
- **[LangChain](https://www.langchain.com/)** —— LLM 应用编排
- **[sqlite-vec](https://github.com/asg017/sqlite-vec)** —— 内嵌向量扩展
- **[RapidOCR](https://github.com/RapidAI/RapidOCR)** —— PaddleOCR 转 ONNX
- **[bge-m3](https://huggingface.co/BAAI/bge-m3)** —— 中英多语嵌入模型
- **[Piper TTS](https://github.com/rhasspy/piper)** —— 轻量 CPU 语音合成
- **[FunASR](https://github.com/modelscope/FunASR)** —— 阿里语音识别
- **[Ollama](https://ollama.ai/)** —— 本地 LLM 运行环境
- **[onnxruntime](https://onnxruntime.ai/)** —— 跨平台推理运行时
- **[React](https://react.dev/)** —— UI 框架
- **[TanStack Router / Query](https://tanstack.com/)** —— 路由与服务端状态
- **[Zustand](https://github.com/pmndrs/zustand)** —— 轻量客户端状态
- **[Tailwind CSS](https://tailwindcss.com/)** —— 原子化 CSS
- **[Radix UI](https://www.radix-ui.com/)** —— 无障碍组件原语
- **[Shiki](https://shiki.style/)** —— 语法高亮
- **[Lucide Icons](https://lucide.dev/)** —— 图标
- **[Marked](https://marked.js.org/)** —— Markdown 渲染
- **[DOMPurify](https://github.com/cure53/DOMPurify)** —— XSS 清理
- **[axios](https://axios-http.com/)** —— HTTP 客户端

各组件按其各自许可证分发(详见各项目 LICENSE 文件)。本仓库及其分发产物的最终许可为 **AGPL-3.0**。

---

## 附录甲:常见问题(FAQ)

**Q1: 为什么默认数据目录是 `%APPDATA%\chayuan`?**
A: macOS / Windows / Linux 各家操作系统都有 "应用数据" 标准位置规范,Tauri Path API 会自动取对应路径。
用户首启动可以改成任意路径(D 盘 / 移动硬盘均可)。

**Q2: 装好后关掉外网,还能用吗?**
A: 取决于您配置的模型:
- 都装本地 Ollama / LM Studio / vLLM:**完全可用**
- 配了云端 LLM(DeepSeek / OpenAI / 通义千问 …):**对话不可用,但 KB 检索 / 文档解析 / OCR 仍可用**

**Q3: 桌面图标怎么是 "察元AI" 而不是 "Chayuan"?**
A: NSIS 安装钩子(installer.nsh)会在 Finish 页之后把默认 ASCII 快捷方式 rename 成中文。详见
PACKAGING.md 与 [chayuan-client/apps/desktop/src-tauri/installer.nsh](chayuan-client/apps/desktop/src-tauri/installer.nsh)。

**Q4: 我能不能把后端单独部署到一台服务器,前端连过来?**
A: 当前桌面单机版的设计取向是 "前后端同机";多用户 / 服务器形态请走
[`chayuan-server/packaging/README.md`](chayuan-server/packaging/README.md) 的部署路径,
那是另一条产品线。

**Q5: 与 chayuan-wps 必须同时使用吗?**
A: 不必。两者可独立使用 —— 桌面单机版自身就是一个完整的 AI 客户端;chayuan-wps 是给重度用 WPS 文字
做公文 / 合同 / 标书的政企用户的"WPS 内嵌入口",**两者使用同一个后端时知识库 / 模型 / 历史共享**。

**Q6: AGPL-3.0 限制商业使用吗?**
A: **不限制内部使用 / 内网部署 / 单位分发**。仅在您 "把修改后的版本作为 SaaS / 公网服务对外提供" 时
才要求开源您的修改。具体分级见 [§一](#一版权声明与开源许可)。如需 OEM 白标 / 闭源集成,联系商务取单独商业许可。

**Q7: 模型对抗(Model Arena)怎么用?**
A: 进入对话页 → 顶栏 [+ 添加] 按钮加新泳道 → 每道独立选模型 → 顶栏勾上 "统一发送" → 在任意一道输入,
所有泳道并发流式生成。

**Q8: 本机有麒麟 / UOS 国产 OS,能装吗?**
A: 可以。桌面单机版的 Linux 产物提供 `.deb` / `.rpm` / `.AppImage`,
绝大多数国产 Linux(基于 Debian/Ubuntu/RHEL)都能直接装。麒麟 V10 还有 aarch64 / loongarch64 架构产物。

**Q9: 数据目录可以放到移动硬盘吗?**
A: 可以,首启动向导支持自选路径。注意硬盘掉线时后端 sidecar 会报数据库读写错误,需要手动停应用重选路径。

**Q10: 升级时数据会丢吗?**
A: **绝不丢真实数据**(对话 SQLite / KB 向量索引 / 上传文件)。MSI 卸载只清 `Program Files\Chayuan\` 装机目录,
您的 `CHAYUAN_ROOT`(用户家目录里的真实数据)永远保留。重装后通过 install_id 检测会
弹一次向导让您"重新确认数据目录指向",向导识别旧目录的 `data_dir_marker` 显示"沿用现有数据"。
确认后所有历史数据立即可用。

**Q11: 为什么我重装后还是要选数据目录?**
A: **有意设计**。重装时 exe 修改时间变了 → install_id 变 → 跟旧 `desktop.json` 里的 `linked_install_id`
对不上 → 重新走向导。这是为了:(1) 避免"幽灵复用"老路径让用户以为是新装;(2) 让用户有机会决定
"用回老数据" vs "全新开始"。**不会丢数据**,向导识别老目录会提示沿用。

**Q12: 安装包 ISO 是干啥的?为什么不直接给 MSI?**
A: WiX 模板写死 `EmbedCab="no"`(绕国产杀软对 `C:\Windows\Installer\` 的拦截),所以 MSI 必须跟外置 CAB
(`app1.cab/app2.cab/...`)一起分发,只发 MSI 装到一半会报 Error 1311/1335 找不到文件。
ISO 把 `.msi + .cab + models_seed/`(集成版的大模型)打成单文件,Win10+ 双击自动挂载,体验干净。

**Q13: 集成版装包后第一次启动很慢,是不是卡了?**
A: **不是卡**。集成版 first_launch 钩子会从挂载光驱的 `models_seed/` 把 ≥ 2 GB 大模型
(如 bge-m3 PyTorch 权重)拷到您的 `<CHAYUAN_ROOT>/models/bundled/`,~2-5 分钟正常,SSD 更快。
**请保持光驱挂载到这一步完成**。如果不小心提前弹了光驱,UI 会显示
"external_pending,请重挂 ISO 后重启 chayuan-server",重挂 + 重启即可恢复。

**Q14: 我的电脑装了火绒 / 360 / 腾讯管家,装得上吗?**
A: 装得上。Windows 安装包用 MSI + 外置 CAB(`EmbedCab="no"`),CAB 不被拷到 `C:\Windows\Installer\`,
避开杀软的拦截高发区。装机后 chayuan-server.exe 用 EV 代码签名(规划中);
sidecar 首次启动可能被杀软"主动防御"沙箱跑一遍,延迟十几秒,verify 链路给到 30s 容忍,不会误报。

**Q15: 删除知识库 / 历史会话报 "Command plugin:dialog|confirm not allowed by ACL" 怎么办?**
A: 这是 Tauri 2 webview 把 `window.confirm()` 重定向到 plugin-dialog 的 ACL 边界问题。
v0.x.x 起所有破坏性 confirm 都改走 `platform.dialog.confirm` 包装层(内部用 `plugin-dialog.ask`,
ACL 稳定授权)。如果您还撞到这条,说明装的是旧版,升级最新即可。

---

## 附录乙:术语表

| 术语 | 缩写 / 别名 | 含义 |
|---|---|---|
| **CHAYUAN_ROOT** | — | 用户首启动选定的数据目录,所有用户数据存这里 |
| **Sidecar** | — | Tauri 主进程 spawn 出来的 Python 子进程(chayuan-server) |
| **ku_id** | — | Knowledge-Universe ID,统一知识源标识(如 `doc:产品文档`) |
| **Knowledge Universe** | KU / 察元智库 | 文档 / 结构化 / 向量 / 办公 / 图像 五类知识源的统一抽象 |
| **Model Arena** | — | 模型对抗,多泳道横向对比模型生成 |
| **Lane** | — | 模型对抗中的一道,独立 conversationId / model |
| **Composer** | — | 对话页底部的输入区(选模型 / 选 KB / 选工具 / 输入文本) |
| **MCP** | Model Context Protocol | Anthropic 提出的工具协议,stdio / sse 两种 transport |
| **RAG** | Retrieval-Augmented Generation | 检索增强生成,先检索知识再交给 LLM 生成 |
| **text2sql** | T2SQL | 自然语言转 SQL,LLM 生成查询并经 AST 校验后执行 |
| **OCR** | — | 光学字符识别;本项目默认走 RapidOCR-ONNX |
| **TTS** | Text-To-Speech | 语音合成;默认 Piper |
| **ASR** | Automatic Speech Recognition | 语音识别;可选 FunASR |
| **AGPL-3.0** | — | GNU Affero General Public License v3.0,本仓库许可证 |
| **Stronghold** | — | Tauri 凭据保险箱插件,Argon2id + ChaCha20-Poly1305 |
| **NSIS** | Nullsoft Scriptable Install System | Windows 安装包生成器 |
| **PyInstaller** | — | Python 打可执行打包工具 |
| **bootReady** | — | Shell 启动就绪标志(i18n + hydrate + auth 三件齐) |
| **Splash** | — | 启动动画,五层零延迟挂载到 OS / HTML / CSS / JS / React |

---

<div align="center">

**察元 AI · 桌面单机版** · Tauri 2 + React 19 + Python 3.12 + AGPL-3.0

由 **北京智灵鸟科技中心** 出品 · <https://aidooo.com>

</div>
