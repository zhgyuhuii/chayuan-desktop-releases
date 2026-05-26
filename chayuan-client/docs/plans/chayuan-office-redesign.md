# 察元办公（Chayuan Office）— 前端重构 & 落地计划

> 此文取代 `docs/plans/chayuan-office-onlyoffice.md` 的"必装 OnlyOffice + 强耦合"版本。
> 核心更新：**OnlyOffice 解耦为可选插件**；**前端布局重构为"主画布三卡 + 右侧库菜单（可折叠）"**；
> AI 助手通过通用 Composer 容器复用，不再特化办公专属链路。
>
> 状态约定：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成

---

## 0. 目标与非目标

### 目标
1. 在主侧栏新增「察元办公」一级入口（**置顶**），打开 `/office`。
2. 办公页布局 = 顶部三张大卡（新建文档/表格/演示稿）+ 主画布 + **右侧库菜单**（默认展开 240px、可折叠 56px、右上角折叠按钮）。
3. 库菜单：`文档库 / 表格库 / 演示库 / 收藏 / 共享给我 / 回收站`，分两组（我的 / 组织）。
4. 切换库 → 主画布下方"视图条 + 内容区"切换；三张大卡始终可见。
5. 视图：卡片网格 / 列表两态，记忆每个库的偏好。
6. 全部状态同步到 URL（`/office?lib=...&view=...`），刷新/分享/前进后退完整还原。
7. **不依赖 OnlyOffice 容器即可启动**——未配置 DS 时点击文档卡用 native md / download-only 兜底。
8. AI 助手以右下 FAB 入口接入（复用通用 Composer），不挤占办公页主区。

### 非目标（本期不做）
- 后端 office 表与 API（落到下一期；本期前端用本地 mock data，接口契约预留）
- OnlyOffice Document Server 引擎接入（属于第二阶段插件，不阻塞本期发版）
- 多人协同与版本树 UI（依赖后端，第二阶段）
- 模板库管理 UI（第三阶段）

---

## 1. 架构总览（站在架构师角度）

```
┌──────────────── chayuan-client (React monorepo) ───────────────────┐
│  apps/web · apps/desktop（共享同一 Shell）                          │
│                                                                     │
│  packages/                                                          │
│    ui              原子组件（已有）                                  │
│    api             REST/SSE 客户端（已有）                           │
│    app/features/                                                    │
│      shell         主侧栏 + Tab 容器（已有；本期：Sidebar 加 office）│
│      office  ★    本期新增 feature                                  │
│        ├─ types.ts                                                  │
│        ├─ data/mockDocuments.ts                                     │
│        ├─ store/officeUI.ts        Zustand（rail collapsed / 视图）│
│        ├─ OfficePage.tsx           主组装                           │
│        └─ components/                                               │
│           ├─ NewDocCardGroup.tsx                                    │
│           ├─ RightNavRail.tsx                                       │
│           ├─ LibraryToolbar.tsx                                     │
│           ├─ LibraryGrid.tsx                                        │
│           ├─ LibraryList.tsx                                        │
│           └─ DocumentCard.tsx                                       │
│      composer    通用 Composer（已有，AI 助手 FAB 复用）            │
└─────────────────────────────────────────────────────────────────────┘
```

### 模块化原则
- **office feature 单向依赖**：仅依赖 `@chayuan/ui`、`@chayuan/i18n`、`@tanstack/react-router`、`zustand`、`lucide-react`；不引第三方表格/卡片库（避免锁定）。
- **数据层抽象**：`useLibrary(kind)` hook 为唯一数据入口，第一阶段返回 mock，第二阶段切换到 `@chayuan/api`，组件不感知。
- **引擎可插拔**（为下一期铺路）：`packages/office-engine-*` 子包按需 dynamic-import；office 主包永远不直接 import OnlyOffice。
- **样式 token 化**：所有色值用 `var(--cy-*)`；不出现裸 `#` 值，便于主题切换。
- **i18n key 化**：文案全部走 `t('office.*')`；与现有 nav/common 同 namespace 风格。

### 高性能 / 多并发（前端视角）
- 列表视图用虚拟滚动（`@tanstack/react-virtual`，已是项目其他 feature 选型）—— 1 万行 60 fps。
- 网格视图按容器查询响应列数（CSS `@container`），无 JS 测宽监听。
- 切库走路由参数 + `useSyncExternalStore`，不重挂载页面。
- 拖拽上传用 `requestIdleCallback` 调度文件 hash 计算（第二阶段）。

