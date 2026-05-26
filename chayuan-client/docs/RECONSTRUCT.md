# 察元客户端重构方案(对齐参考图)

> 目标:把现有 `chayuan-client`(Tauri 2 + React 19 + TanStack monorepo)的 UI/IA 重构成
> `chayuan-server-frontend/界面参考` 中 10 张设计图所定义的"浏览器式多 Tab 工作区"形态,
> 同时保留全部底层工程能力(PAL / 401 单飞 / SSE Worker / outbox / Langfuse / SQLite WAL)。
>
> 配套文档:
> - 决策记录:`docs/adr/0001-...md` ~ `docs/adr/0005-...md`
> - 接口对齐:`docs/contracts.md`

---

## 1. 设计语言提炼(从参考图量化)

**外壳(Shell)**:浏览器式多 Tab(顶部一行 Tab + 前进/后退 + 全局右上的广告位/头像下拉/Pin/Win 三联键)。

**左侧栏**:头像 + 昵称 + 编辑 + 折叠;`+ 新建对话(Ctrl K)`;一级入口 4 项(知识库 / 模型广场 / AI Space / 历史对话);最近会话二级列表。选中态:浅蓝 pill。

**主区**:Tab 标题或欢迎卡 + 卡片网格 + **底部固定霓虹光晕 ChatComposer**(粉/蓝/绿外发光 + 模型选择 + 深度思考 + 工具 + 麦克风 + 黑色圆形发送)。这是产品的视觉锚点和功能枢纽,5 张主功能页都复用它。

**Token(从图量化)**

| 类别 | 值 |
|---|---|
| 主色 | `#3D7BFF`(CTA 蓝)、`#0A0A0A`(选中黑 pill)、紫蓝渐变(头像/欢迎球) |
| 霓虹 | radial-gradient 粉(`#FFB6C1`)+ 蓝(`#7CB7FF`)+ 绿(`#86E6BE`),blur 24-40px |
| 圆角 | 卡片 16、按钮 999(pill)、输入框 16 |
| 间距 | 4/8/12/16/24/32 8 倍数栅格 |
| 字体 | "PingFang SC / HarmonyOS Sans",标题 24/18/14,正文 14,辅助 12 |
| 主题 | 浅 / 深 / 跟随系统 |

---

## 2. 目标信息架构(IA)

```
/login                         未登录(Modal in-place)
/home                          欢迎 + 能力快捷条
/chat/$id                      对话
/history                       历史对话列表
/kb                            知识库:推荐/全部
/kb/$id                        单 KB
/marketplace                   模型广场
/marketplace/$modelId          模型详情
/space                         AI Space(应用商店)
/space/$appId                  单 App
/skill/$skillId                能力快捷条:写作/翻译/妙记/同传字幕/修图……(共用 SkillTemplate)
/settings                      设置(分组锚点)
/admin/...                     管理后台(保留)
```

---

## 3. 目标架构(包结构)

```
packages/
  platform-shared            ← 已有,扩 PAL: tabs / window-position / global-shortcut
  platform-tauri             ← 已有,补 multi-window / 托盘 / 系统主题事件
  platform-web               ← 已有,提供 BroadcastChannel
  api                        ← 已有,扩 marketplace fixture / ai-space pseudo-service
  transport                  ← 已有
  observability              ← 已有
  ui                         ← 已有,补新基元(GlowFrame/Pill/Segmented/CardGrid/Switch/Slider/Avatar)
  app                        ← 已有,内部 features 重排
  i18n             (新)      ← 从 server-frontend 搬 zh/en/ja/ko/fr/de + i18next + ICU
  design-tokens    (新)      ← tokens.ts + DTCG JSON + tokens.css(单源)
```

> **0 破坏性外部 API**;升级集中在 `app` / `ui` / 新增的 `i18n` 与 `design-tokens`。

---

## 4. 多 Tab 工作区(M1 关键)

