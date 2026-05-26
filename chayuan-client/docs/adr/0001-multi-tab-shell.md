# ADR-0001 多 Tab 工作区:内 Tab + KeepAliveOutlet

- 状态:已采纳(2026-04-25)
- 关联里程碑:M1(实现)、M5(评估"拖出原生窗口")

## 背景

参考图把"主页 / 设置 / AI 写作 …"渲染为浏览器式多 Tab,每个 Tab 有独立路由历史与状态保留(切回时不丢滚动位置、草稿、模型选择)。
当前 `Chrome` 是单页 `Sidebar + Outlet`,切路由会 unmount。

## 候选

1. **内 Tab + `KeepAliveOutlet`**:单 BrowserHistory + `tabsStore`(Zustand);每 Tab 一份 `<Outlet/>` 实例,非激活 `display:none`,通过 `MemoryRouter` per-tab 隔离 history。
2. **Tauri 多原生窗口**:每个"Tab"= 一个 Tauri webview;状态通过 Tauri events + BroadcastChannel 共享。
3. **每 Tab 独立 iframe**:隔离最彻底,但 OOM 风险高、TanStack Query 单例失效、a11y 难统一。

## 决策

采用方案 1:**内 Tab + `KeepAliveOutlet`**。

## 理由

- 单进程内存可控;TanStack Query 单例 / Zustand 单例继续工作。
- 与现有 TanStack Router、`platform-tauri` / `platform-web` 双端零摩擦。
- 拖出原生窗口的需求**目前没出现**(参考图全部展示同一 webview);M5 再评估。
- 多 webview 的 Auth / Token 同步、Tauri 窗口生命周期治理代价高,不值得在 M1 引入。

## 后果

- `tabsStore` 持久化"最近 N=8 个 Tab",冷启动恢复。
- Tab 私有切片(草稿、滚动、模型)放 `composerSlice[tabId]`。
- Tab 关闭 → 触发 GC slice;TanStack Query 缓存因 staleTime 自然过期。
- Tauri "拖出 Tab → 独立窗口" 留作 M5 加分项,实施时新窗口走只读跨窗 channel 复制 store snapshot。
