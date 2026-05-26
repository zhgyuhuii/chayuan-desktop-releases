# Chayuan Windows 打包（M5）

本目录包含 Windows amd64 个人版离线安装包的全部脚本。**所有文件都是骨架代码，需要在 Windows 10/11 x64 构建机上实际测试跑一遍**（macOS 上没法跑 makensis / .vbs / .ps1，只能保证语法无误、逻辑同构）。

## 目录结构

```
packaging/windows/
├── README.md            # 本文件
├── launcher.vbs         # 静默启动壳（双击 / 快捷方式入口）
├── launcher.ps1         # 主启动逻辑（被 .vbs 隐藏窗口调起）
├── Chayuan.nsi          # NSIS 安装器脚本
├── build_win.ps1        # 一键打包入口
└── dist/
    ├── first_run.ps1              # 首次运行离线安装器
    └── requirements-runtime.txt   # pip 离线安装清单（含 pystray/pywin32）
```

## 构建步骤（在 Windows 构建机上执行）

### 1. 预下载 vendor 资源（一次性）

```powershell
# python-build-standalone（Windows amd64 self-contained Python 3.11，~20 MB）
$ver = '20260414'
$pkg = 'cpython-3.11.15+20260414-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
Invoke-WebRequest `
    -Uri "https://github.com/astral-sh/python-build-standalone/releases/download/$ver/$pkg" `
    -OutFile packaging\vendor\cpython-3.11-x86_64-pc-windows-msvc.tar.gz

# 预下载运行时 wheels（在真 Windows 上跑最稳，~400-500 MB）
python -m pip download `
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple `
    --dest packaging\vendor\wheels `
    --prefer-binary `
    -r packaging\windows\dist\requirements-runtime.txt
```

### 2. 安装 NSIS（一次性）

下载并安装 [NSIS 3.x](https://nsis.sourceforge.io/)，确保 `makensis.exe` 在 `PATH` 上。

### 3. 打包

```powershell
cd <repo_root>
powershell -ExecutionPolicy Bypass -File packaging\windows\build_win.ps1
```

产物：`packaging\build\Chayuan-1.0.0.0-windows-amd64-personal-dist.exe`

### 4. 测试首次运行

在干净机器（或 VM）上：

1. 双击安装器，同意 UAC，默认装到 `C:\Program Files\Chayuan\`；
2. 从"开始菜单 → Chayuan"或桌面快捷方式启动；
3. 首次启动会自动跑 `first_run.ps1`，进度日志在 `%USERPROFILE%\.chayuan\logs\first_run.log`；
4. 安装完成后任务栏右下角出现 Chayuan 托盘图标；右键菜单同 macOS。

## 用户目录布局

Windows 版和 macOS 完全对齐，都用 `~/.chayuan/`：

```
%USERPROFILE%\.chayuan\
├── python\                 # python-build-standalone 解包目录
│   ├── python.exe
│   ├── pythonw.exe         # launcher 优先用此（无 CMD 黑窗）
│   └── Lib\...
├── logs\
│   ├── launcher.log
│   ├── first_run.log
│   └── tray.log
├── data\                   # CHAYUAN_ROOT
└── .installed              # 安装完成标记
```

"彻底重装"：`Remove-Item -Recurse -Force $env:USERPROFILE\.chayuan` 再启动。

## 已知限制 / 待办

- **未签名**。安装器会被 SmartScreen 拦截，用户需要"更多信息 → 仍要运行"。生产发布在 M8 解决（EV 代码签名证书）。
- **未支持 arm64 Windows**（Surface Pro X 等）。需要单独跑 pbs 的 `aarch64-pc-windows-msvc` tarball。
- **pystray 在某些 Windows 7 机器**可能因缺 pywin32 特定 DLL 报错。MVP 目标是 Windows 10/11；如需 Win7 支持后续补。
- **tar.exe 依赖**。launcher.ps1 用 `tar.exe` 解压；Windows 10 1803 之前没有内置，需要改用 .zip 或 7z（未来优化）。
