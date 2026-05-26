# 察元 AI 助手桌面安装包（packaging/）

本目录用于生成桌面安装包。当前 **macOS Apple Silicon 个人版的 dev / dist 都已跑通**，其它平台（Windows / Linux、Intel、企业版）按路线图分阶段补齐。

## 目录结构

```
packaging/
├── README.md              # 本文件
├── .gitignore             # 忽略 build/ 和 vendor/
├── mac/                   # macOS 打包资源
│   ├── build_mac.sh       # 一键构建（dev / dist 两种模式）
│   ├── launcher.sh        # .app/Contents/MacOS/Chayuan 启动器
│   ├── Info.plist         # 应用清单（含 LSUIElement=1 菜单栏模式）
│   ├── make_icns.sh       # PNG → .icns
│   └── dist/              # dist 模式专属资源
│       ├── first_run.sh         # 首次运行离线安装器
│       └── requirements-runtime.txt  # pip 离线安装清单
├── vendor/                # 预下载的第三方 artifacts，被 .gitignore 屏蔽
│   ├── cpython-3.11-aarch64-apple-darwin.tar.gz  # 19 MB
│   └── wheels/                                   # ~370 MB，220+ 个 whl
└── build/                 # 构建产物目录，被 .gitignore 屏蔽
    ├── dev/   # dev 构建物
    └── dist/  # dist 构建物
```

## 构建模式

`build_mac.sh` 支持两种模式：

### dev（默认，构建机本地可运行）

用于快速验证托盘 UX、CLI 启动链路。**仅在构建机当前开发机可运行**（硬编码 anaconda 环境路径）。

- **产物**：`build/dev/Chayuan.app` + `Chayuan-1.0.0.0-macos-arm64-personal-dev.dmg`
- **体积**：~8 MB（只打业务源码 + 图标 + 启动脚本）
- **构建耗时**：~10 秒
- **限制**：不可分发给其他用户

```bash
bash packaging/mac/build_mac.sh dev
```

### dist（完整离线分发）

真正对外分发的离线安装包。

- **机制**：bundle Astral 的 `python-build-standalone`（自包含 Python 3.11，19 MB tarball）+ 预下载 220+ 个运行时 whl 到 `Resources/dist/`；首次启动时 `first_run.sh` 解压 Python 到 `~/.chayuan/python/`，再用该 Python 离线 pip 安装所有依赖
- **产物**：`build/dist/Chayuan-1.0.0.0-macos-arm64-personal-dist.dmg`
- **体积**：~390 MB（.dmg 压缩后）/ .app 内部约 440 MB
- **构建耗时**：~15 秒（vendor/ 已就绪时）
- **首次运行耗时**：~20 秒（解压 + pip 离线安装 + jieba 等 sdist 本地编译）

**为什么不用 Miniforge？** Miniforge 的 conda 在新机器上 `conda create python=3.11` 需要联网解析 conda-forge 索引，慢且违背"完全离线"的承诺；conda 还拒绝安装到含空格的路径（`~/Library/Application Support/` 里的空格）。改用 python-build-standalone 后：解压即用、完全离线、路径无限制。

```bash
# 1) 预下载 vendor 资源（一次性，主机上执行）
curl -fL -o packaging/vendor/cpython-3.11-aarch64-apple-darwin.tar.gz \
  "$(curl -s https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
      | grep browser_download_url \
      | grep '3.11' | grep 'aarch64-apple' | grep 'install_only_stripped' \
      | head -1 | cut -d'"' -f4)"

/opt/anaconda3/envs/chat/bin/pip download \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --dest packaging/vendor/wheels \
  --prefer-binary \
  -r packaging/mac/dist/requirements-runtime.txt

# 2) 打包
bash packaging/mac/build_mac.sh dist

# 3) 验证（模拟新机器）
rm -rf ~/.chayuan
open packaging/build/dist/Chayuan.app
tail -f ~/.chayuan/logs/first_run.log
```

### 用户目录布局（dist 模式）

```
~/.chayuan/
├── python/              # python-build-standalone 解包目录
│   ├── bin/python3      # Python 3.11.x + pip + 所有业务依赖
│   └── lib/...
├── logs/
│   ├── launcher.log     # 启动脚本日志
│   ├── first_run.log    # 首次安装日志
│   └── tray.log         # 托盘应用日志
├── data/                # CHAYUAN_ROOT：业务数据 / 知识库 / SQLite
└── .installed           # 安装完成标记（含安装时间、Python 路径等）
```

"彻底重装"方式：`rm -rf ~/.chayuan` 然后重新双击 .app。

## 企业版（enterprise edition）

`build_mac.sh` 支持 `--edition=personal|enterprise` 开关。企业版会自动把
配置切换到 Postgres + Milvus + Redis + JWT 鉴权（prod profile），DMG
文件名、Bundle ID 和 Dock 显示名也会分叉。

```bash
bash packaging/mac/build_mac.sh dist --edition=enterprise
```

详见 [`enterprise.md`](./enterprise.md)。

## 下一步路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M3 ✅ | macOS arm64 个人版 **dev** DMG | 完成 |
| M3.5 ✅ | macOS arm64 个人版 **dist** DMG（pbs + 离线 whl） | 完成 |
| M5 ✅ | Windows + Linux 打包骨架（脚本就位，需各自机器跑通） | 完成 |
| M7 ✅ | 企业版配置分叉（Postgres + Milvus + Redis + 鉴权） | 完成 |
| M7.5 ✅ | 企业版内嵌 Postgres 17.9 + Redis 8.6.2 + Milvus-Lite，开箱即用（macOS arm64） | 完成 |
| M4 | macOS Intel（x86_64）构建 | 待排期 |
| M7.6 | Windows / Linux 的 enterprise embedded（把 services tarball 换成对应平台版本） | 待排期 |
| M8 | 代码签名 + 公证 + 自动更新 | 待排期 |
| M9 | CI matrix（GitHub Actions / 私有 Runner） | 待排期 |

## 已知限制 / 待办

- **MVP 不嵌入 Milvus / Redis / Postgres**。个人版默认 FAISS + SQLite，无需这些中间件；企业版在 M7 接入。
- **未签名 / 未公证**。macOS 双击第一次会被 Gatekeeper 拦截，需要「右键 → 打开 → 确认」。生产发布在 M8 解决。
- **仅 arm64**。x86_64 Mac 需要另外下载 `x86_64-apple-darwin` 的 pbs tarball 和 whl，在 M4 支持。
- **托盘图标非"模板图"**。当前用彩色 44×44 PNG；macOS 规范建议监控栏使用黑白 + alpha 的 template image 自动适配深浅色模式。M5 前可以优化。
