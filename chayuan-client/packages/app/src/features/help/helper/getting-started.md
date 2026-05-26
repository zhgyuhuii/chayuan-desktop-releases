# 察元 帮助中心

> 本手册覆盖**安装 → 模型配置 → 对话使用 → 知识库 → MCP / 工具 → 高级用法**全流程,任何一处卡住都能在这里找到答案。

---

## 一、产品定位与核心优点

**察元(Chayuan)** 是一款面向办公场景的离线 AI 助手:把对话、知识库、文档解析、工具调用、模型多平台接入收进**单机一个安装包**,装完即用,数据全程留在本机,**不需要登录、不依赖联网**。

### 与同类工具的对比

| 维度 | 察元(单机版) | 在线 SaaS(ChatGPT / Kimi / 文心一言) | 自部署 LLM(Open WebUI / FastGPT) |
| --- | --- | --- | --- |
| **数据出本机?** | ❌ 默认全留本地 | ✅ 必出本机 | 视部署方式 |
| **要不要登录** | ❌ 不需要 | ✅ 必须 | 通常需要 |
| **离线可用** | ✅ 配本地模型即可全功能 | ❌ 必须联网 | 视部署 |
| **国产系统** | ✅ 麒麟 / 统信 UOS / openEuler | ❌ | ⚠️ 需自己适配 |
| **WPS 嵌入** | ✅ 自带 WPS 加载项联动 | ❌ | ❌ |
| **多模型混调** | ✅ Ollama + 云端厂商任选 | ❌ 单一厂商 | ⚠️ 需多次配置 |
| **知识库一键挂** | ✅ 拖文件即入库 | ⚠️ 限官方文档库 | ⚠️ 步骤多 |
| **多 Tab 工作区** | ✅ 浏览器式多标签 | ❌ | ❌ |

### 跨平台支持(全部一键安装包)

- **Windows 10 / 11**(`.msi` / `.exe`,EV 签名)
- **macOS 12+**(`.dmg`,Apple notarized,Intel + Apple Silicon)
- **Linux**(`.deb` / `.AppImage`)
- **国产系统**:**银河麒麟 V10 / 统信 UOS 1060+ / openEuler 22.03+**(走 Linux x86_64 / ARM64 包,自带 Qt + WebKitGTK 兼容层)

---

## 二、安装

### 2.1 三步装好

1. 到官网 / 内网下载页拿到对应平台安装包。
2. 双击安装,**一路下一步**。安装阶段唯一需要回答的问题就是「数据目录」(默认即可,自动落到平台标准目录)。
3. 启动应用,直接进入主界面 — **不会弹登录页**。

### 2.2 数据目录在哪里

| 平台 | 默认路径 |
| --- | --- |
| Windows | `%APPDATA%\Chayuan\` |
| macOS | `~/Library/Application Support/Chayuan/` |
| Linux / 麒麟 / UOS | `~/.local/share/Chayuan/` |

里面包含:

```
chayuan.sqlite        主库(对话、KB、配置)
vectors/              本地向量库(sqlite-vec)
files/                上传 / 解析的原文件
models_cache/         模型权重缓存
logs/                 日志(出问题先看这里)
config.toml           用户配置
```

**迁移**:把整个数据目录拷到新机器/新路径,启动时改一下 `CHAYUAN_DATA_DIR` 环境变量或在「设置 → 数据目录」里切换,数据就跟过去了。

---

## 三、模型配置(重点 Ollama)

察元采用「模型广场 = 厂商配置中心」的思路:在模型广场里配置一个或多个**厂商**,每个厂商背后挂着一组**模型**(对话 / 嵌入 / 重排 / 视觉等)。配置完成后,在对话框、知识库等所有需要选模型的地方都会自动看到。

### 3.1 用本地模型还是云端模型?

| 场景 | 推荐 |
| --- | --- |
| 完全离线 / 私密性要求高 | **本地模型(Ollama)** |
| 中英文对话,追求最强能力 | 云端旗舰(Claude / GPT / DeepSeek) |
| 文档 RAG / 知识库 | **本地嵌入(bge-m3)+ 本地重排(bge-reranker-v2-m3)**(下面详解) |
| 写代码 / 复杂推理 | 任选一种带 thinking mode 的模型 |

实际部署里,**多数用户用「本地嵌入 + 重排 + 一个云端对话模型」混搭**,既不把文档原文发到云上,又能享受云端模型的对话能力。

### 3.2 安装 Ollama(本地模型运行时)

Ollama 是把开源大模型跑在本地最简单的方式。它会自动管理模型权重、自动选择 GPU/CPU、暴露一个 OpenAI 兼容 HTTP 接口。

#### Windows
1. 打开 https://ollama.com/download/windows 下载 `OllamaSetup.exe`。
2. 双击安装,装完后会在系统托盘有一个羊驼图标。
3. 任意启动一个 PowerShell,运行 `ollama --version` 验证。

#### macOS
1. 打开 https://ollama.com/download/mac 下载 `Ollama-darwin.zip`。
2. 解压后拖到「应用程序」,双击运行。
3. 终端 `ollama --version` 验证。

#### Linux / 麒麟 / UOS
```bash
curl -fsSL https://ollama.com/install.sh | sh
# 或者离线包:从 https://github.com/ollama/ollama/releases 下载对应架构 .tgz 解压到 /usr/local/bin
```

> 装完后默认监听 `http://127.0.0.1:11434`。本机访问无需任何额外配置,察元会自动探测。

