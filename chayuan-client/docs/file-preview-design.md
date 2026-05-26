# 知识库文件预览 — 设计与实施方案

> Owner: 察元 AI 客户端
> Status: design + implementation
> 关键词: dock / float / collapse / detach window / multi-format renderer

## 1. 背景与目标

知识库 (`/kb`) 中的 `DocumentKbDetail` 现在通过 `window.open(/knowledge_base/preview_doc?...)` 把文件 **抛到外部浏览器**预览，体验割裂、每开一个文件多一个标签页、无法和正在进行的对话联动。

参考图 `界面参考/依靠漂浮折叠.png`(微信文件助手风格)：主窗口左侧是文件来源列表，右侧"漂浮"出预览面板，两窗口之间有一根**折叠手柄**，预览面板边缘有一列**悬浮工具栏**(收藏/下载/反馈)。

我们要实现的是**面向桌面客户端**的、支持四种工作模式的统一文件预览中枢:

| 模式 | 行为 | 适用场景 |
|---|---|---|
| **Dock** 停靠 | 紧贴主窗口右侧的固定面板,共享窗口位置 | 默认,边对话边看文档 |
| **Float** 漂浮 | 应用内自由拖拽的浮窗,可调大小 | 多文件比较、临时查看 |
| **Collapse** 折叠 | 折叠成 28px 宽的纵向手柄,只露文件名缩写 | 暂时让位、保持上下文 |
| **Detach** 独立窗口 | Tauri `WebviewWindow` 弹出全新 OS 窗口,可拖到副屏 | 沉浸阅读 / 跨屏协作 |

并且要支持的格式:**Word(.docx/.doc)、Excel(.xlsx/.xls)、PowerPoint(.pptx/.ppt)、WPS 系列(.wps/.et/.dps)、Markdown(.md)、纯文本(.txt/.log/.json/.yaml)、PDF、图像(.png/.jpg/...)、视频(.mp4/.webm)、音频(.mp3/.wav)**。

## 2. 架构总览

```
┌──────────────────────── Shell (apps/desktop, apps/web) ─────────────────────┐
│                                                                              │
│  Chrome (TabHost)         ┌──────────────────────┐    PreviewPanel(portal)   │
│  ┌──────────────┐         │  KbDetailDialog      │    ┌────────────────────┐ │
│  │ /kb 工作台   │  ──→    │  DocumentKbDetail    │ ──→│  Toolbar           │ │
│  └──────────────┘         │  onPreview(file)     │    │  ┌──────────────┐  │ │
│                           └──────────────────────┘    │  │ Renderer     │  │ │
│                                       │               │  │ (lazy chunk) │  │ │
│                                       ▼               │  └──────────────┘  │ │
│                              usePreviewStore          │  ActionRail        │ │
│                                       │               │  Resizer           │ │
│                                       └───── publish ─┘                    │ │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │ detach (mode=window)
                                       ▼
                ┌──────────────────────────────────────────┐
                │ Tauri WebviewWindow / web popup          │
                │  Route: /preview-window?ku=...&file=...  │
                │  渲染同一份 PreviewPanel 的 standalone    │
                │  模式 (不带 toolbar 的 docking 按钮)      │
                └──────────────────────────────────────────┘
```

### 关键设计决策

1. **Headless 状态机**:`usePreviewStore` (Zustand) 只持有"当前预览的文件 + 模式 + 尺寸";UI 是状态的纯函数,这样**主窗 + 独立窗**两份 React 树可以订阅同一个 store(独立窗用 BroadcastChannel + 内嵌 store 双向同步)。
2. **Renderer 注册表**:`registry.ts` 把 `kind → React.lazy(() => import('./renderers/XxxRenderer'))` 集中注册。**新增格式 = 加一行**。每个 renderer 是独立 chunk,首屏不加载 mammoth / xlsx / pdfjs 这些大依赖。
3. **平台抽象层**:`Platform.preview?` 是一个新 capability。Tauri 走 `WebviewWindow.create`,Web 走 `window.open`。业务层 `getPlatform().preview?.openWindow()` 做 capability check,无能力时退化为 floating 模式。
4. **统一 fetch 桥**:`useFileUrl(file)` / `useFileBlob(file)` 集中处理 `baseURL + token` 拼接、AbortController 取消、Blob 缓存(LRU,跨 renderer 命中)。
5. **性能**:
   - Renderer code-split,大依赖按需加载
   - `IntersectionObserver` 只在面板可见时才解析 docx/xlsx
   - Blob 级 LRU(默认 8 项),关闭文件时 `URL.revokeObjectURL` 释放内存
   - SheetJS 大文件 Web Worker 解析(`new Worker(new URL('./xlsxWorker.ts', import.meta.url))`)
