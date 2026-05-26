# ADR-0002 复用 `chayuan-server-frontend` 的视觉资产

- 状态:已采纳(2026-04-25)
- 关联里程碑:M0(i18n / logo manifest)、M3(模型广场 logo)

## 背景

`chayuan-server-frontend`(Vue 原型)已实现 6 个一级页 + 6 套 i18n locale(`zh-CN/en/ja/ko/fr/de`)+ 模型 logo manifest。重新写一遍属于浪费。

## 决策

**视觉资产可复用,业务逻辑不复用**。具体清单:

| 资产 | 源 | 目的地 | 处理 |
|---|---|---|---|
| i18n locales × 6 | `src/i18n/locales/*.ts` | `packages/i18n/src/locales/` | 拷贝后转 i18next + ICU,按 namespace 切分 |
| 模型 logo manifest 解析 | `src/api/models.ts` `loadLogoManifest/resolveLogoUrl` | `packages/api/src/models/logo-manifest.ts` | 直接搬,用 `platform.net.fetch` 替 `fetch` |
| AI Space fixture(数据) | `src/mock/ai-space.ts` `MOCK_AI_APPS` | `packages/api/src/fixtures/ai-space.ts` | 作为 ADR-04 伪服务的内置目录 |
| SVG 图标(能力快捷条/AI Space 图标) | `src/components/**/*.svg`(若有) | `packages/ui/src/icons/` | 单独梳理一次,缺失则用 `lucide-react` 兜底 |

不复用:Vue 组件源码、Element Plus 依赖、Pinia store、`composables/*`、Vite 配置。

## 理由

- i18n 字典是"内容劳动",直接搬可省 ~1 周。
- logo manifest 由 `chayuan-server` 提供静态文件,前端解析逻辑稳定且 server-frontend 已经经历过路径兜底打磨(`/img/...` 同源 vs `<base>/img/...` 直连)。
- AI Space fixture 是设计师整理过的 App 元数据(图标 + 文案 + 角标),拷过来即可上线。

## 后果

- `packages/i18n` 引入 `i18next` + `i18next-icu`;按 namespace 异步加载。
- 每个迁入文件头部加注释 `// Imported from chayuan-server-frontend@<commit-or-date>` 便于后续同步。
- 6 套 locale 的"key 完整度"以 `zh-CN` 为基线,缺失退回 zh-CN。
