# 知识库详情:Tab 化 + 卡片重设计

> Status: design + implementation
> 关键词: 多 Tab / KB 详情独立页 / 卡片重设计 / 复用 / 多并发

## 1. 现状与目标

**现状(问题)**:
1. 知识库管理页 `/kb` 的卡片是一个简陋的"渐变条 + 文字"样式,与 `界面参考/知识库.jpg`、`知识库1.png`、`知识库2.png` 设计稿不符,信息密度低且不耐看。
2. 点击卡片后弹出 `KbDetailDialog`(Radix Dialog 内嵌 4 种 detail 子面板),屏幕被遮挡、Tab 体系外、无法多开比较、preview 面板没法独占空间。
3. `DocumentKbDetail` / `ImageKbDetail` / `StructuredKbDetail` / `VectorKbDetail` 直接耦合 dialog header,
   既不能独立做页面,也不能复用到聊天 citation 等场景。

**目标**:
1. **卡片重设计** — 与设计稿对齐的"白底软卡片"(标题 + 描述 + 类型 chip + 日期)。
2. **KB 详情独立 Tab** — 点 KB 卡片 → 像"新建对话"一样,在 TabBar 顶部新开一个 Tab,路由 `/kb/$kuId`。
3. **新页布局**:
   - 顶部:KB 标题 / 类型 / 元信息 / 操作
   - 中部:文件卡片网格(或列表)— 复用既有 detail 子面板
   - 右侧:文件预览(已实现的 `<PreviewPanel>` — dock / float / collapse / detach)
   - 底部:`<ChatComposer>` 锁定该 KB
4. **复用、并发、可扩展**:
   - 详情面板 = 业务核心,既可被 dialog 外壳承载(legacy / 嵌入 chat citation 等),也可被独立页承载
   - 多个 KB Tab 并存 — 各自的 react-query cache 用 `['ku.detail', kuId]` 自然 key 化
   - 共用同一个全局 PreviewPanel,跨 tab 切换不重渲

## 2. 架构

```
features/kb/
├─ landing/                          ← /kb 页(原 KbBoard)
│  ├─ KbCard.tsx                     ← 新:soft-card 设计(替换 KbCardCompact)
│  └─ KbCategoryChips.tsx            ← 新:推荐/教程/进阶/...
├─ detail/                           ← /kb/$kuId 页 + 内部子面板
│  ├─ KbDetailPage.tsx               ← 新:Tab 路由组件
│  ├─ KbDetailHeader.tsx             ← 新:页内 sticky 顶栏
│  ├─ DocumentKbDetail.tsx           ← 修:body-only,header 抽出去
│  ├─ ImageKbDetail.tsx              ← 修:同上
│  ├─ StructuredKbDetail.tsx         ← 修:同上
│  ├─ VectorKbDetail.tsx             ← 修:同上
│  ├─ types.ts
│  └─ useKuDetail.ts                 ← 新:共享 react-query 钩子
├─ KbBoard.tsx                       ← 改:卡片改 KbCard,onOpen 改为 openTab(/kb/$kuId)
└─ KbDetailDialog.tsx                ← 保留 → 以后可删,或转为引用 KbDetailPage 的轻量 dialog 模式
```

新增 / 改动的页面注册:

```
features/shell/page-registry.tsx
  + RouteEntry: pattern /^\/kb\/(?<kuId>[^/]+)$/ → <KbDetailPage kuId={...} />
                + onActivate:同步 composer.kuIds = [kuId]
                + defaultIcon: 'library'

router/index.tsx
  + workspaceRoutes:'/kb/$kuId'
```

### 数据流