### 3.3 拉取推荐的三件套

打开终端,**一行一行执行**(每行第一次执行会下载几 GB,后续秒级载入):

```bash
# ① 对话模型(7B 中文良好;4B 显存 6GB 也能跑)
ollama pull qwen2.5:7b
# 显存少 → ollama pull qwen2.5:3b
# 想试 DeepSeek → ollama pull deepseek-r1:7b

# ② 嵌入模型 — 把文档 / 问题转成向量(下面知识库要用)
ollama pull bge-m3:latest

# ③ 重排模型 — 二次排序检索结果,大幅提升精度
ollama pull dengcao/bge-reranker-v2-m3:latest
```

> **为什么选这两个?** `bge-m3` 是 BAAI 出品的多语种(支持中英 100+ 语言)开源 embedding,目前公认中文 RAG 效果最稳的本地选项;`bge-reranker-v2-m3` 是配套的 cross-encoder reranker,把召回的 top-50 重新排序后取 top-5,精度比单纯向量召回高一档。两者占用都很小(<3GB),CPU 也能跑。

### 3.4 在察元里配置 Ollama 厂商

1. 顶部导航点「**模型广场**」。
2. 顶部 Tab 切到「**本地**」分类(也可在「**推荐**」里直接看到 Ollama)。
3. 点 Ollama 卡片 → 进详情抽屉。
4. 字段填写(本机一般默认即可):
   - **API base**:`http://127.0.0.1:11434`
   - API key:留空
5. 点「**测试连通性**」按钮 — 应该看到 ✅ 绿勾 + 自动抓到的模型列表(`qwen2.5:7b` / `bge-m3:latest` / `dengcao/bge-reranker-v2-m3:latest` 等)。
6. 给每个模型在右侧打勾启用,并设置「能力标签」:
   - `qwen2.5:7b` → **对话(chat)**
   - `bge-m3:latest` → **嵌入(embedding)**
   - `dengcao/bge-reranker-v2-m3:latest` → **重排(rerank)**
7. 保存即可。

### 3.5 配云端厂商(可选)

模型广场顶部 Tab:

- **国内**:DeepSeek、通义千问、智谱、Moonshot、百川、字节豆包、SiliconFlow…
- **国外**:OpenAI、Anthropic、Google、Mistral、xAI、Groq、OpenRouter…
- **聚合**:OpenRouter / OneAPI / NewAPI(一个 key 打多个模型)
- **自定义**:任何 OpenAI 兼容 endpoint 自填

每张卡片点进详情 → 填 API base + API key → 测试连通性 → 启用所需模型即可。**模型广场只管配置,在哪里使用模型由对话框等具体场景的「模型选择器」决定。**

---

## 四、对话使用

