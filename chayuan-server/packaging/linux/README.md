# Chayuan Linux 打包（M5 / M6）

本目录提供 Linux x86_64 个人版 **AppImage** 安装包脚本。AppImage 是类
Linux 里最接近 macOS `.dmg` 的"单文件免安装"格式：用户下载一个
`.AppImage` 文件，`chmod +x` 后双击即可启动，不需要 root、不污染系统。

**现状**：骨架脚本已提交，需要在 Linux 构建机（推荐 Ubuntu 20.04 / 22.04
x86_64）上实际跑通并测试。

## 目录结构

```
packaging/linux/
├── README.md                    # 本文件
├── launcher.sh                  # AppRun（入口）
├── chayuan.desktop              # .desktop 桌面项
├── build_linux.sh               # 一键打包脚本（调 appimagetool）
└── dist/
    ├── first_run.sh             # 首次运行离线安装器
    └── requirements-runtime.txt # pip 离线安装清单（含 pystray / Pillow）
```

## 构建步骤（在 Linux 构建机上执行）

### 1. 预下载 vendor 资源

```bash
# python-build-standalone（Linux glibc x86_64，~30 MB）
curl -fL -o packaging/vendor/cpython-3.11-x86_64-unknown-linux-gnu.tar.gz \
    "$(curl -s https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
        | grep browser_download_url | grep '3.11' \
        | grep 'x86_64-unknown-linux-gnu' | grep 'install_only_stripped' \
        | head -1 | cut -d'"' -f4)"

# 预下载 wheels（必须在 Linux 跑以匹配 manylinux 标签）
python3 -m pip download \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --dest packaging/vendor/wheels \
    --prefer-binary \
    -r packaging/linux/dist/requirements-runtime.txt
```

### 2. 安装 appimagetool

```bash
sudo curl -fL -o /usr/local/bin/appimagetool \
    https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
sudo chmod +x /usr/local/bin/appimagetool
```

### 3. 打包

```bash
cd <repo_root>
bash packaging/linux/build_linux.sh
```

产物：`packaging/build/linux/Chayuan-1.0.0.0-x86_64.AppImage`

### 4. 用户使用

```bash
chmod +x Chayuan-1.0.0.0-x86_64.AppImage
./Chayuan-1.0.0.0-x86_64.AppImage
```

或者在文件管理器里双击。首次启动会弹出 notify-send 通知（"正在解压 Python
运行时…"→"正在从离线 wheels 安装业务依赖…"→"Chayuan 安装完成"），托盘图
标出现在任务栏。

## 托盘兼容性说明

Linux 的系统托盘（system tray / notification area / status icon）由桌面
环境管理，实现方式有两类：

1. **StatusNotifierItem / AppIndicator**（现代标准）：KDE、Unity、Cinnamon、
   MATE、XFCE 4.14+ 都原生支持；**GNOME 3.26+ 需要用户装扩展**
   `gnome-shell-extension-appindicator`（许多发行版默认集成）。
2. **XEmbed tray**（旧标准）：LXDE、XFCE 旧版、i3 + stalonetray 使用。

pystray 会自动选择：优先 `AppIndicator3`（GObject），失败时退到 `GtkStatusIcon`
/ XEmbed。如果用户的 GNOME 没装扩展，托盘图标不会出现；launcher.sh 里写入
日志，排障时可以看到 `DE=GNOME` 信息，方便提示用户装扩展。

## 用户目录布局

和 mac / win 对齐：

```
~/.chayuan/
├── python/        # python-build-standalone 解包
├── logs/
├── data/          # CHAYUAN_ROOT
└── .installed
```

彻底重装：`rm -rf ~/.chayuan`。

## 已知限制 / 待办

- **仅 x86_64 glibc**。musl（Alpine）和 aarch64（树莓派、Ampere）需要单独跑对应 pbs tarball（pip 下载也要换 `--platform manylinux2014_aarch64`）。
- **GNOME 用户托盘图标可能不显示**。需要用户自行启用 AppIndicator 扩展；可以在 `launcher.sh` 里检测并 `zenity` 提示（未实现）。
- **AppImage 本身不带自动更新**。需要集成 AppImageUpdate，M8 里做。
- **未签名**。用户双击时可能提示"未知来源"，需要用户信任；生产环境建议同时发布 `.deb` / `.rpm` 并签名。
