# 察元办公(OnlyOffice 嵌入)— 设计与实施规划

> 目标:在客户端左侧新增「察元办公」入口,把 OnlyOffice Document Server
> 嵌入到客户端中,实现:本地选区/全文 ↔ 编辑器双向交互、文档助手(基于
> 选中内容的 LLM 协作)、账号与察元统一、文件元数据接入察元后端 Postgres、
> 一键探活、安装时一键部署整套服务栈(OnlyOffice + Postgres + MinIO + Redis)。
>
> 受众:阅读后能直接落地实施。欢迎在审查阶段就驳回方向性问题
> (例如"不要打 Docker、要原生进程",或"先只做 Linux"),再细化字段契约。

---

## 0. 背景与术语

### OnlyOffice Document Server 是什么
OnlyOffice DocumentServer(以下简称 **DS**)是一套**无状态文档协作引擎**,
通过 web iframe 提供 Word / Excel / PowerPoint 三类文档的浏览、编辑、
多人实时协作能力。它本身**不存文件、不管用户**:
- 文件由集成方提供 URL,DS 拉去渲染;
- 用户身份由集成方在 `editorConfig.user` 中注入,DS 只信 JWT。

官方分发形态:
- **Linux 包**:`onlyoffice-documentserver` deb / rpm
- **Docker 镜像**:`onlyoffice/documentserver`(社区版免费)
- **Windows / macOS**:**没有原生服务端二进制**,必须通过 Docker / VM

DS 暴露:
- `:80/web-apps/apps/api/documents/api.js` — 前端编辑器 SDK
- `:80/healthcheck` — 探活
- `:80/coauthoring/CommandService.ashx` — 后端命令通道(强制保存、关版本等)

### 为什么放进客户端
1. 察元已经包含 KB / 文档预览能力,但**只读**,用户对文档的二次创作还要切到本地 Office;
2. 把编辑器内嵌后,**LLM ↔ 文档**的交互闭环可以做完整(选区→助手→改写→回写);
3. 客户端已有「KB/MCP/Tools/Marketplace」等卡片化运维入口,办公模块同形态延续设计语言;
4. 一键部署整套(DS + 数据库 + 对象存储 + 缓存)对**离线/局域网交付**场景价值极高。

### 与 察元 现有体系的关系
- **账号**:复用 `chayuan-server` `/auth/*`,DS 用户即察元用户;
- **文件存储**:复用现有 MinIO(已经在用),DS 通过预签名 URL 取文件;
- **数据库**:文档元数据 / 协作会话进 `chayuan-server` 的 Postgres;DS 自己的内部库可以
  共用同一 Postgres 实例(不同 schema)或独立实例,见 §2;
- **LLM**:文档助手走 `/chat/v2/chat`(LangGraph),与现有助手共用上下文。

---

## 0.5 两条实施路线(本次新增的总纲)

OnlyOffice 集成有两个清晰可分的工程目标:**"能用"** vs **"用得深"**。
为避免一上来就吞掉 8 周的离线包/插件/部署链路,把整体规划拆成两条**可独立交付**
的路线,Route A 是 Route B 的子集 —— **A 先上,再决定是否走 B**。

| 维度 | **Route A — 轻量接入** | **Route B — 深度集成** |
|---|---|---|
| **核心交付** | 用户在 `Settings > 办公` 填一个外部 DS URL(+ JWT secret),客户端嵌入编辑器,能开/能存 | 一键部署整套服务栈(DS + PG + MinIO + Redis + RabbitMQ),用户、文件、协作、助手全部统一 |
| **目标用户** | 已自建 OnlyOffice 的企业 / 内网 / 测试环境 | 单机用户 / 离线交付 / 团队协作 |
| **后端工作量** | `/office/docs` CRUD + `/config` 签 JWT + `/callback` + `/file` 预签名;**不需要** `/deploy/*`、不需要镜像分发 | 全部 §2 路由 + §6 部署编排 + §5 协作字段 + 助手相关 |
| **前端工作量** | 路由 / Sidebar / 文档列表 / iframe 编辑器 / 设置页填 URL | A 的全部 + 部署向导 / 进度页 / 健康面板 / 状态点 / 卸载 / 助手抽屉 |
| **账号统一** | ✅ **两条路线都做**(`editorConfig.user.id` = 察元 user.id,后端签 JWT) | ✅ 同 A,加 `device_id` 后缀支持多端协作 |
| **文档助手(LLM)** | ❌ v1 不做(或仅做"复制选区到外部对话"占位) | ✅ 自定义插件 + postMessage 桥 + 替换/插入回写 |
| **多人协作** | △ 取决于外部 DS 配置;客户端不阻碍 | ✅ 默认开启(可关) |
| **一键部署 / 镜像离线包** | ❌ 不做 | ✅ §6 全套 + 1.6GB 离线包分发 |
| **跨平台 QA 矩阵** | 1×3(浏览器嵌入,Win/Mac/Linux) | 6 矩阵(× 在线/离线) |
| **代码签名 / 公证** | 复用客户端现有签名 | 新增打包流水线 |
| **预估工期(单人)** | **1.5–2 周** | **再叠加 6–8 周** |
| **预估工期(双人)** | **~1 周** | A 后再 4 周 |

### 路线选型建议
- **Route A 必做**,因为它顺带把后端契约 / 前端骨架 / 账号统一这三件"无论走哪条都要做"的事打通,
  且单独发版有完整价值(企业用户自带 DS 即可用)。
- **Route B 按需**,按以下信号触发:
  1. 单机/无运维用户超过 30%(从客户端遥测看);
  2. 法务/采购侧明确要求"开箱即用,不依赖外部 DS";
  3. 助手"选区改写/回写"成为优先级最高的产品需求。
- 不存在"只做 B 不做 A"的分支:Route B 复用 A 的所有契约。