### 4.1 发送第一条消息

1. 左侧导航点「**对话**」(或顶部 `+ 新对话`)。
2. 输入框上方点模型徽标 → 选刚刚启用的对话模型(如 `qwen2.5:7b`)。
3. 输入问题,回车发送。

### 4.2 消息中能看到什么

- **思考过程**:开了 `深度思考` 的模型(如 DeepSeek-R1、Qwen-thinking)会折叠展示推理过程,可点开查看。
- **本次调用工具**:LLM 调了工具/MCP 时,会出现一条琥珀色横线,点开看调用了哪些工具,再点开每个工具看入参 + 结果详情(像浏览器开发者工具那样三层下钻)。
- **引用依据**:绑定了知识库时,出现一条紫色横线,点开看命中的来源、文件、片段,**点击片段可在右侧打开预览**(支持 PDF / Word / 图片)。

### 4.3 输入框右侧三个开关

- **深度思考**:开启后请求里会带 `reasoning` 标识,模型自动进入思考模式(对应支持的模型才生效,如 deepseek-r1 / qwen3-thinking / step-2-mini-thinking)。
- **联网搜索**:让 LLM 通过工具调用 web 搜索补充上下文。
- **工具 / MCP**:决定本次允许 LLM 调用哪些工具和 MCP server(详见下文「工具中心」「MCP」)。

### 4.4 多窗口对话(模型对抗)

顶栏「**+ 添加**」按钮可以在当前 tab 内并排新增「永道」(lane),每条永道独立一个模型 + 对话上下文。常用法:

- 同一问题让 GPT-5 / Claude / DeepSeek 同步回答,直观对比。
- 「拖出独立窗口」可以把某个 lane 单独悬浮成一个原生窗口,便于多屏排布。

---

## 五、知识库

### 5.1 把文件变成知识

1. 左侧导航 → **知识中心**。
2. 进入「我的知识库」(默认有一个 `doc:default`,也可点 `+ 新建知识库` 自己开)。
3. 把 PDF / Word / Markdown / Excel / 图片 等文件**拖**进卡片即开始入库:
   - 文档解析(PyMuPDF / Mammoth / pandoc)
   - 切片(按段 / 表格独立)
   - 嵌入(用你在模型广场启用的 `bge-m3:latest`)
   - 写入本地 `sqlite-vec` 向量库

进度通过 SSE 推送实时显示,无需刷新。

### 5.2 在对话里挂知识库

输入框下方「**知识库**」按钮 → 弹出选择器,可勾选一个或多个 KB:

- 勾选后,提问会自动经过「检索 → 拼上下文 → 回答」流程。
- 答案末尾出现「引用依据」紫色横线,展开看实际命中的片段。

### 5.3 检索模式 — 精准 / 全面 / 速度三档

| 模式 | 召回策略 | 是否过 reranker | 适合场景 |
| --- | --- | --- | --- |
| **精准 (precise)** | 向量 top-30 → reranker rerank 取 top-5 | ✅ 是 | 法规、合同、定义、报价单等**需要原话引用**的场景 |
| **全面 (broad)** | 向量 top-50 + 关键词 top-30 → 合并 → rerank 取 top-15 | ✅ 是 | 开放问题、综述、跨多文件汇总 |
| **速度 (fast)** | 向量 top-5 直出,不过 reranker | ❌ 否 | 闲聊式问答、对延迟敏感、文档量小 |

**怎么选**:**默认精准**;答得不全 → 切「全面」;觉得慢 → 切「速度」(代价是精度下降)。三种模式在输入框「检索控件」里切换,对话过程中可随时调整。

### 5.4 知识源类型

察元的 KB 不是只有「文档库」一种:

- **文档(`doc:*`)**:PDF/Word/MD 等 → 向量 + 全文检索
- **结构化(`src:*`)**:连本地 / 远程数据库,问「有几个用户」「最近 10 个订单」会走 SQL planner 而非文档检索
- **图像(`src:image:*`)**:图片库,走多模态嵌入,可以以图搜图 / 文搜图
- **向量(`src:vec:*`)**:对接外部向量库的纯 vector collection

