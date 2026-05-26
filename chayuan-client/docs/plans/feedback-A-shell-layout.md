# Block A — Shell 布局 + Tab 路由 + 全屏 + 拖出独立窗

> 关联反馈点：1（Sidebar 占满 / 顶部 Tab 从 Sidebar 右侧开始）、2（Tab 可点切换）、11（编辑器新 Tab + 可拖出独立窗）、13（编辑页全屏切换）

## 0. 现状诊断

```
当前 Shell：
  ┌─顶栏（Tab 横条 + 右侧用户头像）  ← Tab 从屏幕左边 0 起
  ├─────────────────────────────────
  │ Sidebar  │  Main                ← Sidebar 上下满，但 Tab 横条在它"上方"
  │  导航     │  内容
  │          │
  └──────────┴──────────────────────
```

**问题**：
- Tab 从屏幕左 0 起 → 视觉上 Sidebar 顶部被压扁
- 用户期望："Sidebar 是物理上下满的纵向品牌区，Tab 横条只占 Main 区上方"
- 顶部 Tab 已实现"打开 / 关闭"，但**点击 Tab 不切换路由**（已是 bug）
- 编辑器（Office / Studio）只能内嵌；想"独占大屏"或"飞出独立窗口"做不到

## 1. 目标布局

```
┌──────────┬─────────────────────────────────────────────┐
│          │  TabBar（仅 Main 区上方；左对齐到此处）     │
│          ├─────────────────────────────────────────────┤
│ Sidebar  │  Main (active Tab content)                  │
│ 上下     │                                             │
│ 占满     │                                             │
│          │                                             │
└──────────┴─────────────────────────────────────────────┘
```

## 2. 实现方案

### 2.1 DOM 结构调整

**当前** `apps/web/src/App.tsx` / `packages/app/src/features/shell/RootShell.tsx`：

```tsx
<div className="grid grid-rows-[40px_1fr]">
  <TabBar />
  <div className="grid grid-cols-[280px_1fr]">
    <Sidebar />
    <Main />
  </div>
</div>
```

**目标**：

```tsx
<div className="grid h-screen grid-cols-[280px_1fr]">
  <Sidebar data-shell="sidebar" />
  <div className="grid min-h-0 grid-rows-[40px_1fr]">
    <TabBar data-shell="tab-bar" />
    <Main data-shell="main" />
  </div>
</div>
```

**关键约束**：
- 外层 `h-screen` 确保占满；内层 `grid` 让 Sidebar 自然撑高
- `data-shell="*"` 给 `cy-embed-mode` CSS 钩子用（已落地，无需改）
- `min-h-0` 防止内容溢出导致 Sidebar 被压扁（grid 子项默认不可滚动）

### 2.2 Tab 点击切换路由

**当前** TabBar 内部用 `tabsStore.setActive(tabId)` 切换 active，但 `route` 没跟着变，浏览器地址栏不变。

**修复**：

```ts
// packages/app/src/features/shell/tabsStore.ts
setActive: (id: string) => {
  set({ activeId: id });
  const tab = get().tabs.find(t => t.id === id);
  if (tab) {
    // 浏览器历史同步
    window.history.pushState({}, '', tab.path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }
},
```

**反向**：popstate（浏览器前进 / 后退）→ tabsStore 找匹配 Tab → setActive

```ts
window.addEventListener('popstate', () => {
  const path = window.location.pathname;
  const tab = get().tabs.find(t => t.path === path);
  if (tab) set({ activeId: tab.id });
});
```

**特殊情况**：路径不在已开 Tab 中（如直接 URL 访问 `/space/abc/studio`）→ 自动 `open(path)`。

### 2.3 全屏单独打开（反馈 13）

**触发**：编辑页右上角 ⛶ 图标 / 快捷键 `F11` / 顶栏菜单"专注模式"。

**实现**：仅 CSS 切换 + tabsStore 状态：

```tsx
// useFullscreenWorkspace.ts
const [fs, setFs] = React.useState(false);
React.useEffect(() => {
  document.body.classList.toggle('cy-fullscreen', fs);
  return () => document.body.classList.remove('cy-fullscreen');
}, [fs]);
```