- 单 BrowserHistory + `tabsStore`(Zustand)。每 Tab = `{ id, title, path, scrollY, transient state }`,激活 Tab → 把 path 投影到 history。
- 切 Tab 不卸载:`Chrome` 之上叠 `<TabHost>`,内部 `KeepAliveOutlet` 为每个 Tab 渲一份 `<Outlet/>`,非激活 `display:none`,通过 `MemoryRouter` per-tab 隔离 history。
- 跨 Tab 共享:TanStack Query 单例 + Zustand 单例。
- Tab 私有:草稿 / 滚动位置 / 模型选择(每 Tab 一份 composerSlice)。
- Tauri 多原生窗口"拖出 Tab"延后到 M5 评估(ADR-01)。

---

## 5. 状态边界

| 域 | 工具 | 作用域 |
|---|---|---|
| 服务端态(会话/消息/KB/模型/AI Space) | TanStack Query | 全局 |
| 用户偏好(主题/字号/locale/窗口位置/AI 键) | Zustand + persist | 全局,localStorage |
| 鉴权 | Zustand + Stronghold/Dexie | 全局 |
| Composer 草稿 / 工具勾选 / 模型选择 | Zustand,**per-Tab slice** | Tab 私有 |
| Tab 列表 / 当前 Tab | Zustand,持久化最近 N | 全局 |
| Artifact 抽屉 / 设置弹窗 | useState + CustomEvent(已有) | 全局 |

铁律保留:**服务端数据严禁进 Zustand**;选择器细粒度。

---

## 6. 高性能 / 多并发(在已有基础上叠加)

已有(保留):SSE Worker、流式段缓存、401 单飞、outbox、FTS5、空闲合并 reset。

新增:

1. 虚拟化:历史 / KB / 模型广场 / AI Space 卡片网格 → `@tanstack/react-virtual`。
2. React 19 编译器:开 `react-compiler` Babel 插件,免去 `memo/useCallback`。
3. Suspense 流式:loader prefetch + `Suspense fallback` 骨架。
4. 多并发请求合流:`useQueries`;`keepPreviousData` 防厂商切换闪烁。
5. Worker 进一步覆盖:markdown / KB 摘要走 `comlink`。
6. 离屏 Tab 节流:visibility API 暂停 `requestAnimationFrame`/SSE。

---

## 7. 主题与国际化

- **Token 单源**:`packages/design-tokens` 输出 CSS variables + DTCG JSON,Tailwind v4 `@theme` 直接消费,Storybook 同步。
- **三态主题**:`data-theme="light|dark"` + `prefers-color-scheme`;`index.html` 内联早期 IIFE 消除 FOUC。
- **字号**:`useSettingsStore.fontSize` → `<html style="font-size: ?px">`,Tailwind 全 `rem`。
- **i18n**:升级到 i18next + ICU。**直接迁** `chayuan-server-frontend/src/i18n/locales/{zh-CN,en,ja,ko,fr,de}.ts`(ADR-02),按 namespace 切分(`common / chat / kb / marketplace / space / settings / auth / errors`)。按需 lazy load。
- **格式化**:日期/数字/相对时间用 `Intl`;消息复数用 ICU MessageFormat。
- **RTL 预备**:Tailwind logical properties,token 命名 `start/end` 替 `left/right`。

---

## 8. 可访问性 / 测试

- Radix Primitives 已用;`Composer` 必须键盘全操作(`/` mention,`@` 模型,`Esc` 关闭)。
- `aria-live="polite"` 流式气泡;`prefers-reduced-motion` 关霓虹动画。
- Storybook + a11y addon 全组件覆盖。
- Playwright 新增:多 Tab 切换、模型广场厂商切换、KB 文档上传、菜单 Popover 主题切换。

---

## 9. 模块化分解(features 蓝图)