---

## 六、工具中心

工具中心位于左侧导航 **工具**,内置 30+ 工具,默认全部禁用,逐个按需启用。常用清单:

| 工具 | 用途 | 配置 |
| --- | --- | --- |
| `search_internet` | DuckDuckGo / Google / Bing 联网搜索 | 选搜索引擎,可选 SerpAPI key |
| `url_reader` | 抓取网页正文 | 无需配置 |
| `amap_weather` | 中国大陆城市天气(高德) | 高德 API key |
| `openweather` | 全球城市天气 | OpenWeather API key |
| `calculate` | 数学计算(numexpr) | 无需配置 |
| `wolfram` | 符号数学 / 方程求解 | Wolfram appid |
| `arxiv` / `pubmed` / `semantic_scholar` | 学术论文检索 | 无需配置 |
| `wikipedia_search` | 维基检索 | 无需配置 |
| `lark_message` / `wechat_work_message` / `dingtalk_message` | 飞书/企微/钉钉群消息推送 | 各自机器人 webhook |
| `text2sql` / `text2promql` | 自然语言 → SQL / PromQL | 无需配置 |
| `python_repl` / `shell` | ⚠️ 危险,默认禁用 | 仅在开了沙箱后启用 |

### 6.1 让 LLM 自动调工具

1. 工具中心里把目标工具开关打开,填好必要 API key。
2. 对话框「工具 / MCP」按钮 → 勾选要本次启用的工具。
3. 提问。LLM 会按工具的 description 自动决定是否调用,**这就是为什么察元的工具描述都是中文「调用时机 + 输入 + 输出 + 不要用于」结构 — 命中率明显高于通用一句话**。

### 6.2 添加自定义工具(HTTP 接口)

工具中心顶部「**+ 自定义工具**」 → 三种方式:

#### 方式 A:OpenAPI 导入

填一个 OpenAPI / Swagger 的 URL 或粘贴 JSON,系统会自动把每个 endpoint 注册成一个工具(LLM 看到的就是 endpoint 的 summary + 参数 schema)。**最快**,适合接公司内部 API 网关。

#### 方式 B:手填 HTTP

- 工具名(英文,LLM 用)
- 中文显示名
- 描述(给 LLM 看 — 越具体命中越准,推荐写「调用时机 / 输入 / 输出 / 不要用于」四段)
- HTTP method + URL 模板(支持 `{var}` 占位,从入参取值)
- 入参 schema(JSON Schema 风格,字段名 + 类型 + 描述)
- 鉴权(none / Bearer / Header / Basic)

#### 方式 C:粘贴 cURL

把一条 `curl` 命令粘进来,系统反向解析成方式 B 的字段,**这是最快的「我有现成 cURL,想暴露给 AI」路径**。

### 6.3 自定义工具的提示词写法

照着内置工具的格式写 description,LLM 命中率显著提升:

```text
{工具中文名} — {一句话讲做什么}。
调用时机:用户**明确提到** XX / YY / ZZ 时;不要把 AAA 也包进来。
输入:param1(说明,带例子);param2(可选,默认 X)。
输出:{返回结构概述}。
不要用于:{容易误调用的场景列表,显式排除}。
```

---

## 七、MCP(Model Context Protocol)

MCP 是 Anthropic 提出的 LLM 工具协议,把工具按「server」打包,任何兼容客户端都可以接入。察元支持:

- **stdio**:本地命令行进程(如 `npx -y @modelcontextprotocol/server-filesystem /workspace`)
- **SSE / HTTP**:远程 server(如 `https://your-mcp.example.com/sse`)

### 7.1 添加一个 MCP server

1. 左侧导航 → **MCP**。
2. 顶部「+ 添加 MCP」 → 选传输方式(stdio / sse)。
3. 填:
   - 名称
   - command + args(stdio)/ url(sse)
   - 环境变量(可选)
4. 保存后系统自动连接,「能力」一栏会显示该 server 暴露的 **tools / resources / prompts**。
5. 在对话框「工具 / MCP」按钮里勾选要启用的 MCP server,LLM 即可像用本地工具一样调用其能力。