```css
/* globals.css */
body.cy-fullscreen [data-shell="sidebar"],
body.cy-fullscreen [data-shell="tab-bar"] {
  display: none !important;
}
body.cy-fullscreen [data-shell="main"] {
  height: 100vh !important;
}
```

**回退**：再次点击 ⛶ / `Esc` → `setFs(false)` → 恢复。同 `cy-embed-mode` 一脉相承。

### 2.4 拖出独立窗口（反馈 11）

**两条路径**：

| 形态 | 实现 | 共享状态 |
| --- | --- | --- |
| **Tauri 桌面** | `WebviewWindow.new(label, { url })` 开新窗 | 走 IPC 同步主窗口的 store |
| **Web** | `window.open(url, '_blank', 'popup,width=1280,height=860')` | 用 `BroadcastChannel('chayuan')` 跨窗同步 |

**抽象**：

```ts
// packages/platform-shared/src/window.ts
export interface WindowApi {
  openWorkspace(opts: { label: string; url: string; title?: string }): Promise<void>;
  closeWorkspace(label: string): Promise<void>;
}

// platform-tauri / platform-web 各自实现
```

**Tab 拖出手势**：

```tsx
<TabItem
  onDragStart={(e) => {
    e.dataTransfer.setData('cy/tab', tab.id);
  }}
  onDragEnd={(e) => {
    if (e.dataTransfer.dropEffect === 'none' &&
        Math.abs(e.clientY - rect.bottom) > 200) {
      // 拖出 200px 即视为飞出
      windowApi.openWorkspace({
        label: `tab-${tab.id}`,
        url: `${tab.path}?detached=1`,
      });
      tabsStore.close(tab.id);
    }
  }}
/>
```

**收回**：独立窗口工具栏有"返回主窗" → 调 `windowApi.closeWorkspace(label)` → 主窗口 `tabsStore.open(path)` 重新接管。

**跨窗状态同步**：

```ts
// useCrossWindowSync.ts —— 在主窗口与独立窗口都挂
const channel = new BroadcastChannel('chayuan');
channel.onmessage = (e) => {
  if (e.data.type === 'apps-invalidate') queryClient.invalidateQueries({ queryKey: ['ai-space', 'apps'] });
  if (e.data.type === 'task-updated') queryClient.invalidateQueries({ queryKey: ['ai-space', 'task', e.data.token] });
  // ... 关键 query key 列表
};
// 写入端：mutation onSuccess → channel.postMessage(...)
```

## 3. 路由 / Tab 模型增量

`tabsStore.Tab` 增字段：
```ts
interface Tab {
  id: string;
  path: string;
  title: string;
  icon: string;
  /** 'main-window' | 'detached' */
  windowLabel?: string;
  /** 当前是否全屏（仅 main-window 有效） */
  fullscreen?: boolean;
  /** 上次访问时间（多 Tab 时按 LRU 摆放） */
  lastVisited?: number;
}
```

## 4. 工程量

| 项 | 工程量 |
| --- | --- |
| Shell DOM 重排 + 测试 | 0.3w |
| Tab 路由双向同步 | 0.3w |
| 全屏切换 | 0.2w |
| Tauri WebviewWindow 接入 + IPC 状态同步 | 0.5w |
| Web BroadcastChannel sync | 0.2w |
| **合计** | **1.5w** |

## 5. 待你确认

- [ ] 顶部 Tab 是否仅显示"已打开工作区"（推荐），还是同时承担二级导航？【仅显示已打开工作区】
- [ ] 全屏快捷键 `F11` 还是 `Cmd+\`？【F11 第一次可以提示 要有个图标 点击可以全屏】
- [ ] 独立窗口默认尺寸 1280×860 还是上次记住？【上次记住】
- [ ] Tab 拖出阈值 200px 合理吗？【合理】
- [ ] Web 端不支持 Tauri WebviewWindow 时退化到 `window.open` 还是直接禁用拖出？【退化到winodw.open】