```
KbBoard ── click(kuItem) ──→ tabsStore.open('/kb/' + kuId, { title, icon })
                                                    │
                                                    ▼
                                        TabBar 出现新 Tab
                                                    │
                                                    ▼
                                  page-registry 解析 → KbDetailPage(kuId)
                                                    │
                                                    ├── useKuDetail(kuId)        ← cache: ['ku.detail', kuId]
                                                    │     └── 路由到 4 个 body 面板
                                                    │
                                                    ├── 底部 <ChatComposer> 锁定 selectedKuIds=[kuId]
                                                    │     onActivate 时同步,离开 tab 不清(用户体验更连贯)
                                                    │
                                                    └── 文件 click → usePreviewStore.open({source:'kb-doc', ...})
                                                          ↓
                                                    全局 <PreviewMount> 渲染
```

### 复用 / 解耦

- `DocumentKbDetail` 等 4 个面板 = **纯内容渲染**,不再含 header / close 按钮 / dialog 容器。
- 需要 dialog 模式时:`KbDetailDialog` 可以继续在 `<DialogContent>` 里内嵌 `<KbDetailHeader>` + `<DocumentKbDetail>`(过渡期保留)。
- 独立页模式:`KbDetailPage` 内嵌 `<KbDetailHeader>` + body + composer。
- 这样以后接入 chat citation 跳转、admin 嵌入等只需要再写一个 wrapper。

### 多并发

- 每个 KB Tab 是 KeepAliveOutlet 下独立的子树(挂载即创建,切走保留)。
- React Query 用 `['ku.detail', kuId]` 当 key,自动复用、并发请求合并。
- 共享 `<PreviewMount>`(在 Shell 顶层),无论从哪个 tab 触发都同一个面板,**state 全局**,切 tab 不重置。
- 上传进度通过 `kbUploadStore`(已 WeakMap 缓存)在多 tab 间共享 — 同一 KB 上传中,多个 tab 看到同一进度。

### 性能

- KbDetailPage 走 `React.lazy` 注册,首屏不引入。
- 文件列表沿用既有 `<table>`(虚拟化以后再做);图像网格已是按需加载 thumb。
- Preview 面板 renderer 全部 React.lazy(已做)。
- TabHost 的 KeepAlive 让来回切 tab 不重新发起请求。

## 3. 卡片设计(KbCard 新版)

参考 `知识库.jpg`:

- **容器**:`white` 背景,`rounded-xl`,`border-subtle`,`shadow-sm` → hover `shadow-md` + `-translate-y-0.5`
- **顶部**:无渐变条(老版有,设计稿没有)
- **第一行**:粗标题 truncate(font-medium,~14px)
- **第二行**:描述 line-clamp-3(text-tertiary,~12px,~3 行)
- **第三行(底)**:kind chip(PDF / IMG / DB / VEC,带颜色)+ 日期(右对齐,opacity-60)
- **角标**:右上 mine pill / private 锁;左上 hover 才出 checkbox(多选)
- **类型驱动微调**:doc 卡可显示主文件名;image 卡可缩略图栅格;structured 显示表数;vector 显示集合数

每种 kind 颜色:
- document → 靛蓝(indigo)
- image → 玫粉(rose)
- structured → 翠绿(emerald)
- vector → 琥珀(amber)

## 4. 落地步骤(本次 commit)

1. `useKuDetail.ts` — 共享 query hook
2. 抽出 `KbDetailHeader.tsx` — 含 title / kind chip / mine / refresh / close-tab 按钮
3. 改 4 个 detail body 面板:去掉自带 header(它们已经只渲染 body table/grid,改动很小)
4. 写 `KbDetailPage.tsx` — 顶 header + body + 底 composer
5. 注册 `/kb/$kuId` 路由 + tab onActivate(同步 selectedKuIds=[kuId])
6. 改 `KbBoard.onCardClick`:`useTabsStore.open('/kb/' + kuId, { title, icon })`
7. 写 `KbCard.tsx` — 新设计;`KbBoard` 切到使用 KbCard
8. 写一个最小版 `KbCategoryChips.tsx`(推荐 / 全部 / 我的;tutorials chips 后续可加)
9. 老 `KbDetailDialog.tsx` 暂保留(其他地方未用到则后续删)

> Phase 2(下次):上传/批量删/以图搜库 跨 tab 同步;tutorial chips 接入文档分类。