### 灵活 / 可扩展
- 库种类用枚举 `LibraryKind`，新增"音频库/视频库"只加一个枚举值 + 一条菜单 + 一份 mock；OfficePage 不改。
- 卡片字段用 `DocumentSummary` 接口；新增字段卡片自适应。
- AI Composer 是 `<AssistantFab scope="office-home">`，scope 决定建议词与上下文，办公页换其他 scope 也是一行变更。

---

## 2. 技术选型（本期）

| 能力 | 选型 | 备选 | 取舍 |
|---|---|---|---|
| 路由 | TanStack Router（已有） | — | 沿用 |
| 状态 | Zustand 5（已有，含 persist） | Jotai/Redux | 沿用 |
| UI | shadcn-flavored `@chayuan/ui` + Radix Primitives + Tailwind 4 | — | 沿用 |
| 图标 | Lucide（已有） | — | 沿用 |
| 列表虚拟滚动 | `@tanstack/react-virtual` | react-window | 与 React 18 ConcurrentMode 兼容更好 |
| 拖拽（第二阶段） | dnd-kit | react-dnd | 已是项目其他 feature 选型 |
| 路由动画 | View Transitions API（原生）+ Framer Motion 兜底 | 仅 Framer Motion | 原生优先，零成本 |
| 动效 | Framer Motion 11（已有） | — | 沿用 |
| 容器查询 | 原生 `@container`（Tailwind 4 已支持） | element-resize-observer | 原生，无 JS 监听 |

---

## 3. UE 设计（站在用户体验角度）

### 3.1 布局规则
```
┌─ 主侧栏 ─┬─────── 主画布（自适应） ─────────────┬─ 右菜单 ─┐
│          │                                       │          │
│ 察元办公 │  早上好，张三                          │       ⇿ │ ← 折叠按钮
│ (置顶 ●) │                                       │          │
│ 知识库   │  [📝新建文档][📊新建表格][🎯新建演示] │ 我的     │
│ 模型广场 │                                       │ 📄文档库 │ ← 默认 active
│ MCP      │  ── 文档库 · N 个 ── [⊞|≡] [⇅] ──   │ 📊表格库 │
│ 工具     │                                       │ 🎯演示库 │
│ AI Space │  [卡片网格 / 列表]                    │          │
│ 历史     │                                       │ 组织     │
│          │                                       │ ⭐收藏   │
└──────────┴───────────────────────────────────────┴─────────┘
            主画布（grid: 1fr 240px / 折叠后 1fr 56px）
```

### 3.2 视觉系统
- **三张大卡**色彩矩阵（OKLCH 同 L/C 不同 H，感知亮度一致）：
  - 文档 → 靛蓝 H240
  - 表格 → 翠绿 H152
  - 演示 → 琥珀 H36
  - 兜底用 `--cy-brand-*` token；hover scale 1.02 + shadow elevated + 主色 1px 描边；按下 scale 0.98 弹回 80ms。
- **激活态**：右菜单激活项 `bg-cy-brand-50` + 内侧 3px 主色竖条贴向主画布。
- **折叠动效**：`width 220ms cubic-bezier(0.32, 0.72, 0, 1)` + 文字 `opacity` 同时淡出（120ms）。
- **缩略图**：第一阶段用 SVG 占位（按格式画 doc/sheet/slide 风骨），第二阶段接 DS thumbnail / LibreOffice headless。
- **暗色模式**：完全用 token 自适应；卡片底色用 `--cy-surface-1`。

### 3.3 趣味细节
- 时间问候随小时段变（早/午/晚 + emoji 微变）。
- 三大卡 hover 时副 CTA「↳ 从模板」从下而上滑入。
- 切库时主画布做 **View Transitions**：卡片网格 morph 成新库的卡片网格。
- 折叠右菜单时，菜单宽变化期间不允许 hover 触发 tooltip（避免视觉颤抖）。
- 空状态用手绘 SVG（每库一份，主色块为该库主题色）。

### 3.4 键盘
```
[                折叠 / 展开右菜单
1 / 2 / 3        切到 文档库 / 表格库 / 演示库
4 / 5 / 6        切到 收藏 / 共享给我 / 回收站
v                网格 ↔ 列表
n                聚焦三张卡（按 enter / 方向键选其一）
⌘N / ⇧⌘N / ⌥⌘N   直接新建文档/表格/演示
/                聚焦页面搜索（第二阶段）
```

