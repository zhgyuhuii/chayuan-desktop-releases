# 跨平台 / 跨架构兼容矩阵

察元客户端的目标:**Windows / Linux / macOS × x86_64 / aarch64** 全部受支持。
本文记录每平台的依赖、Tauri target、构建命令、已知约束与发布产物。

---

## 1. 支持矩阵

| OS | 架构 | Rust target | Tauri bundle | 状态 |
|---|---|---|---|---|
| Windows 10/11 | x86_64 | `x86_64-pc-windows-msvc` | `msi` + `nsis` | ✅ 主力 |
| Windows 10/11 | aarch64 | `aarch64-pc-windows-msvc` | `msi` + `nsis` | ✅ Win on ARM |
| Windows 7 | x86_64 | `x86_64-pc-windows-msvc` | `nsis` | ⚠️ Tauri 2 不再保证;需 WebView2 标准版 + 旧编译链 |
| macOS 10.15+ | x86_64 (Intel) | `x86_64-apple-darwin` | `dmg` + `app` | ✅ |
| macOS 11+ | aarch64 (Apple Silicon) | `aarch64-apple-darwin` | `dmg` + `app` | ✅ M1/M2/M3 |
| Linux glibc 2.31+ | x86_64 | `x86_64-unknown-linux-gnu` | `deb` + `appimage` + `rpm` | ✅ |
| Linux glibc 2.31+ | aarch64 | `aarch64-unknown-linux-gnu` | `deb` + `appimage` | ✅ Raspberry Pi 等 |

> Tauri 2 官方的最低运行环境 = WebView2 (Win) / WebKit2GTK 4.1 (Linux) / WKWebView (Mac)。

---

## 2. Web 端(纯静态)

`apps/web` 产物 = `apps/web/dist/*`(JS/CSS/HTML/SVG/PNG)。

- **零 Node 运行时依赖** —— 已审查 `packages/api/src/typed.ts` 中的 `require` 已移除
- **零 Tauri API 硬依赖** —— 仅 Tauri 端动态字符串 import,Web 端 catch 后 noop
- **i18next + i18next-icu** —— 纯前端,所有 locale 同步内联
- **WebView 兼容性**:Chromium 90+ / Safari 14+ / Firefox 90+;不支持 IE/旧 Edge

部署:nginx 反代 `chayuan-server` 同源 `/auth/*`、`/chat/*`、`/v1/*`、`/knowledge_base/*`、`/img/*`。
`apps/web/vite.config.ts` 已列出所有反代前缀,与 nginx 配置对应即可。

---

## 3. 桌面端依赖逐项

### 3.1 npm 端(`platform-tauri` 引入的所有 Tauri 插件)

| 插件 | 用途 | Win / Mac / Linux | 备注 |
|---|---|---|---|
| `@tauri-apps/api` | core invoke / events / window | ✅✅✅ | 已修 `getCurrentWindow`(Tauri 2) |
| `plugin-clipboard-manager` | 剪贴板 | ✅✅✅ | |
| `plugin-dialog` | 文件选择 | ✅✅✅ | |
| `plugin-fs` | FS 读写 | ✅✅✅ | |
| `plugin-global-shortcut` | 全局快捷键 | ✅✅✅ | Linux 需窗口管理器支持(GNOME/KDE OK) |
| `plugin-http` | 绕 CORS 的 fetch | ✅✅✅ | |
| `plugin-notification` | 系统通知 | ✅✅✅ | macOS 首次需用户同意 |
| `plugin-os` | 平台信息 | ✅✅✅ | |
| `plugin-shell` | open 浏览器 | ✅✅✅ | |
| `plugin-sql` (sqlite) | 本地数据库 | ✅✅✅ | sqlite 嵌入,无原生外部依赖 |
| `plugin-stronghold` | 加密 vault | ✅✅✅ | 纯 Rust 实现(chacha20-poly1305),无 libsodium |
| `plugin-updater` | 应用自动更新 | ✅✅✅ | 需 `pubkey` + 服务端 endpoints |
| `plugin-window-state` | 窗口位置/大小记忆 | ✅✅✅ | |
| `plugin-process` | 重启自身(updater 后) | ✅✅✅ | |

### 3.2 Cargo 原生 crate

| Crate | 用途 | 跨平台 | 跨架构 |
|---|---|---|---|
| `tauri` 2.1 | 框架 | ✅ Win/Mac/Linux | ✅ x86_64 + aarch64 |
| `serde` `serde_json` | 序列化 | ✅ | ✅ |
| `sha2` `hex` `machine-uid` | vault 密码派生 | ✅ | ✅(machine-uid 0.5 已支持 ARM) |
| `xcap` 0.0.14 | 截屏 | ✅ Win(Win32 GDI) / Mac(CGImage) / Linux(X11/Wayland) | ✅ |
| `image` 0.25 | PNG 编解码 | ✅ 纯 Rust | ✅ |

> ⚠️ `xcap` 在 Linux 下需要 X11 或 Wayland 会话;无显示服务器的纯 SSH 环境无法截屏(预期行为)。

---

## 4. 平台必装系统依赖

### 4.1 Windows
- **WebView2 Runtime**(Win11 自带;Win10 需后装)
  - 安装方式 1:用户预装 `MicrosoftEdgeWebview2Setup.exe`
  - 安装方式 2:`tauri.conf.json` 已配 `bundle.windows.webviewInstallMode = "downloadBootstrapper"`,首次启动自动下载
- **Visual C++ 运行时**(Tauri 静态链接 MSVC,通常无需额外)