6. **多并发**:
   - 同时打开多个 detached window:每个窗 own 自己的子 store,主窗用 `BroadcastChannel('cy.preview')` 广播 `open/close/mode-change`
   - In-app 多 floating panel:Phase 2(本期暂支持单个),store 改 `current: PreviewFile[]` 即可
7. **趣味化 UX**:
   - 折叠/展开有 spring 动效(CSS transform)
   - 拖拽手柄 hover 显示文件名缩写;放手立即吸附
   - 检测到 .pptx → 渲染时显示加载彩蛋"正在拼图…"

## 3. 模块划分

```
packages/app/src/features/preview/
├─ index.ts                      # 公共导出
├─ types.ts                      # PreviewFile / PreviewKind / PanelMode
├─ previewStore.ts               # Zustand: open/close/mode/collapse/size
├─ detectKind.ts                 # ext + mime → 'pdf'|'docx'|'image'|...
├─ useFileUrl.ts                 # token 化 URL + AbortController
├─ blobCache.ts                  # LRU<url,Blob>
├─ PreviewMount.tsx              # 顶层挂载点(放 Shell 里)
├─ PreviewPanel.tsx              # 主体(dock / float / collapse 三态)
├─ PreviewToolbar.tsx            # 顶栏:icon, name, mode toggles, popout, close
├─ PreviewActionRail.tsx         # 浮动右侧 mini rail(download / fit / refresh)
├─ Resizer.tsx                   # 左/右/角拖拽手柄
├─ broadcast.ts                  # BroadcastChannel 桥(主窗 ↔ detached)
├─ standalone/
│   └─ PreviewStandaloneRoute.tsx # 独立窗口的 React 入口(挂在 router /preview-window)
└─ renderers/
   ├─ index.ts                   # registry: lazy map
   ├─ ImageRenderer.tsx
   ├─ VideoRenderer.tsx
   ├─ AudioRenderer.tsx
   ├─ PdfRenderer.tsx
   ├─ MarkdownRenderer.tsx
   ├─ TextRenderer.tsx           # shiki 高亮
   ├─ DocxRenderer.tsx           # mammoth (lazy)
   ├─ XlsxRenderer.tsx           # SheetJS (lazy + worker)
   ├─ PptxRenderer.tsx           # 后端转 pdf 优先,前端 fallback
   ├─ FallbackRenderer.tsx       # 不支持 → 下载/外部打开
   ├─ ErrorRenderer.tsx
   └─ LoadingRenderer.tsx
```

平台层扩展:

```
packages/platform-shared/src/index.ts
  ├─ PreviewWindowSpec         # url / title / size / position
  └─ PreviewWindowApi          # openWindow(spec)→handle (close/focus/onClose)

packages/platform-tauri/src/index.ts
  └─ tauriPreviewWindow        # 用 @tauri-apps/api/webviewWindow

packages/platform-web/src/index.ts
  └─ webPreviewWindow          # window.open
```

路由扩展:

```
packages/app/src/router/index.tsx
  └─ /preview-window  (新顶层路由,bypass Chrome,渲染 PreviewStandaloneRoute)
```

## 4. 关键流程

### 4.1 打开预览(主窗)
1. `DocumentKbDetail` 点 👁:`usePreviewStore.open({ kbName, fileName, ext })`
2. `PreviewMount` 订阅 store,有 current 则渲染 `PreviewPanel`
3. `PreviewPanel` 按 ext 选 renderer(lazy import 触发 chunk 加载)
4. Renderer 调 `useFileUrl()` 拿到带 token 的 URL → 渲染

### 4.2 模式切换
- **dock ↔ float**:布局算法不同,store.mode 切换 + Framer-style transform 过渡
- **collapse**:store.collapsed = true,panel 宽度 → 28px,渲染 `<CollapsedRail>`
- **detach**:调 `platform.preview.openWindow({ url: '/preview-window?...' })`,主窗 `current = null`