> 后文 §1–§8 的契约 / UI / 安全章节同时适用两条路线,在小节内会用
> **[A]** / **[B]** / **[A+B]** 标注边界。§9 / §10 单独拆出两条难度与里程碑表。

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Tauri Desktop (chayuan-client)                                 │
│                                                                 │
│  ┌─────────────┐   ┌────────────────────────────────────────┐   │
│  │ Sidebar     │   │  Office Workspace (React)              │   │
│  │  …          │   │  ┌──────────────┐  ┌────────────────┐  │   │
│  │  📄 办公 ●──┼──>│  │ 文档列表/树   │  │ <iframe DS>     │  │   │
│  │  …          │   │  │              │  │                 │  │   │
│  └─────────────┘   │  └──────────────┘  │ postMessage桥   │  │   │
│                    │   ┌──────────────┐ │  ↕              │  │   │
│                    │   │ 文档助手抽屉 │<┤   选区 / 全文  │  │   │
│                    │   │ (LLM 对话)   │ │   插入 / 替换  │  │   │
│                    │   └──────────────┘ └────────────────┘  │   │
│                    └────────────────────────────────────────┘   │
│                                  │                              │
│                                  │ HTTP + JWT                   │
│                                  ▼                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ chayuan-server (FastAPI, /api)                           │   │
│  │  /office/*  ← 新增,详见 §2                               │   │
│  │   ├─ POST /office/docs                  创建文档元数据    │   │
│  │   ├─ GET  /office/docs/{id}/config      签 editorConfig + JWT│
│  │   ├─ POST /office/docs/{id}/callback    DS 保存回调       │   │
│  │   ├─ GET  /office/docs/{id}/file        预签名重定向 MinIO│   │
│  │   ├─ POST /office/docs/{id}/assistant/* 选区 → LLM 桥     │   │
│  │   └─ GET  /office/health                聚合探活          │   │
│  └──────────────────────────────────────────────────────────┘   │
│            │           │              │             │           │
│            ▼           ▼              ▼             ▼           │
│      ┌─────────┐  ┌────────┐  ┌────────────┐  ┌─────────┐       │
│      │Postgres │  │ MinIO  │  │ DS (docker │  │ Redis   │       │
│      │ chayuan │  │  files │  │  + nginx)  │  │  cache  │       │
│      └─────────┘  └────────┘  └────────────┘  └─────────┘       │
│                                                                 │
│      上述四个服务在「一键部署」模式下由客户端拉起 docker compose │
└─────────────────────────────────────────────────────────────────┘
```

### 部署形态(对应 §0.5 两条路线)
用户在 `Settings > 办公` 切换:

1. **External(Route A,默认形态)**:用户填一个外部 DS URL + JWT secret,客户端
   不部署本地服务,只做 iframe 嵌入和后端契约。**v1 默认**,**适用已有企业内网部署 / 自建测试 DS 的用户**。
2. **Embedded(Route B,一键部署)**:客户端通过 Docker 起 5 个容器(DS + PG +
   MinIO + Redis + RabbitMQ),数据卷写到用户目录
   (`%APPDATA%/chayuan/office` / `~/Library/Application Support/chayuan/office`
   / `~/.local/share/chayuan/office`)。**适用单机 / 局域网 / 离线交付用户**,在 Route B 上线后才放开。

> 切换器 UI 一直在,但 Embedded 的入口在 Route B 没交付前置灰并提示"即将推出"。

### 不把 DS 嵌入 Tauri 主进程的原因
- DS 依赖 nginx + Node.js + RabbitMQ + Postgres,**自己就是个完整的服务栈**,
  没法塞进单二进制;
- 官方只发 Docker / Linux 包,跨平台只能通过 Docker;
- 即使在 Linux 上拆包提取二进制,版本升级时维护成本极高。

---

## 2. 数据模型 / API 契约

### 2.1 后端路由(全部 prefix `/api/v1/office`,鉴权同 MCP/Tools)

> **A** = Route A 必做;**B** = Route B 才需要;**A+B** = 两条路线共用。

| 路线 | 方法 | 路径 | 说明 |
|---|---|---|---|
| A+B | `GET`  | `/office/health` | A 模式只 ping 外部 DS;B 模式聚合 5 服务 + 容器状态 |
| **B** | `POST` | `/office/deploy/start` | 一键部署(后端 SSE 推进度) |
| **B** | `POST` | `/office/deploy/stop`  | 停止本地服务栈 |
| **B** | `GET`  | `/office/deploy/status` | 容器状态 + 版本 + 卷大小 |
| A+B | `GET`  | `/office/settings` | 读当前模式(external/embedded)+ DS URL + 是否已配置 |
| **A** | `PATCH`| `/office/settings/external` | 改外部 DS URL / JWT secret(只在 external 模式可用,JWT 写 secrets 表) |
| A+B | `GET`  | `/office/docs?folder=` | 列文档(分页 + 搜索) |
| A+B | `POST` | `/office/docs` | 创建空白文档(模板可选) |
| A+B | `POST` | `/office/docs/upload` | 上传本地文件,落 MinIO |
| A+B | `GET`  | `/office/docs/{id}` | 文档元数据 |
| A+B | `DELETE` | `/office/docs/{id}` | 软删(回收站) |
| A+B | `GET`  | `/office/docs/{id}/config` | 返回签好 JWT 的 `editorConfig`,前端直接喂 DS SDK |
| A+B | `POST` | `/office/docs/{id}/callback` | **DS → 后端**保存回调,内部接口,JWT 校验 |
| A+B | `GET`  | `/office/docs/{id}/file?token=…` | DS 拉文件用,一次性 token,302 到 MinIO 预签名 |
| A+B | `POST` | `/office/docs/{id}/forcesave` | 强制存盘(走 DS CommandService) |
| A+B | `GET`  | `/office/docs/{id}/versions` | 历史版本列表 |
| **B** | `POST` | `/office/docs/{id}/assistant/rewrite` | 选区 → LLM(改写/翻译/总结/续写) |
| **B** | `POST` | `/office/docs/{id}/assistant/insert` | 选区 → LLM 生成 → 待写回 |
| **B** | `WS`   | `/office/docs/{id}/assistant/stream` | 助手 SSE/WS 流式响应 |

> A 模式下,`/office/health` 的实现简化为 `httpx.get(<external>/healthcheck, timeout=2s)`,
> 不查容器、不读卷;返回结构同 §2.3 但 `services` 只含 `documentserver`。

### 2.2 数据库表(`chayuan-server` 的 Postgres,新 migration `0005_office`)

```sql
create table office_document (
  id           uuid primary key,
  owner_id     bigint not null references "user"(id),
  title        text not null,
  kind         text not null check (kind in ('docx','xlsx','pptx')),
  storage_key  text not null,             -- minio object key
  size_bytes   bigint not null default 0,
  doc_key      text not null unique,      -- DS 协作 key,改文件就换
  current_version int not null default 1,
  is_deleted   boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index office_document_owner_idx on office_document (owner_id, is_deleted, updated_at desc);

create table office_document_version (
  id           bigserial primary key,
  document_id  uuid not null references office_document(id) on delete cascade,
  version_no   int not null,
  storage_key  text not null,             -- 历史 minio key
  saved_by     bigint references "user"(id),
  changes_url  text,                      -- DS 提供的 changes.zip
  created_at   timestamptz not null default now(),
  unique(document_id, version_no)
);

create table office_assistant_action (
  id           bigserial primary key,
  document_id  uuid not null references office_document(id) on delete cascade,
  user_id      bigint not null references "user"(id),
  selection    text,                      -- 选区原文(可能很大,>4KB 截断到对象存储)
  prompt       text not null,
  result       text,
  status       text not null default 'pending',
  created_at   timestamptz not null default now()
);
```

> DS 自身的 Postgres schema(`onlyoffice` 用户)与上面**完全独立**,
> 部署时建议用同一 PG 实例不同 db,降低运维面。

### 2.3 关键响应类型(前端 TS 镜像)

```ts
// GET /office/docs/{id}/config
type DocConfig = {
  serverUrl: string;        // http://127.0.0.1:<动态端口>
  config: {
    document: {
      key: string;           // = office_document.doc_key
      title: string;
      url: string;           // /office/docs/{id}/file?token=<one-time>
      fileType: 'docx' | 'xlsx' | 'pptx';
      permissions: { edit: boolean; download: boolean; print: boolean; comment: boolean };
    };
    documentType: 'word' | 'cell' | 'slide';
    editorConfig: {
      mode: 'edit' | 'view';
      lang: 'zh-CN' | 'en';
      callbackUrl: string;   // /office/docs/{id}/callback?token=<one-time>
      user: { id: string; name: string };
      customization: { autosave: true; forcesave: true; uiTheme: 'theme-light' | 'theme-dark' };
      plugins: { autostart: ['asc.{{guid}}'] };  // 自定义插件:右键菜单 → 助手
    };
  };
  token: string;             // 整体 JWT
};

// GET /office/health
type OfficeHealth = {
  overall: 'healthy' | 'degraded' | 'down';
  services: {
    documentserver: { status: 'up'|'down'; version?: string; latencyMs?: number };
    postgres:       { status: 'up'|'down'; latencyMs?: number };
    minio:          { status: 'up'|'down'; latencyMs?: number; bucketReady: boolean };
    redis:          { status: 'up'|'down'; latencyMs?: number };
  };
  ports: { documentserver: number; postgres: number; minio: number; redis: number };
  dockerAvailable: boolean;
  deployedAt?: string;
};
```

### 2.4 DS 回调与 JWT
- 所有出入 DS 的 config / callback / 文件 URL 都签 JWT(HS256),secret 在
  首次部署时由后端随机生成,写入 DS 容器的 `JWT_SECRET` env,落 `chayuan-server`
  的 secrets 表。**前端永远拿不到 secret**。
- `editorConfig.user.id` 用 `chayuan-server` 的 `user.id` + `:device_id` 拼接,
  避免同一用户多端打开时 DS 把光标合并(详见 OnlyOffice 协作模型)。

---

## 3. 前端 UI 设计

### 3.1 路由 / 入口
- 新增路由:`/office`(列表)、`/office/d/:docId`(编辑器);
- Sidebar 在 `Tools` 与 `AI Space` 之间插入条目,labelKey `nav.office`,
  icon `FileText`(lucide),`iconKeyFor` 加 `if (path.startsWith('/office')) return 'file-text'`;
- i18n:`zh-CN.json` 加 `nav.office: '察元办公'`,`en.json` 加 `nav.office: 'Office'`。

### 3.2 状态分支(`OfficeWorkspace` 主容器)

| 状态 | 触发 | UI |
|---|---|---|
| `not-deployed` | `health.overall = 'down'` 且 `dockerAvailable && !deployedAt` | `DeployHero`:大按钮「一键部署办公套件」+ 服务清单 + 预计磁盘占用 |
| `docker-missing` | `dockerAvailable = false` | `DockerInstallGuide`:下载 Docker Desktop 链接 + 复检按钮 |
| `deploying` | `deploy/start` 进行中 | `DeployProgress`:SSE 进度条(拉镜像 / 起容器 / 等待健康) |
| `degraded` | 部分服务 down | 顶部红条 + 「修复」按钮调 `deploy/start --repair` |
| `ready` | 全绿 | `DocList` + 顶部状态点(绿) |
| `external` | 用户配置了外部 DS | 隐藏部署面板,只有探活和文档列表 |

### 3.3 编辑器页(`/office/d/:docId`)
左右两栏布局,响应式可折叠:

**左主区:DS iframe**
- 由 `<OfficeEditor>` 组件加载 `serverUrl + /web-apps/apps/api/documents/api.js`,
  `new DocsAPI.DocEditor('placeholder', config)` 启动;
- iframe 通过 `postMessage` 与父窗口通信(详见 §4);
- 加载失败回退:显示「编辑器加载失败」+ 探活快捷入口。

**右抽屉:文档助手 `<DocAssistantPanel>`**
- 默认收起,Cmd/Ctrl+J 展开;
- 上半部:对话流(消息气泡,流式),复用 `MessageBubble`;
- 下半部:Composer:
  - 「使用选区」开关(默认 ON)→ 发送时把当前选区文本带上;
  - 快捷动作:总结 / 改写 / 翻译 / 续写 / 提取要点 / 生成大纲(每个动作一个 chip);
  - 输入框 + 发送;
- LLM 返回后,消息气泡上多两个按钮:
  - **替换选区**:把回复内容写回 DS 当前选区(走 §4 桥);
  - **插入光标处**:在光标位置 insertText;
  - **复制**:复制到剪贴板;
- 历史动作存 `office_assistant_action`,可在助手面板顶部「历史」抽屉里翻。

### 3.4 文档列表页(`/office`)
- 复用现有 KB 列表的视觉:卡片网格 / 列表二选一切换;
- 顶栏:新建(下拉:Word/Excel/PPT)、上传、搜索、视图切换;
- 卡片:缩略(由 DS 提供的 thumbnail API 抓)、标题、所有者、修改时间;
- 右键菜单:打开 / 重命名 / 复制 / 历史版本 / 删除;
- 空态:`<EmptyState>` 引导新建或上传。

### 3.5 一键探活
- `/office` 顶栏右上常驻状态点(绿/黄/红),点击展开 `<HealthPopover>`:
  - 4 个服务各自一行 + 端口 + 延迟;
  - 「重新检测」按钮调 `/office/health?refresh=1`(后端绕过缓存);
  - 「查看日志」按钮跳 `Settings > 办公 > 日志`。
- 后端 `/office/health` 实现要点:5s 内并行 ping 4 个服务,各服务 timeout 2s,结果缓存 3s。

### 3.6 Sidebar 集成与状态点
- 在 `Sidebar.tsx` 的 `nav.office` 项右侧画一个 4×4 状态圆点(同 OpenClaw 计划做法);
- 颜色:绿 = ready / 黄 = degraded / 红 = down / 灰 = 未部署;
- 数据来源:`useOfficeHealthStore`,后台每 30s 轮询 `/office/health`(只在客户端聚焦时)。

---

## 4. 本地 ↔ 编辑器交互(关键技术点)

> 本章遵循 [ADR-0006 OnlyOffice 集成 API 路线选型](../adr/0006-onlyoffice-integration-routes.md) 的决议:
> **C(Proxy Plugin)主 + B(服务端改写)辅** 的混合路线。
> 历史方案 D(业务插件)已废弃;选 C 而不是纯 B 的理由见 ADR §决策。

### 4.0 路线分发原则

父窗口的 `actionRouter` 按操作类型把请求分到两条路径:

| 操作类型 | 走哪条 | 原因 |
|---|---|---|
| 选中文字 → 加粗 / 斜体 / 高亮 | **C 实时** | 用户期望即时反馈 |
| 选中一句 → 翻译 / 改写 | **C 实时** | 选区状态只能 plugin 拿到 |
| 查找替换(单文档全局) | **C 实时** | 用 `executeMethod('SearchAndReplace')` |
| 选区 → 添加评论 | **C 实时** | `AddComment` |
| 全文翻译 / 全文摘要 / 章节重写 | **B reload** | 大改动需审计、可回滚,沉淀 version |
| 批量插评论(全文检查) | **B reload** | 一次写完 N 条,reload 一次比 N 次 plugin 调更快 |
| 派生新文档(双语对照、outline 子文档) | **B 服务端生成** | 产物要进文档列表 |

`actionRouter` 在 `packages/app/src/features/office/bridge/` 落地,
对外暴露统一的 `executeAction(actionId, target)` 接口,UI 不感知路径差异
(只在动作 chip 上画"⚡ 实时"或"🔄 reload"小标识)。

### 4.1 路径 C — Proxy Plugin(30 行死桥)

#### 4.1.1 物理形态
- 包路径:`apps/desktop/src-tauri/resources/onlyoffice-plugin/asc.chayuan-bridge/`
- 三个文件,**写一次基本不变**:
  - `config.json`:声明 guid、`isVisual:false`、订阅 `onTargetPositionChanged`
  - `index.html`:空骨架,只 import `code.js`
  - `code.js`:**~30 行**(完整代码见 ADR-0006 §路线 C)
- `editorConfig.plugins.autostart` 注入此 guid,DS 启动即载入,用户无感

#### 4.1.2 Plugin 协议(永不动)
Plugin 只识别三种消息,全部由父窗口下发:

| `kind` | 转发到 | 用途 |
|---|---|---|
| `method` | `Asc.plugin.executeMethod(name, args, cb)` | 单次方法调用,带回调 |
| `command` | `Asc.plugin.callCommand(fn, isClose, isCalc)` | 在文档上下文跑 builder.js DSL,适合复合操作 |
| `event` | `Asc.plugin.attachEvent(name, cb)` | 订阅事件(选区变化、光标移动) |

**plugin 永远不发起业务逻辑,永远不调网络,永远不持有状态。**

#### 4.1.3 父窗口 Bridge(`pluginBridge.ts`)
```ts
// 业务用法,在 React 组件里像调本地函数:
const text = await bridge.method<string>('GetSelectedText');
await bridge.method('PasteText', [translated]);

// 复合操作走 callCommand,fn 序列化为字符串发到 plugin:
await bridge.command(function () {
  const oDocument = Api.GetDocument();
  const oRange = oDocument.GetRangeBySelect();
  oRange.SetBold(true);
});

// 事件订阅:
const off = bridge.on('onTargetPositionChanged', (pos) => {
  cursorStore.setPosition(pos);
});
```

实现细节:
- nonce 配对 reply,Promise 化封装,超时 5s 自动 reject
- DS iframe 与客户端跨 origin(127.0.0.1:动态端口 vs tauri://):postMessage 用 `'*'` 宽 origin,**消息内带 `chayuan` 命名空间字段做识别**,不签名(同源体系内)
- `bridge.command` 的 fn 用 `Function.prototype.toString()` 序列化,plugin 端用 `new Function('return (' + src + ')')()` 重建 —— **有 CSP 风险**,DS 默认允许,Embedded 模式下不破坏;External 模式若用户加了 strict CSP 需文档说明
- 选区变化高频事件(`onTargetPositionChanged`)在父窗口节流 100ms

#### 4.1.4 跨文档类型(word / cell / slide)
Plugin API 在三类文档下方法名/签名不一致,bridge 内做归一化:

| 抽象操作 | word | cell | slide |
|---|---|---|---|
| 取选区文本 | `GetSelectedText` | `GetSelectedRange().GetValue()` | `GetSelectedSlides()` 取标题/正文 |
| 加粗 | `Range.SetBold(true)` | `Range.SetBold(true)` | `ParaPr.SetBold(true)` |
| 替换 | `PasteText` | `Range.SetValue(text)` | `Shape.GetContent().GetElement(0).GetParagraph().SetText` |
| 查找替换 | `SearchAndReplace` | 遍历单元格 | 遍历 shape |

`pluginBridge` 暴露的高层 API 是统一的(`bold()` / `replaceSelection()` / `findReplace()`),
内部按 `editor.documentType` 分发。

#### 4.1.5 部署
- **Embedded**:compose 模板里把目录 mount 到 DS 容器 `/var/www/onlyoffice/documentserver/sdkjs-plugins/asc.chayuan-bridge/`,用户无感
- **External**:Settings 页加「安装察元桥接插件」按钮 → 后端 `/office/install-plugin` 产出 zip + 一行 `docker cp` 命令,用户一次性执行

### 4.2 路径 B — 服务端改写 + Reload

#### 4.2.1 适用场景与流程
触发条件:大批量 / 跨段落 / 需审计的操作。流程:

1. 前端调 `POST /office/docs/{id}/assistant/{revise|annotate|derive}`,body 含目标范围 + 动作
2. 后端走以下子流程:
   - `forcesave` 触发(走 `CommandService.ashx`),拿到最新二进制
   - 从 MinIO 拉最新版本 → `python-docx` 加载
   - 调 LLM(SSE 流式回前端,显示进度)
   - 按动作类型应用变更:
     - `revise`:原段落标 `w:del`,新段落标 `w:ins`(带 author=「察元助手」)
     - `annotate`:在目标段落注入 `w:comment`
     - `derive`:基于模板生成新 docx,作为独立 `office_document` 入文档列表
   - 写入 MinIO 新 key,**bump `office_document.doc_key`**,落 `office_document_version`
3. 后端 SSE 推 `event: ready, data: {new_doc_key, version_no}` 给前端
4. 前端 `<OfficeEditor>` 收到 → `editor.destroyEditor()` → 用新 config 重建 → 用户在 DS 原生「审阅」UI 看到所有变更,逐条 ✓/✗

#### 4.2.2 后端模块布局
`libs/chayuan-server/chayuan/server/office_authoring/`:
- `outliner.py`:python-docx 抽 outline + 段落 list,给前端 picker 用
- `revisor.py`:范围内 LLM 改写 → `w:ins`/`w:del` 注入(直接操作 lxml)
- `commenter.py`:目标段落注入 `w:comment` + 评论作者元数据
- `deriver.py`:派生文档(翻译副本、outline 子档、对照版)
- `forcesave.py`:封装 `CommandService.ashx` 的 forcesave/info/drop

#### 4.2.3 跨文档类型降级
| | docx | xlsx | pptx |
|---|---|---|---|
| 修订(ins/del) | ✅ python-docx + lxml | ❌ 无原生修订 → 降级为单元格批注 + 新建对照 sheet | ❌ → 降级为 slide notes 追加 |
| 评论 | ✅ `w:comment` | ✅ `openpyxl.comments.Comment` | ✅ `python-pptx` slide notes |
| 派生 | ✅ | ✅ | ✅ |

xlsx/pptx 的修订降级写法在 §4 子文档里详述,不在本 plan 内重复。

### 4.3 与 LLM 的协作

#### 4.3.1 C 路径(实时)
- 前端拿到选区后直接调 `/chat/v2/chat`,SSE 流式回流
- 显示在 `<DocAssistantPanel>` 对话流
- 用户点「应用」→ bridge 调 `PasteText` 或更复杂的 `callCommand`
- 不写 `office_assistant_action`(无版本、无审计需求)

#### 4.3.2 B 路径(reload)
- 助手发送时,`POST /office/docs/{id}/assistant/revise` body:
  ```json
  {
    "target": {"kind": "paragraphs", "ids": ["p_3", "p_4", "p_7"]},
    "action": "translate",
    "prompt": "翻译成英文",
    "model": "gpt-4o",
    "stream": true
  }
  ```
- 后端把目标段落原文 + prompt 拼 system+user 走 `/chat/v2/chat`,SSE 流式
- 完成后写入 docx → 写 `office_assistant_action`(action_id 供撤销/重放)
- 推送 `event: ready` 触发前端 reload

---

## 5. 账号统一

### 5.1 用户身份注入 DS
- 用户在客户端登录 `chayuan-server` → 拿 `access_token`;
- 打开文档时前端调 `/office/docs/{id}/config`,**后端**:
  1. 校验 `access_token` 拿 `user`;
  2. 鉴权:这文档对该用户可读/可写;
  3. 组装 `editorConfig.user = { id: f"{user.id}:{device_id}", name: user.display_name }`;
  4. 用 DS_JWT_SECRET 对整个 config payload 签 HS256;
  5. 文件 URL / callback URL 各自挂一次性短期 JWT(`exp=10min`,绑 `doc_id + user_id`)。
- DS 拿到合法 JWT 即认人,显示的协作光标/头像 = 察元用户。

### 5.2 协作场景(局域网/团队版)
- 多人同开一份文档:每人各自调 config,DS 按 `user.id` 区分 → 自动协作;
- 权限粒度:`permissions.edit/comment/download/print` 由 `chayuan-server` 按
  「文档.owner / 共享列表 / 知识库 grants」三层查询后下发。

### 5.3 不踩的坑
- 不要让前端自己签 JWT(secret 不可下发);
- 不要用用户名当 id(改名后协作历史断);
- 不要为每个 doc 重新发 secret,**全局一份**,轮换时停 DS 30s。

---

## 6. 一键部署(Embedded 模式)

### 6.1 客户端侧职责
1. **检测 Docker**:`docker version` 调通 → 可用;不调通 → 进 `docker-missing` 状态;
2. **加载内置镜像**(避免首次几个 G 的下载):
   - 安装包 `apps/desktop/src-tauri/resources/office-images/*.tar.zst` 在 Tauri build
     时通过 `extraResources` 打入;
   - 首次部署时执行 `docker load -i <tar>`(SSE 推进度);
3. **生成 compose 文件**:把 `docker-compose.template.yml` 用动态变量(随机端口、
   随机密码、JWT secret、用户数据卷路径)渲染成 `~/.chayuan/office/docker-compose.yml`;
4. **拉起服务**:`docker compose up -d`,然后 ping `/healthcheck` 直到 ready 或 90s 超时;
5. **写配置回写**:把端口、JWT secret、MinIO endpoint/credentials 写到 `chayuan-server`
   的运行时配置(走 `/admin` 接口,落 `office_runtime_config` 表)。

### 6.2 docker-compose 关键骨架(模板)
```yaml
services:
  documentserver:
    image: onlyoffice/documentserver:8.x
    environment:
      JWT_ENABLED: "true"
      JWT_SECRET: "${DS_JWT_SECRET}"
      DB_TYPE: postgres
      DB_HOST: postgres
      DB_NAME: ds
      DB_USER: ds
      DB_PWD: ${DS_DB_PWD}
      REDIS_SERVER_HOST: redis
      AMQP_TYPE: rabbitmq
      AMQP_SERVER_URL: amqp://guest:guest@rabbitmq
    ports: [ "${DS_PORT}:80" ]
    volumes:
      - ${DATA_DIR}/ds/data:/var/www/onlyoffice/Data
      - ${DATA_DIR}/ds/log:/var/log/onlyoffice
      - ${PLUGIN_DIR}:/var/www/onlyoffice/documentserver/sdkjs-plugins/asc.${PLUGIN_GUID}
    depends_on: [postgres, redis, rabbitmq]

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ds
      POSTGRES_PASSWORD: ${DS_DB_PWD}
      POSTGRES_DB: ds
    volumes: [ "${DATA_DIR}/pg:/var/lib/postgresql/data" ]
    # 初始化脚本里再 createdb chayuan & 给察元后端用

  minio:
    image: minio/minio:RELEASE.2024-XX
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PWD}
    ports: [ "${MINIO_PORT}:9000", "${MINIO_CONSOLE_PORT}:9001" ]
    volumes: [ "${DATA_DIR}/minio:/data" ]

  redis:
    image: redis:7-alpine
    volumes: [ "${DATA_DIR}/redis:/data" ]

  rabbitmq:
    image: rabbitmq:3-alpine
    volumes: [ "${DATA_DIR}/rabbitmq:/var/lib/rabbitmq" ]
```

> RabbitMQ 是 DS 的硬依赖,跑不掉。包体里已经默认要带 5 个镜像。

### 6.3 进度模型(SSE)
```
event: phase    data: {"name":"loading-images","total":5,"done":2}
event: log      data: {"line":"Loaded image onlyoffice/documentserver:8.x"}
event: phase    data: {"name":"compose-up"}
event: phase    data: {"name":"waiting-health","service":"documentserver","attempts":3}
event: done     data: {"deployedAt":"2026-04-27T10:23:01Z","ports":{...}}
```

### 6.4 卸载 / 数据清理
- `Settings > 办公 > 卸载`:停容器、可选保留卷;
- 「彻底卸载」:`docker compose down -v` + `rm -rf $DATA_DIR`。

---

## 7. 跨平台兼容矩阵

| 平台 | Embedded(Docker) | External | 说明 |
|---|---|---|---|
| **Linux x64** | ✅ 推荐 | ✅ | docker-ce 通常已装 |
| **Linux arm64** | ⚠️ 镜像需 arm64 多架构(documentserver 8.x 已支持) | ✅ | Apple Silicon / 国产 ARM |
| **macOS arm64** | ✅(需 Docker Desktop / Colima / OrbStack) | ✅ | 推荐 OrbStack,启动快 |
| **macOS x64** | ✅ | ✅ | |
| **Windows x64** | ✅(需 Docker Desktop + WSL2) | ✅ | 安装时引导用户开启 WSL2 |
| **Windows arm64** | ⚠️ Docker Desktop on Win arm64 仍预览 | ✅ | v1 文案标"实验性" |

### 安装包大小预估
| 模块 | 大小 |
|---|---|
| Tauri 客户端壳 | ~15 MB |
| OnlyOffice DS 镜像 | ~1.6 GB |
| Postgres 镜像 | ~250 MB |
| MinIO 镜像 | ~150 MB |
| Redis 镜像 | ~30 MB |
| RabbitMQ 镜像 | ~120 MB |
| **合计(zstd 压缩后)** | **~1.4–1.6 GB** |

> 安装包**离线版**1.6 GB 是必须接受的。**在线版**可不打镜像,首次部署联网拉取
> (国内做镜像加速代理),客户端体积压回 30 MB 以内。建议**两个分发渠道并存**:
> `chayuan-setup-online.exe` 30 MB / `chayuan-setup-offline.exe` 1.6 GB。

---

## 8. 安全 / 健壮性

- **JWT secret 全局唯一**,首次部署随机生成,只存后端;轮换时停 DS 容器替换 env 重启;
- **callback / file URL 一次性短 token**(10 min,绑 doc + user),防越权;
- **postMessage origin 校验** + nonce,防恶意页面伪造编辑指令;
- **MinIO bucket 私有**,DS 通过预签名 URL 拉文件,不直连;
- **DS 升级**:容器化升级简单,但要先 `forcesave` 所有在线文档;
- **崩溃恢复**:DS 自带 30s autosave + 强制保存,容器重启丢失最多最近 30s 操作;
- **杀软误报**:Windows 上要给 Tauri 主程序签代码签名证书,避免被 Defender 隔离;
- **大文档 OOM**:DS 默认上限 100MB,后端 upload 时拒绝 >50MB(可配置)。

---

## 9. 难度评估

按子项目拆,1 = 一两天,5 = 两周以上。**按路线分别汇总。**

### 9.1 Route A — 轻量接入

| 子项目 | 难度 | 主要风险 |
|---|---|---|
| 后端 `/office/docs` CRUD + 数据表 `office_document(_version)` | 2 | 标 CRUD + JWT,常规活 |
| `editorConfig` 签发 + callback 6 状态机 | 3 | OnlyOffice 回调状态机要读文档,但只需在 A 实现一次 |
| 后端 `/office/settings/external` + secrets 落库 | 1 | 一个 PATCH + 一张表,小活 |
| 前端 `/office` 列表 + 编辑器嵌入(iframe + DS SDK) | 2 | DS SDK 文档齐全 |
| 设置页 ExternalDsForm(URL + JWT secret + 联通性测试) | 1 | 复用 ModelPlatform 设置弹窗形态 |
| 简化版 `/office/health`(只 ping 外部 DS) | 1 | 一行 httpx |
| 跨平台联调(只验客户端 webview × 3 OS) | 2 | DS 由用户自带,不进 QA 矩阵 |

**Route A 小计:**
- 单人 **1.5–2 周**(含写测试)
- 双人 **~1 周**
- 风险点集中在 callback 6 状态机 + JWT 签发,文档充分

### 9.2 Route B — 深度集成(在 A 基础上叠加)

> 本节难度按 [ADR-0006](../adr/0006-onlyoffice-integration-routes.md) 的 **C+B 混合 API 路线**估算。
> 历史上"业务插件"(API 路线 D)的 4 分项已替换为"Proxy Plugin + 服务端改写"两项,合计 3+3 难度,
> 但工程链路更解耦、可单测,实际 v1 工期不增加。

| 子项目 | 难度 | 主要风险 |
|---|---|---|
| **Proxy Plugin(30 行死桥)+ 父窗口 bridge(`pluginBridge.ts`)** | **3** | postMessage 协议 + nonce 配对 + word/cell/slide 三类归一化;`callCommand` 的 fn 序列化要在 External 模式下兼容用户自家 DS 的 CSP |
| **服务端改写 `office_authoring/`(python-docx/openpyxl/python-pptx)** | **3** | `w:ins`/`w:del`/`w:comment` 直接操作 lxml,有现成 snippet 但要写跨文档类型适配;段落 stable ID(防 LLM 改写后 index 偏移)需要设计 |
| `actionRouter` 父窗口分发层 + 动作 chip "⚡实时/🔄reload" 标识 | 2 | UX 一致性靠 router 集中,实现简单 |
| 文档助手对话 UI + LLM 联调 + 历史动作抽屉 | 2 | 复用现有 chat 链路 |
| 助手相关后端路由 `/assistant/{revise,annotate,derive}` + `office_assistant_action` 表 | 2 | 标活 |
| 跨文档类型(word/cell/slide)C+B 双路径适配 | 3 | xlsx/pptx 没原生修订,B 路径降级为批注/slide notes;C 路径方法名不同,bridge 内归一化 |
| Plugin 自动安装(External 模式 zip + `docker cp` 引导) | 1 | 一个 zip 端点 + Settings 按钮 |
| **一键部署 / docker compose 编排** | **4** | **跨平台 Docker 检测、镜像离线打包、端口冲突、首启 90s 超时,坑多** |
| `/office/deploy/*` SSE 进度路由 | 3 | 长任务管理,需要进度回放 |
| 部署向导 UI(DeployHero / DeployProgress / HealthPopover) | 3 | UX 流程多状态,需要每步都能恢复 |
| 多服务健康聚合 + 状态点 30s 轮询 + 自愈 `--repair` | 2 | 标活 |
| 安装包打镜像 / 增量更新 / 代码签名 | **5** | 1.6 GB 包体的 CI/CD、增量更新、代码签名都要新搭 |
| 跨平台联调 (Win/Mac/Linux × 在线/离线) | **5** | 6 个矩阵全跑通,QA 投入最大 |
| 多人协作字段(`device_id` 后缀、permissions 三层查询) | 2 | A 模式下也可以做,但只在 B 路径里有真实回归场景 |

**Route B 增量小计:**
- 单人在 A 基础上 **再 6–8 周**(不含离线包/CI)
- 双人 **再 4 周** 可达 v1 可用
- **离线包打通整个发布管线再 2 周**(可独立外包给 DevOps)

### 9.3 总体
- **只做 A**:1.5–2 周即可上线企业自带 DS 场景,验证产品价值
- **A → B**:总计 8–10 周(单人)/ 5 周(双人) + 2 周离线包
- **强烈建议先合 A,再用线上反馈决定 B 的优先级和范围**(尤其"助手回写"和"一键部署"哪个更值得先做)

---

## 10. 实施切片(里程碑)

> 分两阶段:**Phase A** 是 Route A 的最小可发版集合(M0–M3);**Phase B** 是 Route B
> 的扩展集合(M4–M7),只在 Phase A 上线 + 反馈收集后启动。
> 两阶段中间允许停下来重新评估 B 的范围/优先级,不必无缝衔接。

---

### Phase A — Route A(轻量接入,1.5–2 周)

#### M0 — 技术验证(2 天)
- 本地起一个 DS 容器(开发期手动,不进客户端),前端裸页面嵌 iframe,用本地 `editorConfig` 签 JWT 打开 docx;
- **退出条件**:能在浏览器里打开 docx + 输入文字 + DS 回调到本地后端日志可见。

#### M1 — 后端骨架(3 天,与 M2 并行)
- migration `0005_office`(`office_document` + `office_document_version`);
- `/office/docs` CRUD、`/config` 签发、`/callback` 处理 6 种状态、`/file` 预签名重定向;
- `/office/settings/external` PATCH + secrets 表存 DS URL/JWT secret;
- 简化版 `/office/health`(只 ping 外部 DS);
- MinIO bucket `chayuan-office` 创建脚本;
- 单测覆盖 callback 状态机。

#### M2 — 前端骨架(3 天)
- 路由 `/office`、Sidebar 入口、`OfficeWorkspace` + `DocList` + `OfficeEditor`;
- 创建 / 上传 / 打开 / 自动保存全链路通(无助手、无回写桥);
- `Settings > 办公` 页:`ExternalDsForm`(URL + JWT secret + 联通性测试,复用 ModelPlatform 设置弹窗形态);
- Sidebar 入口右侧状态点(只有"已配置/未配置/失联"三态)。

#### M3 — 联调与发版(2 天)
- 验收 §11 中标记 [A] 的项;
- Win/Mac/Linux × 各跑一遍 iframe 嵌入(用一个统一的测试 DS 实例);
- 文档:Settings 设置教程 + 自建 DS 链接。

**Phase A 退出条件:**用户只需填一个 DS URL 就能在客户端里开 docx、编辑、保存,
文件元数据进 chayuan-server,身份是察元用户。**到此即可单独发版。**

---

### Phase B — Route B(深度集成,在 A 基础上叠加 6–8 周)

#### M4 — 文档助手(1.5 周,按 [ADR-0006](../adr/0006-onlyoffice-integration-routes.md) C+B 路线)

拆成三个**可并行**的子任务:

**M4.1 — Proxy Plugin + 父窗口 bridge(C 路径,3 天)**
- `apps/desktop/src-tauri/resources/onlyoffice-plugin/asc.chayuan-bridge/`
  下三个静态文件(`config.json` / `index.html` / `code.js` ~30 行,见 §4.1)
- `packages/app/src/features/office/bridge/pluginBridge.ts`:
  - postMessage 协议 + nonce 配对 + Promise 化 `bridge.method/command/on`
  - word/cell/slide 三类高层 API 归一化(`bold()` / `replaceSelection()` / `findReplace()`)
- 后端 `/office/install-plugin` 端点产出 zip(External 模式安装用)
- 单测:Vitest mock postMessage 通道,验 bridge 协议正确

**M4.2 — 服务端改写 `office_authoring/`(B 路径,4 天)**
- `outliner.py` / `revisor.py`(`w:ins`/`w:del`)/ `commenter.py`(`w:comment`)/ `deriver.py` / `forcesave.py`
- 路由 `/office/docs/{id}/assistant/{revise,annotate,derive}` + `office_assistant_action` 表
- 段落 stable ID 设计(防 LLM 改写后 paragraph index 偏移)
- xlsx/pptx 降级实现(批注 / slide notes)
- 集成测:用真实 docx 验 ins/del/comment 写入正确

**M4.3 — UI + actionRouter 整合(2 天)**
- `<DocAssistantPanel>`(对话流 + 目标范围 picker + 动作 chip)
- `actionRouter.ts` 按操作类型分发到 C 或 B
- 动作 chip 上画"⚡ 实时"/"🔄 reload"小标识
- LLM 流式接入复用 `/chat/v2/chat`
- reload 触发:`editor.destroyEditor()` + 用新 `doc_key` 重建

- **此 M 也可在 Phase A 之后单独发**,作为"深度集成第一步":即使 DS 仍是外部的,
  Proxy Plugin 可由用户一次性 mount 到外部 DS 的 `sdkjs-plugins/`,即获 C 路径全能力;
  B 路径无需任何 DS 侧改动。

#### M5 — 一键部署(1.5 周)
- `office_runtime` 模块:Docker 检测、镜像加载、compose 渲染、健康聚合(扩 5 服务);
- `/office/deploy/*` SSE 路由;
- `<DeployHero>` / `<DeployProgress>` UI;
- 模式切换器:Settings 里 External/Embedded 二选一,Embedded 入口在此 M 才放开。

#### M6 — 探活 / 卸载 / 离线包(1 周)
- 状态点扩为 4 色 + 30s 轮询(B 模式)/ 复用 A 的简化版(External 模式);
- `Settings > 办公 > 卸载` + 日志查看(`docker logs` 流式);
- 离线镜像打包脚本(产物在 `apps/desktop/src-tauri/resources/office-images/`);
- `--repair` 自愈路径。

#### M7 — 跨平台联调与签名(1.5 周)
- Win/Mac/Linux × 在线/离线 6 矩阵 QA;
- 代码签名 + 公证(macOS notarize、Windows Authenticode);
- 文档与发布说明 + 升级路径(从 A 升到 B 时不丢文档)。

**Phase B 累计:6–8 周(单人 ~ 10 周)。**

---

### 累计
- 走完 A:**1.5–2 周**(单人)/ ~1 周(双人)
- 走完 A+B:**8–10 周**(单人)/ ~5 周(双人) + 2 周离线包发布管线

---

## 11. 验收标准(给 QA / Reviewer)

### Route A — 必须全部通过才能发版

- [A] 全新 macOS / Win / Linux 装客户端 → 登录 → `Settings > 办公` 填外部 DS URL + JWT secret → 联通性测试通过;
- [A] 进入「察元办公」→ 文档列表加载 → 上传一份本地 docx → 在编辑器内打开;
- [A] 创建一份 docx → 输入"你好" → 关闭客户端 → 重启 → 文档内容仍在(callback 6 状态机正确);
- [A] 文件 / callback URL 各自一次性 token,过期或重放被拒;
- [A] 错填 DS URL → 状态点显示"未配置/失联",可在设置页一键复检;
- [A] 删除文档 → 进入回收站,30 天后真删 MinIO object。

### Route B — Phase B 完成后追加

- [B] 全新干净环境 → 「一键部署」→ 90s 内 ready;
- [B] 选中一段中文 → 助手输入「翻译成英文」→ 流式回复 → 点「替换选区」→ 内文被替换 + 可 Ctrl+Z 回退;
- [B] 两台机器同账号打开同一文档 → 实时看到对方光标 + 输入;
- [B] 状态点反映服务真实状态:停掉 redis 容器后,30s 内变黄;
- [B] 卸载客户端 → 容器停止 + 卷可选保留;
- [B] 1.6 GB 离线包在断网环境完成全流程;
- [B] 从 A 模式升级到 B 模式 → 已有文档元数据/MinIO object 不丢、可继续打开。

---

## 12. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| OnlyOffice 商业 license 限制(并发用户、文档大小) | 生产部署受限 | v1 用社区版,文档明确商业版升级路径 |
| 1.6GB 包体过大,用户拒装 | 转化率 | 默认在线版,离线版仅企业渠道 |
| Docker Desktop Win/Mac 收费政策变化 | 商业用户合规 | 在 Win/Mac 上提供 Colima/OrbStack/Rancher Desktop 备选指引 |
| DS 升级 break callback 协议 | 已有文档无法保存 | 锁定 DS 镜像 tag,升级前 staging 验证 |
| 自定义 plugin 在 DS 升级后 API 不兼容 | 助手功能失效 | M3 用 DS 8.x LTS,plugin 加版本探测降级提示 |
| Win arm64 Docker 不稳 | 安装失败率 | v1 标实验性,允许 External 模式回退 |
| postMessage 在跨 origin + Tauri webview 下行为差异 | 选区桥失灵 | 在 Tauri webview 注入预设 origin 白名单,M0 必须验证 |

---

## 13. 待审查决策(请 Reviewer 表态)

### 路线相关(本次新增,优先回答)

0. **是否同意"先 A 后 B"分两阶段?** 建议同意,A 单独有交付价值,B 按反馈再启动;
   - 0a. **Route A 是否要在助手位置留占位 UI?**(只能"复制选区到剪贴板,不写回")
     建议**不留**,避免给用户"已支持"的错觉,Phase B 上线前 DocAssistant 抽屉整体隐藏;
   - 0b. **Phase A 的 DS 是否提供官方测试实例?** 建议**不提供**,只在文档里给 docker 单行命令样例,
     避免承担运维责任。

### 沿用原决策

1. **路由放在察元侧栏第几位?** 建议 `/tools` 之后、`/space` 之前;
2. **DS 数据库与察元 PG 共用还是独立?**(仅 Route B 相关)建议同实例不同 db;
3. **离线包是否同时分发?**(仅 Route B 相关)建议是,但只走企业渠道,社区版默认在线;
4. **多人协作 v1 是否启用?**(Route A 由外部 DS 决定;Route B)建议**默认关闭**;
5. **助手"全文"操作的上限?**(仅 Route B)建议 5 万字以内直传 LLM,>5 万字走 RAG 切片;
6. **是否要做"文档 → KB 导入"双向通道?** v1 单向(KB 文件 → 在 Office 中打开)即可;
7. **支持的文件类型?** v1 只支持 docx/xlsx/pptx;
8. **WebSocket 还是 SSE 给助手流?**(仅 Route B)建议 SSE。

---

## 附:相关参考

- OnlyOffice Document Server API: https://api.onlyoffice.com/docs/docs-api/
- Plugins SDK: https://api.onlyoffice.com/docs/plugin-and-macros/
- JWT integration: https://api.onlyoffice.com/docs/docs-api/additional-features/signature/browser/
- Docker compose 官方示例: https://github.com/ONLYOFFICE/docker-onlyoffice
- 察元已有相关文档:`docs/plans/openclaw-lifecycle.md`(同形态参考)、
  `docs/RECONSTRUCT.md`(monorepo 架构)
