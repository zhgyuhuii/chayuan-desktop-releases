# Storybook 9 + Chromatic 视觉回归

Storybook 装在 `packages/app` 里，能同时覆盖 UI 原子件（@chayuan/ui）与业务组件（features/*）。

## 启动

```bash
pnpm storybook
# → http://localhost:6006
```

## 已覆盖的 stories

| 文件 | 涉及组件 | 关键变体 |
|---|---|---|
| `Button.stories.tsx` | Button | default / outline / destructive / icon |
| `Composer.stories.tsx` | Composer + AttachmentBar | idle / streaming / 含附件 |
| `MessageBubble.stories.tsx` | MessageBubble | user / assistant / streaming / reasoning / tool / error / interrupt |
| `ToolCallCard.stories.tsx` | 工具卡注册中心 | web_search / weather / calc / generic |
| `ArtifactPanel.stories.tsx` | ArtifactPanel | code / mermaid / json |

> 业务 story 通过 `.storybook/preview.tsx` 注入了 stub Platform + QueryClient，
> 让任何依赖 PAL 的组件不会因「platform 未注入」抛错。

## 视觉回归（Chromatic）

```bash
# 一次性：在 chromatic 上建项目，拿到 token
export CHROMATIC_PROJECT_TOKEN=xxx

# 跑（CI 也用同样命令）
pnpm chromatic
```

`--exit-zero-on-changes` 让 PR 上有视觉差异时不阻塞 CI；review 在 chromatic UI 完成。

## 添加新 story

1. 写组件（features/xxx/...）
2. 在 `packages/app/src/stories/` 加 `Xxx.stories.tsx`
3. 用真实业务类型，不要写 mock 类型，保证 story 与生产代码漂移最小
4. 涉及网络的组件：注入 mock 数据（不要 fetch 真接口）；TanStack Query 已通过 preview 提供 client