### 4.3 独立窗口
- 新窗启动后:挂同一份 platform/auth/queryClient(因为 token 存 localStorage 共享),路由解析 query param → store.open
- 主窗与独立窗通过 `BroadcastChannel('cy.preview')` 同步关闭事件
- 关独立窗 → 主窗收到 `close`,store 不重新打开(避免回弹)

### 4.4 Renderer 通用 props

```ts
interface RendererProps {
  file: PreviewFile;
  url: string;          // 已经拼好 token 的可直接 GET 的 URL
  fetchBlob: () => Promise<Blob>;  // 缓存版
  onReady(): void;
  onError(err: Error): void;
}
```

### 4.5 格式映射

| 扩展名 | renderer | 说明 |
|---|---|---|
| pdf | PdfRenderer | iframe + Chromium PDF Viewer(零依赖,最快) |
| png/jpg/jpeg/webp/gif/avif/svg/bmp | ImageRenderer | 缩放/平移/100%/适应 |
| mp4/webm/mov/mkv | VideoRenderer | `<video>` controls |
| mp3/wav/flac/ogg/m4a | AudioRenderer | `<audio>` + 波形(可选) |
| md/markdown | MarkdownRenderer | marked + DOMPurify + 代码块 shiki |
| txt/log/json/yaml/xml/csv/ini/conf | TextRenderer | shiki(自动语言探测) |
| docx | DocxRenderer | mammoth → HTML;数学公式 KaTeX(可选) |
| doc | FallbackRenderer | 旧二进制格式;提示请用 docx + 下载按钮 |
| xlsx/xls | XlsxRenderer | SheetJS(读),Web Worker 解析,sheet tabs |
| pptx | PptxRenderer | 优先后端 to-pdf,前端兜底解析(可选) |
| ppt/wps/et/dps/wpt | FallbackRenderer | 二进制 office;下载或外部打开 |

## 5. 性能与并发

- **首屏零成本**:`PreviewMount` 仅在 store.current 不为 null 时挂载;renderer lazy。
- **大文件**:>= 30 MB 时,Renderer 提示"较大文件可能较慢"+ 提供"独立窗口打开"快捷按钮(渲染独立窗口可以分摊主窗 JS 主线程)。
- **Web Worker 解析**:xlsx 走 worker,主线程不阻塞滚动。
- **AbortController**:切换文件时立刻 abort 旧 fetch / 旧 worker。
- **Blob 缓存**:同一 URL LRU(默认 8 项 / 128 MB 上限),关闭后 5 秒延迟回收(用户可能立刻又点)。

## 6. UX 细节

- 折叠手柄:左侧 12 × 60 px 凹槽 + 中间一根 1px 灰线(参考图)
- 浮窗模式:窗体投影 `shadow-2xl`,顶部抓握栏 12px 高、含 `:active { cursor: grabbing }`
- 进入/离开:`@keyframes` slide-in-right + opacity,200ms 缓动
- 工具栏:hover 才显示完整 label,默认 icon-only,极简
- 拖拽 dock → float:从屏幕右边缘往左拖超过 80px 自动切到 float
- 拖拽 float → window:窗体拖出主窗外 60px 触发"是否独立窗口"toast(微信也是这个 UX)
- 主题:沿用 design-tokens 的 `--cy-*` 变量,自动暗色

## 7. 路由与状态共享

| 来源 | 触发 | 行为 |
|---|---|---|
| 主窗 store | `open(file, mode='dock')` | 主窗渲染 panel |
| 主窗 store | `setMode('window')` | platform.preview.openWindow + main store.current = null |
| 独立窗 url | `?ku=&file=` | 独立窗 init 时 store.open(parse) |
| BroadcastChannel | `'close'` | 关心的窗都执行清理 |
| BroadcastChannel | `'redock'` | 反操作:独立窗主动关闭并通知主窗 redock |

## 8. 安全

- 渲染 markdown/docx → DOMPurify 过滤
- 图片/视频 src 仅允许 `https?:|blob:|data:`
- 独立窗口 CSP 与主窗一致(Tauri `tauri.conf.json` 已 allow `connect-src` 后端 host)

## 9. 落地步骤