### 4.2 Linux(打包/运行)
**运行时(终端用户)**:
```bash
# Debian/Ubuntu
sudo apt install -y libwebkit2gtk-4.1-0 libgtk-3-0 libayatana-appindicator3-1 librsvg2-2

# Fedora/RHEL
sudo dnf install -y webkit2gtk4.1 gtk3 libappindicator-gtk3 librsvg2

# Arch
sudo pacman -S webkit2gtk-4.1 gtk3 libayatana-appindicator librsvg
```
`tauri.conf.json` 已在 `bundle.linux.deb.depends` 声明,`apt install xxx.deb` 自动拉。

**编译时(开发/CI)**:
```bash
sudo apt install -y \
  build-essential curl wget file \
  libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev \
  librsvg2-dev libssl-dev libsoup-3.0-dev
```

### 4.3 macOS
- **Xcode Command Line Tools**:`xcode-select --install`
- **代码签名 / 公证**:发布需要 Apple Developer ID + `notarytool`
  ```bash
  export APPLE_ID=...
  export APPLE_PASSWORD=...
  export APPLE_TEAM_ID=...
  pnpm --filter @chayuan/desktop tauri build --target universal-apple-darwin
  ```
- **Universal Binary**:`--target universal-apple-darwin` 同时打 x86_64 + aarch64

---

## 5. CI 构建矩阵(GitHub Actions 推荐)

```yaml
strategy:
  matrix:
    include:
      - os: windows-latest
        target: x86_64-pc-windows-msvc
      - os: windows-11-arm   # GitHub-hosted ARM runner(2025+)
        target: aarch64-pc-windows-msvc
      - os: macos-13         # Intel
        target: x86_64-apple-darwin
      - os: macos-14         # Apple Silicon
        target: aarch64-apple-darwin
      # 推荐 universal:macos-14 上跑 universal-apple-darwin 一次出双架构
      - os: ubuntu-22.04
        target: x86_64-unknown-linux-gnu
      - os: ubuntu-22.04-arm # 或 buildjet/ARC
        target: aarch64-unknown-linux-gnu
```

每个 job 步骤:
1. `actions/setup-node` + pnpm
2. `dtolnay/rust-toolchain@stable` + `targets: ${{ matrix.target }}`
3. **Linux only**:`apt install` 编译时依赖(见 §4.2)
4. `pnpm install`
5. `pnpm --filter @chayuan/desktop tauri build --target ${{ matrix.target }}`
6. 上传 `apps/desktop/src-tauri/target/${{ matrix.target }}/release/bundle/**`

---

## 6. 本地构建命令速查

```bash
# Web(任意 OS,纯前端)
pnpm build:web

# Desktop —— 当前主机架构
pnpm --filter @chayuan/desktop tauri build

# Desktop —— 指定 target
pnpm --filter @chayuan/desktop tauri build --target x86_64-pc-windows-msvc
pnpm --filter @chayuan/desktop tauri build --target aarch64-pc-windows-msvc
pnpm --filter @chayuan/desktop tauri build --target x86_64-apple-darwin
pnpm --filter @chayuan/desktop tauri build --target aarch64-apple-darwin
pnpm --filter @chayuan/desktop tauri build --target universal-apple-darwin
pnpm --filter @chayuan/desktop tauri build --target x86_64-unknown-linux-gnu
pnpm --filter @chayuan/desktop tauri build --target aarch64-unknown-linux-gnu

# 仅出指定 bundle
pnpm --filter @chayuan/desktop tauri build --bundles msi
pnpm --filter @chayuan/desktop tauri build --bundles dmg
pnpm --filter @chayuan/desktop tauri build --bundles deb,appimage
```

> 跨架构编译(host=x86_64,target=aarch64)需要安装对应的 Rust target 与 linker:
> `rustup target add aarch64-pc-windows-msvc`(Win 下需 MSVC ARM64 工具集)
> Linux x86_64 → aarch64 用 `cross`(Docker 工具):`cargo install cross && cross build ...`

---

## 7. 已知约束

| 限制 | 影响平台 | 说明 |
|---|---|---|
| `WindowDock.setDock` | 多显示器场景按主显示器算 | M5 后续可加 `currentMonitor()` 选择目标显示器 |
| 全局快捷键 | Linux Wayland 部分 WM | GNOME 4x、KDE Wayland 已支持;Sway 需手动配置 |
| 截屏 `xcap` | Linux Wayland | 受 portal 协议限制,部分发行版需用户授权 |
| 系统通知 | macOS 首次 | 需用户在系统设置授权"通知" |
| Stronghold vault | 全平台 | 跨设备**不**同步;每台机器一份本地 vault |

---

## 8. 验证清单(发布前)

- [ ] `pnpm typecheck` 全绿
- [ ] `pnpm lint` 无警告
- [ ] `pnpm test` 全过
- [ ] `pnpm build:web` 产物可静态托管
- [ ] Windows x86_64 打包并安装运行
- [ ] Windows aarch64 打包并安装运行
- [ ] macOS Intel 打包 + 签名 + 公证
- [ ] macOS Apple Silicon 打包 + 签名 + 公证
- [ ] macOS Universal 启动后两架构均可运行
- [ ] Ubuntu 22.04 x86_64 deb / appimage 安装运行
- [ ] Ubuntu 22.04 aarch64 deb / appimage 安装运行
- [ ] 各平台首次启动 WebView2 / WebKit2GTK 安装提示
- [ ] 系统托盘 + 快捷键 + 截屏在三平台均可
- [ ] Updater endpoint 配置 + pubkey 注入
