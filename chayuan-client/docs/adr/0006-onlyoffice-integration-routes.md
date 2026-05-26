# ADR-0006 OnlyOffice 编辑器集成的 API 路线选型

- 状态:草案 / 待评审(2026-04-27)
- 关联计划:`docs/plans/chayuan-office-onlyoffice.md`
- 关联里程碑:察元办公 M0(暂未排期)

## 背景

察元办公模块要把 OnlyOffice Document Server(DS)嵌入到客户端,
让用户能在察元里打开/编辑 docx/xlsx/pptx,并在编辑器旁挂一个 LLM 助手做
"翻译/改写/审阅/查找替换/加粗/插入"等操作。

OnlyOffice 提供的能力被严格分到三个 API 层,**每一层能做什么、需要在哪
跑代码**完全不同 —— 集成路线的差异本质上是"用哪几层 API 的组合"。

本 ADR 不重复 plan 里的 UI / 数据模型 / 部署设计,**只对"API 集成路线"
单独做选型归档**,以便后续评审。部署路线(External vs Embedded)与本 ADR
正交,见 plan §0.5。

## OnlyOffice 三个 API 层(背景知识)

| 层 | 跑在哪 | 能力 | 关键入口 |
|---|---|---|---|
| **Editor SDK** | 父窗口(host page,即察元前端) | 保存/下载/插图/对比/协作通知/元数据,**不能动文字内容** | `DocsAPI.DocEditor(elementId, config)` 实例 + `events` |
| **Plugin SDK** | DS 内部 plugin iframe | **几乎全部文档操作**:选区/格式/查找替换/段落/表格/批注/光标 | `window.Asc.plugin.executeMethod / callCommand / attachEvent` |
| **服务端 / 后端** | chayuan-server 进程 | 改 docx 二进制(`python-docx` + lxml);通过 `CommandService.ashx` 控制 DS(forcesave/info/drop/meta) | python-docx / openpyxl / python-pptx + DS callback |

**关键约束:**
- `window.Asc.plugin` **只在 plugin iframe 内可用**,父窗口与 DS 主 iframe 都拿不到
- DS 主 iframe **跨 origin**,父窗口无 DOM 访问
- Plugin iframe **能 `window.top.postMessage`** 与父窗口通信(标准浏览器机制,DS 不限制)

## 候选路线

### 路线 A:**纯 Editor SDK(零插件,零服务端改写)**
只用父窗口的 Editor SDK。

- ✅ 能做:保存/下载/导出 PDF / 插图 / `setRevisedFile` 整体对比 / `onMakeActionLink` 链接 / 协作通知
- ❌ 不能做:加粗/查找替换/选区读写/光标插入/段落操作 —— **任何文字内容相关都做不到**
- 集成代价:零
- 适用:**只把察元办公当"内嵌阅读+协作器"**,助手不参与编辑

### 路线 B:**Editor SDK + 服务端改写 + Reload**
不写任何 plugin,所有文档修改通过 chayuan-server 改 docx 二进制 →
bump `office_document.doc_key` → 编辑器 destroy + 重建。

- ✅ 能做:加粗/查找替换/翻译/改写/批注/修订(以 `w:ins`/`w:del` 形式预写,用户在原生审阅 UI 接受)/ 段落级操作 / 全文操作
- ❌ 不能做:**读取选区** / 监听光标 / 实时(每次操作 reload 2–3s)
- ❌ 选区 UX 缺失 —— 用"段落 picker / 大纲选择"替代"用户在 DS 里选中文字"
- 集成代价:写 `office_authoring/` 模块(outliner / revisor / commenter),~5 天
- 适用:**助手能力以"批量/范围操作 + 审阅工作流"为主**,不追求鼠标即所得

### 路线 C:**Editor SDK + Proxy Plugin(30 行死桥)**
写一个**永不更新**的最小代理插件,仅做 postMessage 中转。
所有业务逻辑、UI、状态、LLM 调用**都在父窗口**写。