| feature | 复用 | 新建 | 关键组件 |
|---|---|---|---|
| `shell/tabs` | — | ✅ | `TabBar` `TabHost` `KeepAliveOutlet` `tabsStore` |
| `shell/menu` | DropdownMenu | ✅ | `UserMenuPopover`(主题/字号/窗口位置) |
| `shell/window` | platform-tauri | ✅ | `WindowControls` `WindowDock` |
| `home` | — | ✅ | `WelcomeOrb` `Greeting` `CapabilityRail` `SuggestionCards` |
| `composer` | 现有 Composer | 改 | `GlowFrame` 视觉 + 模型 + 深度思考 + 工具/麦克风/发送 |
| `chat` | 已有 ChatPage/Thread | 保留 | 装入新 GlowFrame |
| `history` | useConversationList | ✅ | 列表 + 大行气泡 |
| `kb` | KbPage 框架 | 重写 | `KbBoard`(推荐/全部 + 子标签 + 卡片 + AI 笔记) |
| `marketplace` | — | ✅ | `ModelHero` `VendorTabs` `ModelGrid` `ManagePanel` |
| `space` | — | ✅ | `SpaceCategories` `AppCard`(角标 New/Hot) |
| `skill` | — | ✅ | `SkillTemplate`(写作/翻译/妙记/同传/修图共模板) |
| `auth/login` | useAuthStore | 重塑视觉 | `LoginCard`(Modal-in-place,JWT,见 ADR-03) |
| `settings` | SettingsDialog | 重写为页 | `SettingsSections`(个人/基础/快捷工具/高级) |
| `cmdk` | 已有 | 保留 | 新增搜索 模型/AI Space/技能 |
| `notifications` | — | ✅ | 顶栏铃铛 + 消息中心 |

---

## 10. 阶段化交付计划

| 里程碑 | 目标 | 关键交付 | 退出门槛 |
|---|---|---|---|
| **M0 — Token & i18n 地基** | 不破坏现状,准备燃料 | `design-tokens` 包、`ui` 新基元、i18next 升级 + 6 语种、字号接管 `<html>` rem | Storybook 全绿 + 主题/语言切换零回归 |
| **M1 — 多 Tab Shell + 新菜单** | Chrome 升级为 `TabHost`,新侧栏 IA | `TabBar` `TabHost` `KeepAliveOutlet` `tabsStore` `UserMenuPopover` `WindowControls` 桌面靠边 | 主页/设置/任意页可 Tab 化共存,主题/字号生效 |
| **M2 — 首页 + Composer** | 霓虹光晕 Composer 提为站点核心 | `WelcomeOrb` `CapabilityRail` `SuggestionCards`、`ChatComposer`(GlowFrame + 模型 + 深度思考 + 工具) | 首页/Chat 共用同一个 Composer;a11y 通过;帧预算 ≤ 16ms |
| **M3 — KB / 模型广场 / AI Space** | 三大卡片网格页 | `KbBoard`、`ModelHero+VendorTabs+ModelGrid`、`SpaceCategories+AppCard` | 1k+ 卡片 60fps;厂商切换 `keepPreviousData` 不闪 |
| **M4 — 历史/设置/登录/技能模板** | 收齐剩余页型 | `History`、`SettingsSections`、`LoginCard`(JWT)、`SkillTemplate` | 登录 → 首页 → 一级页 → 技能 → 发消息 全程通畅 |
| **M5 — 性能/可观测/E2E/打磨** | 压测 + a11y + 视觉回归 + 文档 | React 19 编译器开关、Lighthouse Web ≥ 95、多 Tab Playwright 套件、`ARCHITECTURE.md` 更新 | Chromatic 0 视觉回归;Tauri 三端冒烟 |

> 节奏:每 milestone 1–2 周。每个 milestone 含 Storybook 故事 + Playwright 用例 + Chromatic 视觉回归。

---

## 11. 与 `ARCHITECTURE.md` 的关系

- 本文档**新增**:Tab 工作区 / 设计 Token / i18n 升级 / 模块化重排 / 阶段化交付。
- 本文档**不动**:网络/状态/渲染/持久化/Trace/HIL/部署等底层契约。
- M5 收尾时把"多 Tab"与"per-Tab slice"等结论合并入 `ARCHITECTURE.md`。

---

## 12. 锁定的决策(详见 ADR)

| 编号 | 决策 |
|---|---|
| ADR-01 | 多 Tab:内 Tab + KeepAliveOutlet(M1),M5 评估拖出原生窗口 |
| ADR-02 | 复用 `chayuan-server-frontend` 的 i18n locale × 6 + SVG / logo manifest |
| ADR-03 | 登录:保留 JWT 用户名/密码,**仅做视觉重塑**(Modal-in-place) |
| ADR-04 | AI Space:M3 前端伪服务 + 内置 fixture,后端可后续替换为同形 contract |
| ADR-05 | 样式栈:沿用 Tailwind + CSS variables + tokens.ts,不引 Vanilla-Extract |
