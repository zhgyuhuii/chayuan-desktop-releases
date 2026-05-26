# ADR-0004 AI Space:前端伪服务 + 内置 fixture

- 状态:已采纳(2026-04-25)
- 关联里程碑:M3(实现)、未来视情况(后端落地真实接口)

## 背景

参考图的 "AI Space" 是应用商店形态:推荐 / 智能体专区 / 绘画设计 / 辅助写作 / 办公提效 / 影音编辑 / 生活娱乐 + New/Hot 角标 + 安装/进入。
`chayuan-server-frontend` 期望 `/api/ai/space/apps` `/api/ai/space/categories` `POST /api/ai/space/apps/{id}/install|uninstall` 端点,但**`chayuan-server` 不存在该 module**(全代码 grep 无 `ai_space` / `/api/ai/space`)。

## 决策

**M3 不依赖后端**。前端实现一个**与目标 contract 同形**的伪服务,数据来自:

1. **内置 fixture**:从 `chayuan-server-frontend/src/mock/ai-space.ts` 迁入 `packages/api/src/fixtures/ai-space.ts`(ADR-02)。
2. **本地"已安装"状态**:走 `platform.kvStore.set('ai-space.installed', string[])`。
3. **远端覆盖(可选)**:若 `GET /api/ai/space/apps` 返 200 → 用远端结果覆盖 fixture;404/缺接口 → fallback 到 fixture。

API 形状(与 server-frontend 完全一致,前端代码可平滑切换):

```ts
listAIAppsApi(params?): Promise<AIApp[]>
listAIAppCategoriesApi(): Promise<AIAppCategory[]>
installAIAppApi(id): Promise<void>
uninstallAIAppApi(id): Promise<void>
```

"进入 App" 实际路由到 `/skill/$id`,由统一 `SkillTemplate` 渲染。

## 理由

- 后端短期没有"应用商店"领域模型(它的领域是模型/Agent/工具/KB)。
- 重构里程碑不能被后端阻塞。
- contract 同形 → 后端落地后,前端只需把"fallback 到 fixture"这一行改回"throw"。

## 后果

- `useAIAppList` `useInstallApp` 走 TanStack Query;失败回退由 query fn 内部消化,UI 无感。
- "已安装"是设备级状态,跨多端不同步(可接受,M5 视情况上 sync)。
- 后端若 M5 之后落 `ai_space_routes.py`,**前端 0 改动**自动接管。