```js
// 完整 plugin 代码,~30 行,写一次基本不动
window.Asc.plugin.init = () => window.top.postMessage({chayuan:'ready'}, '*');
window.addEventListener('message', (e) => {
  const m = e.data; if (m?.chayuan !== 'call') return;
  if (m.kind === 'method') {
    window.Asc.plugin.executeMethod(m.name, m.args || [], (r) =>
      e.source.postMessage({chayuan:'reply', id:m.id, result:r}, e.origin));
  } else if (m.kind === 'command') {
    const fn = new Function('return (' + m.fnSource + ')')();
    window.Asc.plugin.callCommand(fn, m.isClose, m.isCalc);
  } else if (m.kind === 'event') {
    window.Asc.plugin.attachEvent(m.name, (d) =>
      e.source.postMessage({chayuan:'event', name:m.name, data:d}, e.origin));
  }
});
```

- ✅ 能做:**几乎全部 Plugin API 能力**,在父窗口里像调本地函数一样调
- ✅ 实时(无 reload)
- ✅ 业务逻辑全在主代码库,React/TS 友好,可单测
- ✅ Plugin 只依赖三个最稳定的 API(executeMethod / callCommand / attachEvent),DS 升级风险极低
- ⚠️ External 模式下用户需要把 plugin 目录拷贝到他们的 DS `sdkjs-plugins/`(一次性,可脚本化)
- 集成代价:~30 行 plugin + ~50 行父窗口 bridge + 业务调用

### 路线 D:**业务插件(plan 原方案)**
Plugin 内含业务逻辑(选区监听 → 拼请求 → 调 LLM → 回写),父窗口只做框架。

- ✅ 能做:全部
- ❌ Plugin 代码量大(几百行),业务变更要发新 plugin 版本
- ❌ Plugin 内调网络要处理 DS 沙箱限制(CSP / origin)
- ❌ 业务测试必须在 DS 环境跑
- ❌ DS 升级时,plugin 内调的 API 越多,破坏面越大

## 路线对比表

| 维度 | A 纯 Editor SDK | B 服务端改写 | **C Proxy Plugin** | D 业务插件 |
|---|---|---|---|---|
| 选区读取 | ❌ | ❌ | ✅ | ✅ |
| 加粗/格式 | ❌ | ✅(段落级,reload) | ✅ 实时 | ✅ 实时 |
| 查找替换 | ❌ | ✅ reload | ✅ 实时 | ✅ 实时 |
| 修订/审阅 | △ 整体对比 | ✅ 服务端预写,原生 UI 接受 | ✅ 编程接受/拒绝 | ✅ |
| 评论标注 | ❌ | ✅ | ✅ | ✅ |
| 插入图片 | ✅ insertImage | ✅ 服务端 | ✅ | ✅ |
| 实时性 | 即时 | reload 2–3s | 即时 | 即时 |
| 业务代码位置 | 父窗口 | 父窗口 + 后端 | **父窗口** | DS plugin 内 |
| 插件代码量 | 0 | 0 | ~30 行,**写一次永不变** | 几百行,跟业务走 |
| DS 升级风险 | 极低 | 极低 | 低(只 3 个 API) | 中(API 面广) |
| External 部署 | 零 | 零 | 用户一次性 mount plugin 目录 | 同 C,但每次升级要重 mount |
| Embedded 部署 | 零 | 零 | compose mount,无感 | 同 C |
| 跨文档类型一致 | — | ✅(都走二进制改) | ⚠️ word/cell/slide 行为不同,要分支 | 同 C |
| 单测能力 | 强 | 强 | 强(主逻辑在 TS) | 弱(要在 DS 内跑) |
| 可审计 / 可回滚 | — | ✅ 每次产生新 version | ⚠️ 需自己存 audit log | 同 C |

## 决策

**推荐:路线 C(Proxy Plugin)为主,路线 B(服务端改写)为辅。**

