# ADR-0005 样式栈:Tailwind + CSS variables + tokens.ts(不引 Vanilla-Extract)

- 状态:已采纳(2026-04-25)
- 关联里程碑:M0

## 背景

重构需要"主题 / 字号 / 跨端一致"的设计 Token 系统。考察了:

1. **Tailwind v4 `@theme` + CSS variables + 一份 `tokens.ts` 作 TS 常量**(现状)
2. **Vanilla-Extract**(`.css.ts` 编译到零运行时 CSS)
3. **CSS Modules + PostCSS**

## 决策

继续走方案 1。**不引入 Vanilla-Extract**。

## 理由

- 现状所有组件、Storybook、Tauri/Vite 配置都按 Tailwind + Radix + className 写好。引入 VE 等于:
  - 多一个构建插件(`@vanilla-extract/vite-plugin`)+ `.css.ts` 编译期。
  - Storybook 要再配 vite plugin;Tauri 打包多一段处理。
  - 新老组件出现 "`.css.ts` vs `className`" 双轨,review 心智负担长期为正。
- VE 的核心收益(类型安全 token、零运行时)在我们的方案里 90% 复制得出:
  - `packages/design-tokens/src/tokens.ts` 输出 TS 常量(类型安全)
  - 同源 `tokens.css` 输出 CSS variables(运行时主题切换)
  - 同源 `tokens.dtcg.json`(给 Figma / 设计师消费)
- DTCG JSON 已是 W3C Design Tokens Community Group 标准;Tailwind v4 `@theme` 直接读 CSS variables,与 Storybook / Tauri 全栈兼容。

## 拒绝 VE 的临界条件

如果未来出现以下任一,可重新讨论:

- 整个 `ui` 包要重写为 zero-runtime CSS-in-TS(项目级决策)
- 跨端 RSC / SSR 引入,需要严格 CSS-in-JS 抽取
- 团队规模扩到 10+ 人,需要"组件作者无法写错 token"的强约束

## 后果

- M0 落 `packages/design-tokens`:
  - `tokens.ts`:`color`、`radius`、`spacing`、`shadow`、`glow`、`typography` 五大组
  - `tokens.css`:浅 / 深双套 CSS variables(`:root[data-theme=light]` / `[data-theme=dark]`)
  - `tokens.dtcg.json`:DTCG 标准导出(自动从 `tokens.ts` 生成)
- Tailwind v4 `theme.css` 用 `@theme` 直接 import `tokens.css`。
- Storybook 全局装饰器读 CSS variables 切主题。
