# MSIX Assets (Win arm64)

> 这里放 MSIX 包要求的 logo / tile png。源 SVG 在仓库; CI 通过 ImageMagick
> / inkscape 在打包前转出对应尺寸 png; **png 不进 git**。

## 必需文件

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `Square44x44Logo.png` | 44 × 44 | 任务栏图标 |
| `Square150x150Logo.png` | 150 × 150 | 开始菜单中等磁贴 |
| `Wide310x150Logo.png` | 310 × 150 | 开始菜单宽磁贴 (可选) |

## 如何生成

```powershell
# 在 packaging/windows/Assets/ 下放 chayuan-icon.svg
inkscape chayuan-icon.svg -o Square44x44Logo.png   --export-width=44   --export-height=44
inkscape chayuan-icon.svg -o Square150x150Logo.png --export-width=150  --export-height=150
inkscape chayuan-icon.svg -o Wide310x150Logo.png   --export-width=310  --export-height=150
```

`build_win_arm64.ps1` 会把整个 Assets/ 复制到 staging 根。
