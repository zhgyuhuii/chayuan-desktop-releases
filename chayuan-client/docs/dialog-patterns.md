# 模态框使用规约 — 必读

> **TL;DR:** 一律用 `@chayuan/ui` 的 `<Dialog> + <DialogContent>` (Radix 封装),
> 不要写 `<div className="fixed inset-0 z-50 ...">` 手写模态。

## 为什么

我们在 2026-05 复现到一个真实 bug:`MountWizard` 的输入框无法输入、按钮无法点击。
根因:它用了手写的 `<div className="fixed inset-0 z-50 ...">`,但被嵌在多层
父容器中(`Chrome > main > TabHost > KeepAliveOutlet > AnnotationPage > tab-content > DataMountsPanel`)。
其中某一层(`KeepAliveOutlet` 用 `position: absolute`)与 z-index 叠加,导致:

* `fixed` 元素**不再相对于 viewport** 定位(若任意祖先有 `transform`/`filter`/`will-change`)
* 即使位置正确,事件被同 z-index 的兄弟节点截获
* Esc / 点遮罩关闭丢失
* 焦点不进表单元素 → 输入失效

**Radix Dialog 用 `<Portal>` 把内容渲染到 `document.body` 之下,完全绕过这条
栈上下文链**;同时自带焦点陷阱、`aria-modal`、Esc 处理。

## 正确用法

```tsx
import { Dialog, DialogContent, DialogTitle, cn } from '@chayuan/ui';

export const MyModal: React.FC<{ open: boolean; onClose(): void }> = ({ open, onClose }) => (
  <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
    <DialogContent
      className={cn(
        // 默认 max-w-lg;需更宽就覆盖
        'max-w-2xl max-h-[88vh] overflow-y-auto p-4',
      )}
    >
      <DialogTitle className="text-base font-semibold">
        标题(a11y 必需,不写 Radix 会 warn)
      </DialogTitle>
      {/* 主体 */}
    </DialogContent>
  </Dialog>
);
```

关键点:
1. **`<DialogTitle>` 必须有**(可视隐藏也行,但 a11y 不能省)
2. **不要再加 `<button>关闭</button>`**,Radix 自带右上 ✕
3. 自定义 className 覆盖默认 `max-w-lg`,但**不要**改 `fixed left-1/2 top-1/2`
4. 嵌套的内容如有 `pr-12` 给关闭按钮让位

## 错的写法

```tsx
{open && (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
    <div className="...">  {/* 内容 */}
      <button onClick={onClose}>关闭</button>
    </div>
  </div>
)}
```

这类写法在简单页面可能 work,但**只要嵌入到 tab/dialog/popover 里就破**。
不要赌运气。

## 已知历史欠债

这些组件还是手写 fixed,后续逐步迁移:

| 文件 | 风险 |
|---|---|
| `space/AnnotateDialog.tsx` | 中(嵌在 AppStudio 里) |
| `space/KeysSection.tsx` × 3 | 中 |
| `space/TestSuiteSection.tsx` | 低(独立页) |
| `space/AppStudioPage.tsx` | 低(drawer) |
| `space/ImportExportDialog.tsx` | 中 |
| `space/TemplateTryDialog.tsx` | 中 |
| `space/PublishDialog.tsx` | 低 |
| `space/AppGalleryPage.tsx` | 低 |
| `space/flow/NodeDebugDrawer.tsx` | 中 |
| `kb/detail/ImageKbDetail.tsx` | 低(图片大图) |
| `space/VersionsDialog.tsx` | 低 |
| `space/EtagConflictDialog.tsx` | 中 |
| `kb/detail/DocumentKbDetail.tsx` | 中 |
| `space/ShareDialog.tsx` | 低 |

已迁移:`MountWizard`、`RuntimeCenter` (RuntimeInstallWizard)、`VendorHeroStrip` (全部厂商弹窗)。

## 看到这模式 = 拒绝合并

PR review 时看到 `<div className="fixed inset-0 z-...">` 直接打回,
让作者用 Dialog 重写。