- **C 主**:所有"鼠标级即时操作"(选中→按钮→秒变、查找替换、加粗、批注)走 Proxy Plugin
- **B 辅**:大批量 / 跨段落 / 全文级 / 需审计的操作(全文翻译、批量修订、章节重写)走服务端改写 + reload,沉淀到 `office_document_version`

两者并存的依据:
- C 的实时优势在"小范围、明确目标"操作上不可替代
- B 的可审计/可回滚优势在"大改动、有风险"操作上不可替代
- 工程上不冲突 —— 父窗口路由层根据操作类型分发到 C 或 B
- A 的能力(insertImage / setRevisedFile / 协作通知)被 C 和 B **都默认包含**,不另立路线

## 理由

1. **C 几乎抹平了"零插件"的诱惑** —— 30 行死代码不算"插件开发负担",
   但换来全部 Plugin API,且业务代码 100% 在父窗口
2. **D 的真正成本不是首次开发,而是每次业务变更要重发 plugin** —— 升级链路、
   兼容性、用户重 mount,长期成本远超 C
3. **B 单独做也能成立**,但失去选区交互后,部分场景(查找替换、就地加粗)
   UX 明显劣化;作为辅助路径价值最大
4. **A 单独做** 只能交付"内嵌阅读 + 协作",助手能力等于零,不满足产品目标

## 触发重新评估的条件

回到 D(业务插件)如果:
- Proxy Plugin 桥的 postMessage 在某些 DS 版本下出现性能瓶颈(如选区频繁变化时)
- 业务复杂到 plugin 内必须有独立状态机(目前看不到这种场景)
- OnlyOffice 推出官方"plugin 业务模板",标准化业务插件分发

回到 B 唯一(放弃 C)如果:
- External 用户拒绝在自家 DS 上 mount 任何第三方 plugin(企业安全策略)
- 用户基本盘 100% 是"长文档批量处理",没有"鼠标级"需求

## 选 C+B 的后果(落地清单)

后端:
- `office_authoring/` 模块(B 路径专用):`outliner.py` / `revisor.py` / `commenter.py`
- `/office/docs/{id}/assistant/{revise,annotate,derive}` 路由
- Proxy plugin 静态文件随 chayuan-server / chayuan-client 一起分发,
  `apps/desktop/src-tauri/resources/onlyoffice-plugin/asc.chayuan-bridge/`

前端:
- `packages/app/src/features/office/bridge/` 父窗口 bridge:
  - `pluginBridge.ts`(C 路径,postMessage 协议 + Promise 化封装)
  - `serverAuthoring.ts`(B 路径,后端 REST + reload 触发)
  - `actionRouter.ts`(根据操作类型分发到 C 或 B)
- `<DocAssistantPanel>` 内的动作 chip 标注路径(实时/reload),让用户感知差异

部署:
- Embedded:compose 模板里 mount plugin 目录到 DS 容器,零额外步骤
- External:Settings 页加「安装察元桥接插件」按钮,生成 zip + 给一行
  `docker cp` 命令;后端 `/office/install-plugin` 端点产出 zip

测试:
- C 路径:Vitest mock postMessage 通道,在 jsdom 里测 bridge 协议
- B 路径:用真实 docx 文件 + python-docx 集成测,验证 ins/del/comment 写入正确
- E2E:Playwright 起本地 DS + 完整 C+B 链路,验证选区读取 / 服务端改写 / reload

## 关联文档

- 计划:`docs/plans/chayuan-office-onlyoffice.md`(本 ADR 落地后,plan §4
  本地↔编辑器交互章节应按 C+B 路线重写,§9 难度评估 plugin 项从 4 降到 3)
- OnlyOffice Plugin SDK: https://api.onlyoffice.com/docs/plugin-and-macros/
- OnlyOffice Editor SDK: https://api.onlyoffice.com/docs/docs-api/
- 官方 plugin 范例:https://github.com/ONLYOFFICE/sdkjs-plugins
