# OpenClaw 模块全生命期管理 — 设计与实施规划

> 目标:在客户端左侧新增 OpenClaw 入口,把 OpenClaw 这个本地 AI Agent 守护进程
> 的「安装 → 配置 → 启动 → 监控 → 技能(Skill)管理」全部收进一个页面里。
> 用户不必再切到终端跑 `openclaw onboard`,所有运维动作都能在客户端里点完。
>
> 受众:阅读后能直接落地实施;欢迎在审查阶段就驳回方向性问题(比如"不要装在
> 后端、要纯客户端实现"),后面再讨论具体接口字段。

---

## 0. 背景与术语

### OpenClaw 是什么
OpenClaw 是一个开源、本地优先的 AI 个人助手框架(类比 "npm for AI agents")。
形态是**一个常驻 daemon 进程**,你给它一份 `SOUL.md` 描述人格 / 技能配置,
它就把 LLM 包装成能执行任务、记忆跨会话、收发消息的助手。

关键概念:
- **CLI 二进制**:`openclaw`,管理一切;主要子命令:
  - `openclaw onboard` — 引导式首次配置
  - `openclaw start / stop / status` — 守护进程控制
  - `openclaw skills search / install / uninstall / list` — 技能管理
  - `openclaw config` — 读写配置文件
- **SOUL.md**:Agent 的灵魂配置(模型、性格、默认技能列表),放 `~/.openclaw/SOUL.md`
- **ClawHub**:官方技能市场,类似 npm registry,~10k+ 技能
- **Skill**:类似 npm 包,有 `SKILL.md` 描述清单 + 可执行实现

### 为什么放进客户端
1. 用户群是非工程师 — 终端命令行门槛高
2. 客户端已经有 KB/MCP/Tools/Marketplace 这一系列「卡片化运维入口」,OpenClaw
   是同一类形态(卡片 + 状态指示 + 一键操作),延续设计语言成本最低
3. OpenClaw 的 CLI 已经把所有能力暴露好了,我们只是套层壳 — 工作量可控

---

## 1. 整体架构

```
┌──────────────────────────────────────────────────────────┐
│ 客户端 (chayuan-client / Tauri + React)                   │
│                                                          │
│   /openclaw 页面                                          │
│   ├─ Sidebar 入口(MCP / Tools 同级)                      │
│   ├─ OpenclawPage.tsx ─ 状态分支主容器                     │
│   │   ├─ NotInstalledHero  — 一键安装向导                 │
│   │   ├─ StoppedHero       — 单按钮启动                   │
│   │   └─ RunningTabs       — 4 个 tab 主面板             │
│   │       ├─ Overview      — 健康指标 + 日志尾巴          │
│   │       ├─ Configuration — SOUL.md 编辑器               │
│   │       ├─ Skills        — 已装 / 搜 ClawHub / 安装    │
│   │       └─ Logs          — 全量日志 SSE 流              │
│                                                          │
│   API 客户端层(packages/api/src/openclaw.ts)             │
└────────────────────────┬─────────────────────────────────┘
                         │ HTTPS / SSE
                         ▼
┌──────────────────────────────────────────────────────────┐
│ 服务端 (chayuan-server / FastAPI)                          │
│                                                          │
│   /api/v1/openclaw/* 路由                                 │
│   └─ 薄壳:把 RESTful 调用转译成 openclaw CLI 调用          │
│                                                          │
│   subprocess_runner.py — 唯一的子进程封装:                 │
│       • argv list,绝不 shell=True                         │
│       • 子命令 + 参数白名单                                │
│       • 超时、stdout/stderr 捕获、退出码                   │
│       • daemon 启动用 start_new_session=True / DETACHED   │
│                                                          │
│   pid 持久化:<CHAYUAN_ROOT>/data/openclaw.pid              │
└────────────────────────┬─────────────────────────────────┘
                         │ subprocess.Popen
                         ▼
┌──────────────────────────────────────────────────────────┐
│ openclaw 守护进程(用户机器上的本地服务)                   │
│   • 读 ~/.openclaw/SOUL.md                                │
│   • 暴露自己的 gateway HTTP / IPC 给系统其它部分用         │
└──────────────────────────────────────────────────────────┘
```

### 不放在 Tauri 主进程的原因
理论上 Tauri rust 后端能直接 spawn 子进程,但:
1. 我们的 web 模式(`apps/web`)同样需要这个功能,客户端跑在浏览器里,
   不可能直接 spawn 系统进程
2. 服务端 (FastAPI) 已经有完整的鉴权、审计、SSE 设施,直接复用
3. 集中在服务端便于多用户场景下做权限隔离(虽然 v1 不会真用)

---

## 2. 数据模型 / API 契约

### 2.1 后端路由(全部 prefix `/api/v1/openclaw`,鉴权同 MCP/Tools)

| 方法 | 路径 | 说明 | CLI 后台命令 |
|------|------|------|--------------|
| `GET` | `/status` | 返 `{installed, version, daemon_running, pid, uptime_seconds}` | `which openclaw` + `openclaw --version` + `openclaw status --json` |
| `POST` | `/install` | 安装 OpenClaw,SSE 流式吐安装日志 | OS 自动选:macOS `brew`、Linux `pipx`、Windows `winget`,失败 fallback `curl ... \| sh` |
| `POST` | `/uninstall` | 卸载(危险,需二次确认 token) | 反向命令 |
| `GET` | `/config` | 返 SOUL.md 文本 + `~/.openclaw/config.toml`(如有) | 读文件 |
| `PUT` | `/config` | 写 SOUL.md,带 lock 防并发改;改前自动备份 `.backup-<ts>` | 写文件 |
| `POST` | `/start` | 启动 daemon | `openclaw start` |
| `POST` | `/stop` | 停止 daemon | `openclaw stop` |
| `POST` | `/restart` | 等价 stop + start;客户端单按钮触发 | 顺序调用 |
| `GET` | `/logs/tail?lines=200` | 返最近 N 行 | tail `~/.openclaw/logs/openclaw.log` |
| `GET` | `/logs/stream` | SSE 持续推 | `tail -f` 协程 |
| `GET` | `/skills/installed` | 已装技能 list | `openclaw skills list --json` |
| `GET` | `/skills/search?q=&page=` | 搜 ClawHub | `openclaw skills search <q> --json` |
| `POST` | `/skills/install` | body `{name, version?}`,SSE 装包过程 | `openclaw skills install <name>` |
| `POST` | `/skills/uninstall` | body `{name}` | `openclaw skills uninstall <name>` |
| `GET` | `/skills/{name}/manifest` | 返 SKILL.md 文本(用于安装前预览) | `openclaw skills show <name>` |

### 2.2 关键响应类型(给前端 TypeScript 镜像用)

```ts
interface OpenclawStatus {
  installed: boolean;
  version: string | null;          // null 当未装
  daemon_running: boolean;
  pid: number | null;
  uptime_seconds: number | null;   // null 当未运行
  bin_path: string | null;         // 安装位置(诊断用)
  config_path: string | null;      // SOUL.md 解析路径
}

interface SkillSummary {
  name: string;
  version: string;
  description: string;
  publisher: string;
  verified: boolean;               // ClawHub 验证标记
  installed: boolean;              // search 接口里也带,合并去重用
  install_count?: number;          // 仅搜索结果有
}

interface SkillDetail extends SkillSummary {
  readme: string;                  // SKILL.md 全文
  permissions: string[];           // 列出该技能要的权限,装前提示
  dependencies: { name: string; version: string }[];
}
```

### 2.3 子进程封装设计要点

```python
# subprocess_runner.py 概念实现
ALLOWED_SUBCOMMANDS = {
    "--version", "status", "start", "stop",
    "skills",  # 后面跟 list / search / install / uninstall / show
    "config",
}

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")  # 防注入

def run_openclaw(args: list[str], *, timeout=60, sse_handler=None) -> RunResult:
    if not args or args[0] not in ALLOWED_SUBCOMMANDS:
        raise ValueError(f"disallowed subcommand: {args[:1]}")
    # 进一步参数级 white-list:skills install/uninstall 的 name 必须正则匹配
    if args[:2] in (["skills", "install"], ["skills", "uninstall"]):
        name = args[2] if len(args) > 2 else ""
        if not SKILL_NAME_RE.match(name):
            raise ValueError(f"illegal skill name: {name!r}")
    # 永远 argv list,永远不 shell=True
    proc = subprocess.Popen(["openclaw"] + args, stdout=PIPE, stderr=PIPE, ...)
    ...
```

**daemon 启动**用 `start_new_session=True` (POSIX) / `creationflags=DETACHED_PROCESS` (Windows),
这样我们的服务重启 / 崩溃不会带飞 daemon。pid 写到
`<CHAYUAN_ROOT>/data/openclaw.pid`,`/status` 用 `os.kill(pid, 0)` 配合
`openclaw status --json` 双重判活。

---

## 3. 前端 UI 设计

### 3.1 状态分支(主容器决定展示哪一支)

```
useQuery('openclaw.status', { refetchInterval: 5000 }) ──┐
                                                         │
              ┌──────────────────────────────────────────┤
              ▼                                          ▼
       installed: false                          installed: true
              │                                          │
              ▼                                          ▼
     <NotInstalledHero/>                        ┌────────┴────────┐
                                                 ▼                 ▼
                                    daemon_running: false   daemon_running: true
                                                 │                 │
                                                 ▼                 ▼
                                          <StoppedHero/>     <RunningTabs/>
```

### 3.2 NotInstalledHero
单卡片,显示:
- OpenClaw 介绍 + 官方 logo(本地 SVG)
- 检测到的 OS,推荐安装方式徽标(如 macOS → brew)
- 一个大按钮「一键安装」,点击 → 弹出确认对话框(显示要执行的命令)→
  POST `/install`,UI 切换为 `<InstallingProgress/>`
- 折叠:「我已经手动装了,扫一下」按钮(强制 status refetch)

### 3.3 InstallingProgress
- 顶部进度条(不确定模式,纯视觉)
- 中间 SSE 日志面板(等宽字体,自动滚底,失败行红字)
- 底部「取消」按钮(POST 一个 abort 请求让服务端 kill 子进程)
- 完成后自动切到 `<StoppedHero/>`

### 3.4 StoppedHero
- 状态标签 「已安装 vX.Y.Z · 守护进程未运行」
- 主按钮「启动」 → POST `/start`,即时 toast,然后 status 自动跳到 running
- 次按钮「卸载」 → 二次确认对话框
- 一段简短提示:「启动后 OpenClaw 会在 27121 端口暴露 gateway,你的其它工具
  (KB / Chat 等)将能看到它的技能」

### 3.5 RunningTabs(主战场)

#### Tab: Overview
左半屏:
- 状态卡(运行中绿点 + 版本 + uptime + pid)
- 资源指标小条:CPU / 内存 / 已装技能数(指标从 `/status` 取,看 OpenClaw 暴露什么)
- 「重启 / 停止」两个按钮

右半屏:
- 「最近 50 行日志」尾巴,SSE 实时推
- 「查看完整日志」按钮 → 跳到 Logs tab

#### Tab: Configuration
- SOUL.md 编辑器 — v1 用 `<Textarea>` + 行号(用 CSS counter,不引 Monaco,
  保持包体积):字数计 + 语法着色用 shiki(已经在用了)
- 顶部按钮:「保存」(PUT `/config`)、「恢复上次备份」(显示 backup 列表下拉)
- 保存成功后弹「需要重启 daemon 才能生效」,提供「立即重启」按钮

#### Tab: Skills
布局对齐 KbBoard:
- 顶部:搜索框(实时搜 ClawHub)+ Tab 切换「已安装 / 推荐 / 全部」
- 卡片网格:每张 SkillCard 显示
  - 名称 + 版本 + verified 徽标(蓝勾)
  - publisher + install_count + ⭐
  - 描述 1-2 行
  - 卡片右下角按钮:已装 → 「卸载」红色;未装 → 「安装」蓝色
- 点卡片 → 抽屉打开 `<SkillDetailDrawer/>`:
  - SKILL.md 全文(shiki 渲染 markdown)
  - 「需要的权限」chip 列表(高危权限 — 文件系统写、网络出口 — 高亮黄色)
  - 底部安装按钮 + 一段安全提示:「ClawHub 早期约 20% 上传包含恶意行为(数据
    回传 / 凭据窃取),只装 verified publisher 的技能并审 SKILL.md」

底部 footer 当任一安装/卸载在飞:
- 横向进度条(确定模式 if 服务端给百分比,否则 indeterminate)
- 「取消」按钮(POST `/skills/abort`,真有意义的话)

#### Tab: Logs
- 全屏日志面板,SSE 持续推
- 顶部:级别 filter(error / warn / info / debug)+ 关键词搜索 + 「下载日志包」
- 底部:暂停 / 继续滚屏开关、清屏按钮

### 3.6 Sidebar 集成

`/work/chayuan-client/packages/app/src/features/shell/Sidebar.tsx`:
```tsx
// 导入
import { Cpu } from 'lucide-react';

// NAV_ITEMS 加一条(放 MCP / Tools 之间,概念都是「Agent 能力」)
{ to: '/openclaw', labelKey: 'nav.openclaw', icon: Cpu },
```

`page-registry.tsx` 加路由,`tab-titles.ts` 加默认标题,
i18n 加 `nav.openclaw: 'OpenClaw'`(英文同) / `'OpenClaw 助手'`(中文)。

### 3.7 顶栏小型状态指示器(可选,v1.5 加)
`OpenclawStatusPill.tsx` 放在客户端顶栏右侧,小圆点 + 状态文字;hover 显示
版本 + uptime;点击直接跳 `/openclaw` 页。让用户在任意页面都能感知 daemon
是否活着。

---

## 4. 安全 / 健壮性考量

| 风险 | 防御 |
|------|------|
| Shell 注入(skill 名) | 正则白名单 + argv list,绝不 `shell=True` |
| 子进程僵尸 | `start_new_session` + 启动时把 pid 写文件,服务重启时 reconcile |
| 长任务超时 | 安装 / 启动给 5 min 超时;搜索 / status 给 10s |
| 恶意技能 | SkillDetailDrawer 强制展示权限清单,verified 不验则黄色警告 |
| 鉴权穿透 | 整路由前 `Depends(require_auth_enabled())`,不允许匿名控制系统进程 |
| 配置文件并发改 | PUT `/config` 用 fcntl/msvcrt 文件锁,带 ETag 防 lost-update |
| 卸载误操作 | 二次确认 token(uuid 后端生成 → 前端 5s 内回传) |
| 网络代理 | `/install` 检测系统代理(http_proxy / HTTPS_PROXY)直接透传给子进程 |

---

## 5. 实施切片(里程碑)

| 切片 | 工时估算 | 内容 | 价值 |
|------|----------|------|------|
| **M1 骨架** | 0.5 天 | Sidebar 入口 + 路由 + status 接口 + NotInstalledHero / StoppedHero / RunningTabs 三种空壳 + 5s 轮询 | 用户能看到入口,知道装没装 |
| **M2 安装/启停** | 1 天 | `/install` (SSE) `/start /stop /restart` + 对应 UI + InstallingProgress 流式日志 | 单点闭环:从零到 daemon 跑起来 |
| **M3 SOUL.md 编辑** | 0.5 天 | `/config` GET/PUT + Textarea 编辑 + 备份列表 + 重启提示 | Agent 个性化 |
| **M4 技能搜索/安装** | 1.5 天 | 4 个 skill 接口 + SkillCard + SkillDetailDrawer + 安装进度 + verified 徽标 + 权限清单 | 真正能用起来:加技能 |
| **M5 日志体验** | 0.5 天 | `/logs/tail` `/logs/stream` SSE + Logs tab + Overview tab 嵌入 50 行尾巴 | 故障排查 |
| **M6 顶栏状态条** | 0.5 天(可选) | `OpenclawStatusPill` + 跨页面感知 | 软提醒 |

总计 4.5 ~ 5 工时日。**M1 + M2 + M4 是核心闭环**,先到就能 ship beta;
M3 / M5 / M6 都是增强,可灰度。

---

## 6. 跨平台兼容矩阵

| OS | 安装方式优先级 | 守护进程方式 | 日志路径 |
|----|----------------|--------------|----------|
| macOS | `brew install openclaw` → `pipx` → `curl ... \| sh` | launchctl(自身 CLI 处理) | `~/.openclaw/logs/openclaw.log` |
| Linux | `pipx install openclaw` → `curl ... \| sh` | systemd user(自身 CLI 处理) | 同 |
| Windows | `winget install openclaw` → 官方 .msi | sc.exe 服务 / DETACHED_PROCESS | `%APPDATA%\openclaw\logs\openclaw.log` |

服务端 OS 检测一次,缓存 5 min;失败/未识别时,前端按"无推荐安装方式"显示,
让用户去官网手动装。

---

## 7. 验收标准(给 QA / Reviewer)

- [ ] 全新机器(无 openclaw)打开 `/openclaw` 页 → 显示一键安装,点击装好 →
      自动出现"启动"按钮,点击 → 进入 RunningTabs
- [ ] daemon 跑一段时间后 kill -9 → 5s 内前端检测到并切回 StoppedHero
- [ ] 在 Skills tab 搜索 "web search" → 出现卡片,点装 → 进度跑完 → 已装 tab 出现该卡片
- [ ] 编辑 SOUL.md → 保存 → 提示重启 → 点重启 → daemon pid 变化
- [ ] 日志 SSE 中断网络 5s → 自动重连(EventSource 内置)→ 不丢行
- [ ] 卸载需要二次确认,确认后 status 立即回到 NotInstalled
- [ ] 卸载 / 安装失败时 stderr 完整展示在 InstallingProgress,不被吞
- [ ] 跨用户(若 Auth 开启)— A 用户的 daemon B 用户看不到(因为 daemon 是机器级,
      两人共用一个;v1 接受这个语义,把它写在文案里)

---

## 8. 风险与回退

1. **OpenClaw CLI 不稳 / 改 JSON 格式**:抓到 stdout 解析失败立即 logger.warning
   回退到 plain text;UI 上把卡片显示为"原始输出"(monospace 块)
2. **ClawHub 限流**:搜索接口给 cache + 1s debounce
3. **包体积膨胀**:页面所有组件用 React.lazy,仅在用户进 `/openclaw` 时才下;
   Monaco 不引(如果 v2 想引,单独切片评估)
4. **完全不可用时的退路**:整个模块用 `OPENCLAW_ENABLED` 后端开关 gate;关掉
   它,Sidebar 入口隐藏,接口全部 404,零运行时成本。

---

## 9. 待审查决策(请 Reviewer 表态)

1. **CLI vs HTTP**:OpenClaw 自身有没有 HTTP 控制 API?有的话改用 HTTP 比 CLI
   稳得多(不用解析 stdout),需要确认。
2. **多用户语义**:若 Auth 开启,我倾向「daemon 是机器级,所有用户共享一个」,
   不要尝试每用户一个 daemon(资源 & 端口冲突)。同意吗?
3. **SOUL.md 编辑器**:Textarea + shiki(已有依赖)够,还是值得引 Monaco
   (~2MB)?倾向前者。
4. **顶栏 StatusPill**:M6,非阻塞,要不要砍?
5. **ClawHub 直连 vs CLI 代理搜索**:CLI 走 `openclaw skills search` 是
   localhost-only 路径,稳;直连 `clawhub.io` 快但要 CORS。倾向 CLI。
6. **页面入口名字**:i18n key 是 `nav.openclaw`,中文显示「OpenClaw」还是
   「本地助手」?后者更好懂,但失去品牌识别。

请逐条回复(同意 / 否决 / 备注),收到后我以 M1 起步开干。