### 7.2 推荐入门 MCP server

| Server | 能力 | 命令 |
| --- | --- | --- |
| filesystem | 读写本地文件 | `npx -y @modelcontextprotocol/server-filesystem /path/to/dir` |
| github | GitHub 仓库 / issue / PR | `npx -y @modelcontextprotocol/server-github`,需 `GITHUB_TOKEN` |
| sqlite | 本地 SQLite 查询 | `npx -y @modelcontextprotocol/server-sqlite --db ~/data.db` |
| memory | 跨会话记忆 | `npx -y @modelcontextprotocol/server-memory` |
| puppeteer | 浏览器自动化 | `npx -y @modelcontextprotocol/server-puppeteer` |

> **MCP vs 自定义工具怎么选?** 已有现成 MCP server → 直接接;没有但有 HTTP API → 自定义工具更省事。MCP 适合「工具集合」,自定义工具适合「单个端点」。

---

## 八、高级:与 WPS 加载项联动

察元自带 WPS 加载项(`chayuan` 工程),装上后在 WPS 任务窗格里能直接对当前文档做 AI 操作 + 引用本机察元服务里的知识库。

### 8.1 在 WPS 里挂上桌面端的 KB

1. 桌面端正常运行(任意时刻),里面已经创建好若干 KB 并入库完毕。
2. 打开 WPS,装好察元加载项(从「插件」→「我的插件」加载,或企业部署批量分发)。
3. 任务窗格点「**绑定桌面端**」 → 加载项会自动连本机 `127.0.0.1` 上桌面端服务,出现 KB 列表。
4. 选要用的 KB(可多选,**支持文档/结构化/图像三种类型混合**),保存。

### 8.2 在 WPS 里如何使用

- **侧边栏问答**:右侧任务窗格输入问题,模型从绑定的 KB 检索 → 给答案 + 引用。
- **选中段落 → 改写 / 翻译 / 摘要**:右键菜单「察元 AI」→ 选指令,**用桌面端正在用的同一个对话模型 + 同一套知识库**,体验和桌面端一致。
- **批注式插入**:答案可一键插入到光标位置或作为批注挂到选区。
- **引用回链**:答案里的「[出处 N]」点击会高亮原文档片段,**与桌面端预览面板同源**。

### 8.3 WPS 加载项的限制

- 加载项**只读**桌面端的模型 + KB 配置,不在 WPS 里改 — 这样保证桌面端是唯一真源。
- 大文件解析仍然走桌面端服务(WPS 进程不做重活)。
- WPS 关掉不影响桌面端;桌面端关掉则 WPS 加载项进入「未连接」状态,UI 自动收起 AI 面板。

---

## 九、常见问题

**Q: 安装包能放在没网的内网机器上用吗?**
A: **能**。装上之后,只要本机有 Ollama 模型 / 提前下载好的 ONNX 嵌入,就完全离线运行。云端厂商不配就好。

**Q: 数据会不会上传?**
A: **不会**。除非你主动配了云端模型(那是你给云端发请求,察元不参与)。日志、向量库、对话历史全在本机。

**Q: Ollama 模型很慢?**
A: 检查模型尺寸(7B 显存 < 6GB 会爆内存退化为 CPU)、关掉其它占显存的应用、给 Ollama 设 `OLLAMA_NUM_PARALLEL=1`。CPU 推理 7B 大约 5-15 token/s,正常。

**Q: 知识库检索答得不全?**
A: 把检索模式从「精准」切到「全面」;还是不全 → 检查文档是否被切片正确(知识中心点文件可看切片预览);仍然不全 → 调高 reranker top-K(在 KB 详情设置里)。

**Q: 国产系统(麒麟 / UOS)安装报缺库?**
A: 用 AppImage 版本,内置依赖更全。仍报错就把 logs/ 目录打个 zip 给支持。

**Q: 怎么备份 / 迁移?**
A: 把数据目录(见 §2.2)整个拷走即可,**所有状态包含在内**。

---

> 找不到答案?**菜单 → 反馈** 扫码联系我们,或附上 logs/ 目录最近一份日志发邮件。
