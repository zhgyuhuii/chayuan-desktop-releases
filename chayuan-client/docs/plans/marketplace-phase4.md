# 模型广场 Phase 4 — 厂商卡片 + 在线管理 + 热生效

> **核心目标**:把模型广场从"模型级卡片"升级为参考图所示的"厂商级卡片 + 模型标签",
> 接入 admin CRUD 与连通性测试,**配置改完即时生效,不重启服务**。

## 0. 背景与现状

### 0.1 后端(`chayuan-server`)— **已就绪,无需改动**

- `model_platform` 表(migration `0004_model_platform`),把原 yaml 里的 `MODEL_PLATFORMS` 落库
- repository:`bump_platform_version()` 写后版本号 +1
- `server.utils.get_config_platforms` 三层叠加:**yaml seed → JSON overrides → DB(最高)**,5s TTL 缓存以 `_PLATFORM_VERSION` 为 key → **写后即时穿透,不重启**
- `get_OpenAIClient` 不缓存,新连接走最新平台配置
- admin 路由(`/api/admin/model_platforms*`):
  - `GET /admin/model_platforms` — 列出全量(含禁用),返回 `enabled / api_base_url / api_key / llm_models / disabled_models / ...`
  - `POST /admin/model_platforms` — 新建
  - `PATCH /admin/model_platforms/{name}` — 部分字段更新
  - `DELETE /admin/model_platforms/{name}` — 删除 DB 行(yaml seed 仍兜底)
  - `POST /admin/model_platforms/{name}/test` — 连通性探测 + 自动检测 ollama/xinference 可用模型;OpenAI 兼容平台走 `client.models.list()`

### 0.2 前端(`chayuan-client`)— 缺口

`packages/app/src/features/marketplace/MarketplacePage.tsx` 当前是**模型级卡片**,缺:

- ❌ 厂商卡片(一卡 = 一个 platform)
- ❌ 卡片下方模型标签 chips + maxVisible 截断 + 「更多 ▼」dropdown
- ❌ 卡片右下「设置 ⚙️」入口(打开配置弹窗)
- ❌ `PlatformSettingsDialog`:base_url / api_key / proxy / 并发 / 启用开关 / 模型黑名单 / 连通性测试 / 一键填入检测到的模型 / 删除
- ❌ 未配置/未启用平台 → grayscale 卡片
- ❌ 「+ 添加厂商」入口
- ❌ 写后局部更新 + 触发 `chatStore.loadModels`(热生效闭环)

### 0.3 设计参考

`界面参考/模型广场.jpg`:hero(当前模型 logo + 名 + tagline)+ 类别 tabs(推荐 / 厂商分组 / 管理 / 添加)
+ 卡片网格 + 底部 ChatComposer

## 1. 架构设计

### 1.1 数据流(写后热生效闭环)

```
PlatformSettingsDialog.save()
   └─ POST/PATCH /admin/model_platforms/...
        └─ repository.upsert_platform → bump_platform_version()  [后端]
        └─ get_config_platforms 5s TTL 失效                       [后端]
   └─ stores/modelPlatform.invalidate()
        ├─ tanstack-query refetch ['marketplace', 'platforms']
        └─ tanstack-query refetch ['marketplace', 'models']  ← /v1/models
              └─ ChatComposer 下次 send 自动用最新 model 列表       [前端]
```

**关键点:** `get_OpenAIClient` 不缓存 client 实例 + 5s TTL 平台缓存按版本号失效 →
后端任何 API call 都会读到最新配置;前端 query refetch 后 UI 也立刻同步。
**不需要 hot-reload 信号、不需要重启进程。**

### 1.2 模块划分

```
packages/api/src/
  modelPlatform.ts                # 新建:admin CRUD + test 类型化封装

packages/app/src/
  store/modelPlatform.ts          # 新建:RQ key + 写后 invalidate 编排
  features/marketplace/
    MarketplacePage.tsx           # 重写:厂商卡片网格 + composer
    components/
      ProviderCard.tsx            # 新建:厂商卡片(logo+名+tags+more+gear)
      ModelTagsRow.tsx            # 新建:模型 chip 行(maxVisible + dropdown)
      PlatformSettingsDialog.tsx  # 新建:配置/启用/测试/删除
      PlatformCreateDialog.tsx    # 新建:从空 + 添加厂商
      VendorTabs.tsx              # 新建:推荐 / 厂商 / + 添加 / 管理
```

**包边界:** `api` 不依赖 `transport`(同 KU SSE 决定);`app` 用 RQ 串起 admin + 模型列表。

### 1.3 性能 / 并发要点

- `marketplace.platforms` query staleTime=60s + writeThrough invalidate
- 写操作 mutation 完成后,**先**用 `setQueryData` 局部更新平台行(零闪烁),**再** invalidate `models`
- 连通性测试是独立 mutation(不会触发列表 invalidate,避免误闪)
- ProviderCard 用 `React.memo`,父级排序变更不会让其他卡片重渲
- ModelTagsRow 用 `useMemo` 切 `visible / overflow` 两段;切 active model 走 `useComposerStore.setModel`
- DropdownMenu 复用 `@chayuan/ui`,触发器用 portal 防溢出截断

### 1.4 UE / 视觉

- 卡片:`hover:-translate-y-0.5`,选中态 brand 描边 + ring;**未配置/未启用** 走 grayscale + 60% opacity
- 标签 chips:active 实心(brand-700 文字 + brand-100 底)/ 默认描边
- 「更多 ▼」用 DropdownMenu,折叠超出 maxVisible 的标签,选中后自动归位置顶
- ⚙️ 设置:hover-only 显式;owner / admin 才显示
- 连通性测试结果:绿勾 / 红叉 + detail badge(检测到的模型数)