---

## 4. URL 与状态契约

### URL
```
/office                                       默认 → ?lib=documents
/office?lib=sheets                            表格库
/office?lib=slides                            演示库
/office?lib=starred|shared|trash              其他菜单
/office?lib=documents&view=list               列表视图
/office?lib=documents&sort=updatedAt:desc     排序
```

### 持久化
| 键 | 范围 | 说明 |
|---|---|---|
| `office.rightRail.collapsed` | 全局 | true / false |
| `office.view.<lib>` | 每库 | 'grid' \| 'list' |
| `office.sort.<lib>` | 每库 | `field:direction` |

URL 与 localStorage 冲突时 **URL 胜**——便于分享链接。

---

## 5. 数据模型（前端类型）

```ts
export type LibraryKind =
  | 'documents'  // 我的文档
  | 'sheets'     // 我的表格
  | 'slides'     // 我的演示
  | 'starred'    // 收藏
  | 'shared'     // 共享给我
  | 'trash';     // 回收站

export type DocFormat = 'doc' | 'sheet' | 'slide';

export interface DocumentSummary {
  id: string;
  format: DocFormat;
  title: string;
  ownerName: string;
  ownerAvatarKey?: string;
  updatedAt: string;     // ISO
  size: number;          // bytes
  thumbUrl?: string;     // 第二阶段；第一阶段空，前端画 SVG
  // shared 库专用
  sharedBy?: string;
  sharedRole?: 'editor' | 'commenter' | 'viewer';
  // trash 库专用
  trashedAt?: string;
  daysUntilPurge?: number;
}
```

---

## 6. 后端契约（预留，本期不实施）

为了第二阶段不返工，本期 mock data 与 hook 形态严格对齐未来 REST：

```
GET  /api/office/documents?lib=documents&sort=updatedAt:desc&page=1
     → { items: DocumentSummary[], total: number, hasMore: boolean }
POST /api/office/documents                       新建（lazy 默认空文档）
POST /api/office/documents/{id}/open             返回 editorConfig
DELETE /api/office/documents/{id}                软删 → trash
```

---

## 7. OnlyOffice 解耦边界（第二阶段插件）

- `office/` 主目录下 **0 处 import** OnlyOffice。
- 第二阶段在 `packages/office-engine-onlyoffice/` 单独包，dynamic-import；env 未配置时 tree-shake。
- CI 加 lint：`grep -ri "onlyoffice" packages/app/src/features/office/` 必须 0 命中。

---

## 8. 阶段化清单（按本期 + 后续期分组，可勾选）

### Phase 0 · 设计 & 入口（本期 — 已完成 ✅）
- [x] 写本计划文档
- [x] i18n 文案（zh-CN + en）：`nav.office`、`office.greeting/cards/libraries/toolbar/empty/list/role/rail/daysShort`
- [x] 在 `page-registry` / `tab-titles` / Sidebar 注册 `/office`，置顶第一项；TabBar 图标 map 加 `briefcase`
- [x] features/office/ 目录骨架（types / mock / store / 组件占位）

### Phase 1 · 视觉与交互（本期 — 已完成 ✅）
- [x] NewDocCardGroup（三大卡，OKLCH 色彩矩阵 + hover/active 微交互 + 模板副 CTA）
- [x] RightNavRail（240↔56 折叠 + 顶部 ⇿ + 激活竖条 + 折叠态 tooltip + 红点）
- [x] LibraryToolbar（库标题+计数 / segmented 视图切换 / 排序下拉同字段切方向）
- [x] LibraryGrid + DocumentCard（响应式 auto-fill 网格 / 格式风骨 SVG 缩略图 / hover ⋯ + ⭐）
- [x] LibraryList（动态列：shared 加来源·权限；trash 加删除时间·距永久删；Phase 2 接入 react-virtual）
- [x] OfficePage 组装 + 主画布 grid 两栏（flex h-full + overflow 隔离）
- [x] URL 状态 + localStorage 持久化（URL > store > 默认；视图/排序按库各自记忆）
- [x] 键盘：`[` 折叠 / `1-6` 切库 / `v` 切视图
- [x] TypeScript（`tsc --noEmit`）+ Biome lint 全清；web 整体类型检查通过

### Phase 2 · 后端打通（下一期）
- [ ] Alembic migration `office_documents` / `office_document_versions` / `office_document_grants` / `office_document_audit`
- [ ] FastAPI 模块 `chayuan/server/office/`
- [ ] DocumentEngine 协议 + native md / download-only / native csv 三个内置引擎
- [ ] REST：list / create / get / delete / share
- [ ] 接通 `useLibrary` 从 mock 切换到真接口