1. **平台层**:加 `PreviewWindowApi` 接口 + Tauri/Web 实现
2. **依赖**:`packages/app/package.json` 加 `mammoth`、`xlsx`(devDependencies 不行,要 dependencies)
3. **核心**:types / store / detectKind / useFileUrl / blobCache / broadcast
4. **UI**:`PreviewMount`、`PreviewPanel`(三态)、`Toolbar`、`ActionRail`、`Resizer`
5. **renderers**:Image / Video / Audio / Pdf / Markdown / Text / Fallback(零新依赖)
6. **renderers (重)**:Docx(mammoth) / Xlsx(sheetjs)
7. **路由**:`/preview-window` 顶层 + `PreviewStandaloneRoute`
8. **接入**:`DocumentKbDetail.onPreview` → `usePreviewStore.open(...)`
9. **集成**:`Shell` 挂 `<PreviewMount />`
10. **打磨**:动效、键盘快捷键(Esc 关、F 全屏切独立窗口、`[` `]` 切折叠/dock)

> Phase 2(后续):`ImageKbDetail` / `StructuredKbDetail` 命中行的 citation 也走这个统一面板。

## 10. 实施结果

落地的文件清单(本期完成):

```
packages/platform-shared/src/index.ts          + PreviewWindowSpec / PreviewWindowHandle / PreviewWindowApi
packages/platform-tauri/src/index.ts           + tauriPreview (WebviewWindow)
packages/platform-web/src/index.ts             + webPreview (window.open)
apps/desktop/src-tauri/capabilities/default.json
                                               + windows: ['main', 'preview-*'] + create-webview-window 权限

packages/app/src/features/preview/
├─ types.ts                                    PreviewFile / PreviewKind / PanelMode
├─ detectKind.ts                               扩展名/MIME 路由
├─ blobCache.ts                                LRU(8 项 / 256 MB)
├─ useFileUrl.ts                               token 化 URL + fetchBlob hook
├─ previewStore.ts                             zustand:open/close/mode/collapsed/几何
├─ broadcast.ts                                BroadcastChannel('cy.preview')
├─ PreviewMount.tsx                            顶层挂载点(portal + ESC + beforeunload)
├─ PreviewPanel.tsx                            主体(dock/float/collapse + Resizers + ErrorBoundary)
├─ PreviewToolbar.tsx                          history/dock/float/collapse/popout/close
├─ PreviewActionRail.tsx                       右侧浮动 mini rail(刷新/复制/外打开/下载)
├─ standalone/PreviewStandaloneRoute.tsx       /preview-window 入口
├─ index.ts                                    公共导出
└─ renderers/
   ├─ index.ts                                 lazy registry
   ├─ ImageRenderer.tsx                        缩放/平移/双击/适应
   ├─ VideoRenderer.tsx                        <video controls>
   ├─ AudioRenderer.tsx                        <audio controls> + 渐变背景
   ├─ PdfRenderer.tsx                          iframe 直吃 chromium PDF viewer
   ├─ MarkdownRenderer.tsx                     marked + DOMPurify
   ├─ TextRenderer.tsx                         shiki 语法高亮(>500 KB 退化)
   ├─ DocxRenderer.tsx                         mammoth → HTML(动态 import)
   ├─ XlsxRenderer.tsx                         SheetJS + sheet tabs(动态 import,1000 行截断)
   ├─ PptxRenderer.tsx                         iframe 优先 + 8s 超时回退 fallback
   ├─ FallbackRenderer.tsx                     友好兜底(下载 / 系统打开)
   ├─ LoadingRenderer.tsx
   └─ ErrorRenderer.tsx

packages/app/src/router/index.tsx              + /preview-window 顶层路由
packages/app/src/Shell.tsx                     + <PreviewMount />
packages/app/src/features/kb/detail/DocumentKbDetail.tsx
                                               改:点击行 / 👁 → usePreviewStore.open(...)
packages/app/package.json                      + mammoth ^1.9.0 / xlsx ^0.18.5
```

入口点:

```ts
import { usePreviewStore } from '@chayuan/app/.../preview';
usePreviewStore.getState().open({
  source: 'kb-doc',
  kbName,
  fileName,
  fileSize,
});
```

四种模式由用户自由切换:

- **Dock**:默认贴右,左边缘可拖拽宽度(360–960 px),宽度持久化
- **Float**:任意拖动,角部 resize,几何持久化
- **Collapse**:dock 下点折叠 → 28px 边缘条,点条展开
- **Window**:点 ⤴ 弹出 Tauri WebviewWindow(web 退化为 window.open);
  关闭时 BroadcastChannel 通知主窗清状态

下次手动 `pnpm install` 即可装上 mammoth + xlsx 启用 docx/xlsx 解析。