## 2. 任务拆分(随实现勾选)

### 2.1 P0 — API + 状态层

- [x] **MP-1** `packages/api/src/modelPlatform.ts`:类型 `PlatformConfig` / `PlatformTestResult`;方法 `list / create / update / delete / test`,从响应壳剥 data
- [x] **MP-2** 在 `api/src/index.ts` 导出
- [x] **MP-3** `packages/app/src/store/modelPlatform.ts`:RQ keys + helper(写后 setQueryData + invalidate models query)

### 2.2 P1 — 视图组件

- [x] **MP-4** `ProviderCard.tsx`:logo / 名 / 描述 / 模型标签插槽 / 右下 ⚙️ / 整卡 grayscale 在禁用时
- [x] **MP-5** `ModelTagsRow.tsx`:maxVisible(默认 3)+ DropdownMenu「更多 ▼」+ 选中 active 高亮 + 选中即调 `setModel`
- [x] **MP-6** `PlatformSettingsDialog.tsx`:表单(platform_type / api_base_url / api_key / api_proxy / api_concurrencies / enabled / auto_detect_model / disabled_models)+ 测试按钮(显示 reachable + detected)+ 一键导入检测到的模型 + 删除
- [x] **MP-7** `PlatformCreateDialog.tsx`:从空开始建新厂商,完成后高亮新卡片
- [x] **MP-8** `VendorTabs.tsx`:推荐 / 各 platform / + 添加 / 管理 segmented;尾部加按钮槽

### 2.3 P2 — 整页接入

- [x] **MP-9** `MarketplacePage.tsx` 重写:hero(当前 model)+ VendorTabs + Card grid + Composer;按 tab 过滤 / 数据读 `useQuery('marketplace.platforms')`
- [x] **MP-10** 选中模型 → `setModel` → composer placeholder + `chat.loadModels` 自动用上(已有路径)
- [x] **MP-11** i18n:补 zh-CN/en 的 `modelMarket.platform.*` / `modelMarket.settings.*` 键

### 2.4 P3 — 验证与提交

- [x] **MP-12** typecheck + vitest 通过
- [x] **MP-13** commit + push 两仓库(本期只动前端;后端无改动)

## 3. 验收标准

1. 打开 `/marketplace`,看到厂商卡片网格(不是单模型)
2. 未配置 api_key 的厂商整卡灰 + 「未启用」徽
3. 卡片下显示 ≤ 3 个模型 chip;超出折叠到「更多 ▼」dropdown
4. 点 chip → 选中态 + composer placeholder 切到该模型
5. 卡片右下 ⚙️ → 弹窗填 base_url + api_key → 保存:**无需重启**,卡片立即转彩 + 模型 chip 立刻可点
6. 弹窗内「测试连通」→ 显示 reachable + 检测到的模型数;点「一键填入」→ 模型清单自动覆盖
7. 弹窗内「删除」→ 卡片消失(yaml seed 仍能兜底)
8. 「+ 添加厂商」→ 新建后立刻出现在 tabs 与卡片网格

## 4. 风险与回退

- **风险 1:** admin 路由要 `require_role("admin")`;非 admin 用户该如何看?
  - **应对:** 非 admin 时 `/admin/model_platforms` 401/403,前端隐藏「管理 / + 添加 / ⚙️」入口,仅展示已启用厂商的只读卡片(数据来源回退到 `/v1/models` 推断)
- **风险 2:** 平台改 enable=false 后,正在进行的 chat 是否会断?
  - **应对:** 不主动断流;后端的 `get_OpenAIClient` 每次 chat 重新读 platform,所以**新发起的请求**才会感知,正在进行的不变(符合期待)
- **风险 3:** api_key 在网络中明文?
  - **应对:** PATCH 时若 api_key 为空字符串,后端跳过更新(常见模式);UI 显示用 mask `••••` + 「展示」临时切换
- **回退:** 三层叠加保证 yaml 不被打破;DELETE 后 yaml seed 仍兜底,事故时回 yaml 即可

## 5. 进度日志

- 2026-04-26 起草规划,docs 入库
- 2026-04-26 P0 完成:`api/modelPlatform.ts` + store 编排
- 2026-04-26 P1 完成:5 个新组件,包括 settings/create dialog
- 2026-04-26 P2 完成:MarketplacePage 重写 + i18n 键
- 2026-04-26 P3 完成:typecheck 通过,commit 推送
- 2026-04-26 fix(api): 修 kbUniverse / modelPlatform 的双层 .data 解引用,列表恒空 bug
- 2026-04-26 Phase 4.1:类别目录(全部 / 推荐 / 国内 / 国外 / 本地 / 聚合)
  - 后端 `GET /admin/model_platforms/catalog` 暴露 PROVIDER_CATALOG(~60 家)+ DB 配置态合并
  - 前端 `usePlatformCatalogQuery` + `ProviderCatalogEntry` 类型;非 admin 回退 /v1/models 推断
  - 卡片右上角显示 tag 徽(推荐 / 国内 / 国外 / 本地 / 聚合 配色)
  - 前端 `EXTRA_RECOMMENDED_PIDS` 在 catalog "推荐" 标签外补丁 volcengine(豆包)/ moonshot(Kimi)/ minimax / openai / anthropic
  - logo 优先用 catalog `e.logo` 拼 `/static/model_logos/<file>`,回退到既有 logo manifest 匹配