### Phase 3 · OnlyOffice 插件（按需）
- [ ] `chayuan/server/office_engine_onlyoffice/`（pyproject extra）
- [ ] `packages/office-engine-onlyoffice/`（前端 adapter）
- [ ] DS callback 幂等处理 + JWT 签发（生产打开 JWT_ENABLED）
- [ ] 编辑器页 `/office/doc/:id`

### Phase 4 · AI 深度集成（按需）
- [ ] AssistantFab 在办公页展示
- [ ] 选区 → 改写 / 摘要 / 翻译 三动作
- [ ] 文档自动 embed 进个人 KB（可关）

### Phase 5 · 模板与生产化（按需）
- [ ] 模板库（默认 8 套）
- [ ] 移动端只读路由
- [ ] Langfuse trace + 性能压测

---

## 9. 风险登记

| 风险 | 缓解 |
|---|---|
| 第二阶段后端契约可能变化 | 把数据访问收敛到单个 hook + 类型定义；本期 mock 与未来真接口同形 |
| 右菜单与全局 KB tab 路由风格不一致 | 显式只在 `/office` 内部使用右菜单；不下放给其他页面 |
| 缩略图缺失看着空 | 第一阶段画格式风骨 SVG 占位；颜色与卡片色矩阵呼应 |
| 卡片数量增加导致网格抖动 | 容器查询固定列数变化点；卡片宽度区间内 fr 自适应 |
| OnlyOffice 后期接入回头改主目录 | 主目录与引擎包物理隔离；CI lint 锁死 |

---

## 10. 验收

- 主侧栏点「察元办公」即进 `/office`，且第一次进入默认 `?lib=documents`。
- 三大卡可见可悬浮，色彩矩阵正确。
- 右菜单默认展开，右上角 ⇿ 按钮可折叠到 56px；激活项有竖条；切换菜单项主区视图条 + 内容区随之切换；三大卡保持。
- 卡片视图 / 列表视图切换记忆按库存活；URL 反映当前 lib + view。
- TypeScript / Biome lint 全部通过；`pnpm -F @chayuan/web build` 成功。
- 暗色模式所有元素无白底/黑字错配。

> 完成上述项后，把 Phase 0-1 的所有 `[ ]` 改成 `[x]`。

---

## 11. 本期落地清单（截至 commit 前）

新增（office feature 目录）：
- `packages/app/src/features/office/types.ts`
- `packages/app/src/features/office/data/mockDocuments.ts`
- `packages/app/src/features/office/store/officeUI.ts`
- `packages/app/src/features/office/OfficePage.tsx`
- `packages/app/src/features/office/components/NewDocCardGroup.tsx`
- `packages/app/src/features/office/components/RightNavRail.tsx`
- `packages/app/src/features/office/components/LibraryToolbar.tsx`
- `packages/app/src/features/office/components/DocumentCard.tsx`
- `packages/app/src/features/office/components/LibraryGrid.tsx`
- `packages/app/src/features/office/components/LibraryList.tsx`

修改：
- `packages/app/src/features/shell/Sidebar.tsx` — `NAV_ITEMS` 首项加 `/office`；`iconKeyFor` 映射；导入 `Briefcase`
- `packages/app/src/features/shell/page-registry.tsx` — 加 `/office` lazy 路由
- `packages/app/src/features/shell/tab-titles.ts` — `STATIC` 加 `/office: nav.office`
- `packages/app/src/features/shell/TabBar.tsx` — `ICON_MAP` 加 `briefcase`
- `packages/i18n/src/locales/zh-CN.ts` / `en.ts` — `nav.office` + `office.*`

未改：路由 `/office` 通过 TabHost 自动开 Tab；不需要改 `router/index.tsx`（splat workspace 路由覆盖）。

## 12. 下一期入口

- 后端 office 模块（参考 §6 契约）+ Alembic migration
- 把 `OfficePage` 内的 `loadMock` / `libraryCounts` 替换为 `useLibrary(lib)` hook（@chayuan/api）
- AssistantFab（右下浮窗）+ ⌘K（命令面板）—— 复用 chat 已有的 Composer
- 编辑器路由 `/office/doc/:id` + DocumentEngine 协议（native md / download-only / OnlyOffice 三引擎）
