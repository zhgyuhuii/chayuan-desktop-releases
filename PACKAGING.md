# 察元 AI · 单机版打包教程

> 本文件覆盖 **单机版**(Tauri 桌面应用 + 嵌入式 Python 后端)的端到端打包流程。
> 多用户 / SaaS 部署形态请看 [`chayuan-server/packaging/README.md`](./chayuan-server/packaging/README.md)。
>
> 适用日期:**2026-05-07** 及之后(Phase 1–7 端到端就绪)。

---

## 0. 关键概念

单机版安装包由 **两段** 拼起来:

| 段 | 名字 | 内容 | 工具 | 平台产物 |
| :--- | :--- | :--- | :--- | :--- |
| ① 后端 | **chayuan-server** sidecar | Python 解释器 + 全部 wheels + 模型权重 + sqlite-vec 扩展 | **PyInstaller** | 单可执行(onedir) |
| ② 前端外壳 | **chayuan-client** 桌面应用 | React UI + Rust 主进程 + 嵌入 ① 作为 sidecar | **Tauri 2** | `.dmg` / `.msi` / `.deb` / `.AppImage` |

启动时:Tauri 主进程 spawn ① 子进程,通过 `CHAYUAN_ROOT=<用户首启动选定目录>` 注入数据路径,Tauri webview 与 ① 通过 `127.0.0.1:62581` HTTP 通信。

详细架构见 [`CLAUDE.md`](./CLAUDE.md) §3。

---

## 1. 前置环境

### 共通

| 工具 | 版本 | 安装命令 |
| :--- | :--- | :--- |
| Git | ≥ 2.30 | OS 自带 / 包管理器 |
| Python | **3.12.x** | `pyenv install 3.12.x` 或官网安装 |
| Poetry | ≥ 1.8 | `pip install "poetry>=1.8"` |
| PyInstaller | ≥ 6.0 | `pip install "pyinstaller>=6.0"`(不进 pyproject 主依赖) |
| Node.js | **22.x** | `nvm install 22` |
| pnpm | **9.x** | `corepack enable && corepack prepare pnpm@9 --activate` |
| Rust | stable | `rustup install stable` |

### 平台专属

#### macOS(Apple Silicon 或 Intel)

```bash
xcode-select --install                     # 命令行工具
# 公证 + 签名(可选,仅 release 需要):
# - APPLE_CERTIFICATE / APPLE_API_KEY 等 secret 由 CI 注入
```

#### Linux(Ubuntu 22.04+)

```bash
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  libssl-dev \
  patchelf \
  file
```

#### Windows 10/11(x86_64)

- **MSVC Build Tools(必装,Tauri / Rust 链接器依赖)**:
  ```powershell
  winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
  ```
  装完**关掉所有终端再开新的**让 PATH 生效。验证:`where.exe link` 应该输出 `link.exe` 路径。
  漏装会在 `cargo` 编译到一半时报 `linker 'link.exe' not found`。
- **WebView2 Runtime**(必装,Tauri build 会查注册表确认):
  ```powershell
  winget install Microsoft.EdgeWebView2Runtime
  ```
  漏装会在 `tauri build` 早期报 `RegQueryValueExW failed`。Win11 / 较新 Win10 通常自带,
  Build Tools 干净环境可能没。验证:
  ```powershell
  Get-ItemProperty "HKLM:\SOFTWARE\Wow6432Node\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue | Select pv
  ```
- (可选)EV 证书签名工具,仅 release 需要

---

## 2. 工作区布局

```
/work/chayuan-desktop/
├── README.md                      ← 本文件
├── CLAUDE.md                      ← 总架构 + 任务清单
├── chayuan-server/                ← 后端(Python / FastAPI)
│   ├── libs/chayuan-server/
│   │   └── chayuan/               ← 主包
│   ├── packaging/
│   │   ├── pyinstaller/           ← Phase 2 打包脚手架
│   │   │   ├── chayuan-server.spec
│   │   │   ├── build.py           ← 一键打包
│   │   │   └── README.md          ← PyInstaller 细节
│   │   └── vendor/sqlite-vec/     ← (可选)覆盖默认 sqlite-vec 扩展
│   └── pyproject.toml
└── chayuan-client/                ← 前端(Tauri 2 + React)
    ├── apps/desktop/
    │   └── src-tauri/
    │       ├── tauri.conf.json    ← bundle.externalBin = chayuan-server
    │       ├── binaries/          ← ② 嵌入 ① 的位置(由 build.py 自动拷入)
    │       └── src/{lib.rs,sidecar.rs,data_dir.rs}
    ├── packages/                  ← @chayuan/{api,app,platform-tauri,...}
    └── .github/workflows/
        ├── build-desktop.yml      ← Phase 6 三平台流水线
        └── README.md
```

---

## 3. 本地开发(不打包,日常开发循环)

```bash
# 终端 1:启动后端(开发模式,可选 --single-machine 模拟单机)
cd chayuan-server
poetry install
poetry run chayuan start -a --single-machine

# 终端 2:启动前端(Tauri dev,默认跳过 sidecar spawn,连本机 62581)
cd chayuan-client
pnpm install
pnpm dev:desktop
```

> **dev 模式默认不 spawn sidecar**(`cfg!(debug_assertions)` 跳过),所以你需要自己起后端。
> 如需在 dev 下也走 sidecar 流程,设 `CHAYUAN_DESKTOP_SPAWN_SIDECAR=1` 再 `pnpm dev:desktop`。

---

## 4. 本地打包(单平台一次过)

> **一键脚本**(macOS / Linux / Windows 都有):仓库根目录的 `build-desktop.{sh,cmd,ps1}`
> 把 §4 的 Step 1-3 串起来,默认**一次产两个版本**:
>
> - **轻量版 (lite)** — 不带模型,装机包体积小,首次启动靠 BootstrapBanner
>   引导用户在线下载或扫盘配置;产物落到 `dist-lite/`。
> - **集成版 (integrated)** — 把 `vendor/bundled_models/` 整树打进 Tauri
>   **`bundle.resources`**(与主 exe 同目录,**不嵌入 sidecar 内**),装好即用;
>   产物落到 `dist-integrated/`。
>
> 2026-05-15 起两个 flavor **共用同一份 sidecar exe**:模型从 PyInstaller
> datas 剥到 Tauri resources,解决集成版 sidecar ≥ 2 GB 撞 32-bit makensis
> mmap 上限。flavor 差异只剩 `build.py --sync-bundled-only` 那步。
>
> **Windows installer 两道独立限制(都是 2 GB,别混淆)**:
> 1. **单文件 < 2 GB** — NSIS makensis 32-bit mmap;WiX 3 light.exe LGHT0263
>    硬编码 INT32_MAX。`build.py --sync-bundled-only` 已加 size-guard 在 sync
>    阶段 abort,不会让你等到 makensis 才看见。
> 2. **NSIS 总 payload 压缩后 < 2 GB** — 仅 NSIS 受限,32-bit makensis 的
>    LZMA solid 累积 offset 32-bit 寻址溢出会报 *"Internal compiler error
>    #12345 error mmapping file ... out of range"*。集成版 vendor 嵌
>    emb+rerank+chat+asr 总 ~3.2 GB 就撞穿。WiX 3 MSI 总大小可到 4 GB+(CAB
>    分卷),不受此限。
>
> 解法:**Windows 集成版改走 WiX MSI**,轻量版仍 NSIS(vendor 空,远低于
> 2 GB)。脚本里 `Flavor='integrated' + Windows` 自动走 `pnpm
> build:desktop:integrated`(透传 `tauri build --bundles msi`)。代价:
> Windows 集成版用户拿到的是 `.msi`,装机后桌面图标是英文 "Chayuan"
> (`installer.nsh` 的"察元AI"中文 rename 只对 NSIS 生效;如需 MSI 同样
> 定制要用 WiX 自定义动作另写)。
>
> **集成版 MSI 走"外置 CAB"** (`apps/desktop/src-tauri/wix/main.wxs` 里
> `<MediaTemplate EmbedCab="no" ...>`):CAB 文件不嵌进 .msi 而是落在
> .msi 旁边作为独立 `app1.cab/app2.cab/...`。.msi 本体只剩 ~50 MB 元数据,
> Windows Installer 不再拷整个 3.5 GB 到 `C:\Windows\Installer\`,直接从
> 源目录逐 cab 读取——**绕开火绒 / 360 / 腾讯管家对系统缓存目录大文件写
> 的拦截**(用户报告 Error 1310 / 系统错误 110 / Error 2755 / MSI 退出码
> 1603 都是这道坎)。**分发必须把 dist-integrated/ 下的 .msi + 所有 .cab
> 打成 zip 一起给用户**,只给 .msi 不带 cab 装到一半会报 Error 1311/1335
> 找 cab 失败。
>
> 单文件 < 2 GB 由 `vendor/bundled_models/` "瘦身默认集"保证:chat 走 Qwen3-4B
> Q3_K_S (~1.85 GB) 或 Qwen2.5-3B Q4_K_M (~1.96 GB) 兜底,embedding 走
> `gte-multilingual-base` (~1.22 GB),reranker 走
> `gte-multilingual-reranker-base` (~584 MB)。> 2 GB 的高质量版本走
> install_job 在线下载。一键换装:
> `.\scripts\install-bundled-models.ps1 -Source modelscope -Clean` 或
> `python scripts/install-bundled-models.py --source modelscope --clean`。
>
> 构建开始时会自动调 `scripts/check-bundled-models.py` 体检,打印**本次嵌入
> 哪些模型**。模型相关的下载地址 / 格式 / 放置约定见
> [chayuan-server/vendor/bundled_models/README.md](chayuan-server/vendor/bundled_models/README.md)。
>
> ```bash
> # macOS / Linux
> ./build-desktop.sh                         # 双产物
> ./build-desktop.sh --lite-only             # 只打轻量版
> ./build-desktop.sh --integrated-only       # 只打集成版
> ./build-desktop.sh --skip-model-check      # 跳过模型体检
> ./build-desktop.sh --bundle-only           # 仅重打 Tauri bundle
> ```
>
> ```powershell
> # Windows
> .\build-desktop.cmd                        # 双产物
> .\build-desktop.cmd -LiteOnly              # 只打轻量版
> .\build-desktop.cmd -IntegratedOnly        # 只打集成版
> .\build-desktop.cmd -SkipModelCheck        # 跳过模型体检
> .\build-desktop.cmd -BundleOnly            # 仅重打 Tauri bundle
> .\build-desktop.cmd -Clean -VerboseSubprocess  # 干净重建 + 完整日志
> ```

### Step 1 — 服务端 PyInstaller

```bash
cd chayuan-server

# 1.1 装依赖(单机版只装 main,跳过 dev/test 节省 ~600 MB)
poetry install --only main
poetry run pip install "pyinstaller>=6.0"

# 1.2(可选)放 sqlite-vec 平台扩展到 vendor/(若 wheel 自带的不够新)
#     默认走 sqlite_vec wheel 内置的,**多数情况下跳过此步**
# packaging/vendor/sqlite-vec/{vec0.so | vec0.dylib | vec0.dll}

# 1.3 一键打包 + 自动按 rust target triple 拷到 chayuan-client/apps/desktop/src-tauri/binaries/
poetry run python packaging/pyinstaller/build.py

# 仅打包不拷贝(调试 spec 用):
poetry run python packaging/pyinstaller/build.py --no-copy

# 转发参数到 PyInstaller(诊断用):
poetry run python packaging/pyinstaller/build.py -- --clean --log-level=DEBUG
```

输出:
| 路径 | 内容 |
| :--- | :--- |
| `chayuan-server/dist/chayuan-server/chayuan-server[.exe]` | 入口可执行 |
| `chayuan-server/dist/chayuan-server/_internal/` | 解释器 + wheels + 资源(~ 1.5–2 GB v0;Phase 5.x 后目标 < 600 MB) |
| `chayuan-client/apps/desktop/src-tauri/binaries/chayuan-server-<triple>[.exe]` | Tauri externalBin 命名约定 |

triple 表(`build.py:rust_target_triple()` 自动判断):

| OS / arch | triple |
| :--- | :--- |
| macOS arm64 | `aarch64-apple-darwin` |
| macOS x86_64 | `x86_64-apple-darwin` |
| Windows x86_64 | `x86_64-pc-windows-msvc` |
| Linux x86_64 | `x86_64-unknown-linux-gnu` |
| Linux arm64 | `aarch64-unknown-linux-gnu` |

**Step 1 验证:**

```bash
# 单可执行启起来,/healthz 返 200
./chayuan-server/dist/chayuan-server/chayuan-server start -a --single-machine &
SERVER_PID=$!
sleep 30                                   # 等 alembic upgrade + uvicorn 就绪
curl -s http://127.0.0.1:62581/healthz | jq # {"status":"ok",...}
kill $SERVER_PID
```

### Step 2 — 客户端 Tauri bundle

```bash
cd chayuan-client

# 2.1 装 npm 依赖
pnpm install --frozen-lockfile

# 2.2 类型检查(预存 15 个 kb/collections 错误,不阻塞 release)
pnpm typecheck || true

# 2.3 打包桌面应用 ── Tauri 自动嵌入 binaries/chayuan-server-<triple> 作 sidecar
pnpm build:desktop
```

输出:`chayuan-client/apps/desktop/src-tauri/target/release/bundle/`

| 平台 | 产物 |
| :--- | :--- |
| macOS | `dmg/察元 AI_<version>_<arch>.dmg` + `macos/察元 AI.app.tar.gz` |
| Windows | `msi/察元 AI_<version>_x64_zh-CN.msi` + `nsis/察元 AI_<version>_x64-setup.exe` |
| Linux | `deb/*.deb` + `rpm/*.rpm` + `appimage/*.AppImage` |

### Step 3 — 安装 + 实机验收

#### macOS

```bash
open chayuan-client/apps/desktop/src-tauri/target/release/bundle/dmg/*.dmg
# 拖到 Applications,启动「察元 AI」
```

#### Windows

双击 `.msi` 或 `.exe` → 选安装路径 → 完成。

#### Linux

```bash
sudo dpkg -i chayuan-client/apps/desktop/src-tauri/target/release/bundle/deb/*.deb
chayuan-ai
# 或 chmod +x *.AppImage && ./*.AppImage
```

#### 首启动期望(用户视角)

1. 弹「**欢迎使用察元 AI**」窗口 → 选数据目录(默认平台标准目录:macOS `~/Library/Application Support/chayuan` / Windows `%APPDATA%\chayuan` / Linux `~/.local/share/chayuan`)
2. 「**正在启动后端服务...**」(10–30 秒,首次跑 alembic upgrade)
3. 进入主界面,提示「**尚未配置模型 → 去模型广场**」
4. 模型广场配任一可用厂商 → 全功能可用

---

## 5. 三平台 CI 自动打包

由 [`chayuan-client/.github/workflows/build-desktop.yml`](./chayuan-client/.github/workflows/build-desktop.yml) 驱动。

### 触发方式

| 触发 | 何时跑 | 用途 |
| :--- | :--- | :--- |
| `push` 到 `main` | 改动 `apps/desktop/**` / `packages/platform-tauri/**` 时 | 验证不破坏构建 |
| `tag release/*` | 发版本时 | 生成正式 artifact |
| `pull_request` | 同 push 路径过滤 | 门禁 |
| `workflow_dispatch` | 手动 | 任意时机 |

### 矩阵

4 个独立 job(`fail-fast: false`):

| label | runs-on | 产物 |
| :--- | :--- | :--- |
| `macos-arm64` | macos-14 | `.dmg` |
| `macos-x86_64` | macos-13 | `.dmg` |
| `windows-x86_64` | windows-latest | `.msi` / `.exe` |
| `linux-x86_64` | ubuntu-22.04 | `.deb` / `.rpm` / `.AppImage` |

### 下载产物

CI 跑完后到对应 run 的 **Artifacts** 区下:

```
chayuan-desktop-macos-arm64.zip
chayuan-desktop-macos-x86_64.zip
chayuan-desktop-windows-x86_64.zip
chayuan-desktop-linux-x86_64.zip
```

artifact 14 天保留。`release/*` tag 触发时建议加 `softprops/action-gh-release` step 自动上传到 GitHub Release(留 Phase 6.x 实装,见 workflow README)。

---

## 6. 体积与瘦身(单机版当前 vs 目标)

| 依赖 | v0 占比 | 来源 | 路线 |
| :--- | :--- | :--- | :--- |
| paddleocr + paddlepaddle | ~1.2 GB | 文档 OCR | Phase 5.x:改按需懒加载,默认走 RapidOCR |
| torch(paddle 间接) | ~600 MB | paddle / sentence-transformers | Phase 5.x:embedding 切 ONNX 后移除 |
| transformers / sentence-transformers | ~200 MB | reranker | 按需保留 |
| faiss-cpu | ~120 MB | 老向量索引 | Phase 4 已切 sqlite-vec;Phase 5.x 把 faiss 排除 |
| onnxruntime | ~70 MB | embedding | **保留** |
| sqlite-vec | < 1 MB | 嵌入式向量库 | **保留** |

**v0 单机包**:macOS arm64 ~ **1.8 GB**(.dmg)、Windows ~ **2.2 GB**(.msi)、Linux ~ **1.6 GB**(.deb)。
**v1 目标**(Phase 5.x 完成后):各平台 < **600 MB**。

---

## 7. 故障排查

### 7.1 PyInstaller 阶段

| 现象 | 处理 |
| :--- | :--- |
| `ModuleNotFoundError: chayuan.xxx` 在 frozen exe 启动时报 | 在 `chayuan-server.spec` 的 `hidden_modules` 显式追加该模块名 |
| `ImportError: dynamic module does not define module export function` | 多见于 paddle 的 `.so`;`pip install --force-reinstall paddlepaddle` 重装 |
| 打包后启动比预期慢 5x+ | 确认是 `--onedir` 不是 `--onefile`;onefile 每次启动都解压 |
| `sqlite-vec` 在 frozen exe 中 `no such function: vec_distance_l2` | spec 里 `collect_all('sqlite_vec')` 是否触发;否则手动放扩展到 `packaging/vendor/sqlite-vec/` |

### 7.2 Tauri bundle 阶段

| 现象 | 处理 |
| :--- | :--- |
| `error: external binary not found` | 确认 `apps/desktop/src-tauri/binaries/chayuan-server-<triple>(.exe)` 存在;triple 必须与目标平台对应 |
| Linux 装包后跑不起来:缺 libwebkit2gtk | 安装包用户需 `apt install libwebkit2gtk-4.1-0` 或选 AppImage(自带) |
| Windows Defender 误报 sidecar 是病毒 | EV 代码签名(Phase 6.x);未签名时用户需选「仍要运行」 |
| macOS「无法验证开发者」 | 公证(Phase 6.x);开发期可 `xattr -d com.apple.quarantine /Applications/察元\ AI.app` |

### 7.3 用户首启动阶段

| 现象 | 处理 |
| :--- | :--- |
| 数据目录写入失败 / 磁盘满 | FirstRunSetup 报错,引导改路径 |
| 「正在启动后端服务...」卡 60 秒后报错 | 看错误屏的 stdout/stderr 尾部 8 行 + 错误码;数据目录 logs/ 下也有完整日志 |
| sidecar 启动后崩溃,Tauri 不重启 | 当前 v0 不自动重启(留 Phase 5.y);用户点「重试启动」 |
| 装完后 `chayuan_data_dir_state` 报 | 数据目录权限不够,引导用户改到 home 下 |

---

## 8. 验证清单(release 前必跑)

```bash
# 后端测试
cd chayuan-server
PYTHONPATH=libs/chayuan-server pytest -q \
  libs/chayuan-server/tests/unit_tests/test_vector_store_local.py \
  libs/chayuan-server/tests/unit_tests/test_profile_single_machine.py \
  libs/chayuan-server/tests/unit_tests/test_sqlite_vec_kb_service.py
# 期望:55 passed

# 前端类型检查
cd ../chayuan-client
pnpm typecheck
# 期望:除预存 15 个 kb/collections + kb/folder-sync 历史错误外,0 错误
```

实机三平台:`Phase 1–7` 全部走通即满足 [CLAUDE.md §3.8](./CLAUDE.md) 的 v1 GA 验收清单大部分项;签名 / 公证 / Linux ARM 留 Phase 6.x。

---

## 9. 路线图

| 阶段 | 状态 | 内容 |
| :--- | :--- | :--- |
| Phase 1 | ✓ | Tauri 首启动数据目录向导 |
| Phase 2 | ✓ | PyInstaller spec + build.py |
| Phase 3 | ✓ | Tauri sidecar wiring(spawn / health / kill) |
| Phase 4 | ✓ | sqlite-vec LocalVectorStore(23 测试) |
| Phase 5 | ✓ | 单机 profile bootstrap(19 测试) |
| Phase 5.x | ✓ | SqliteVecKBService 适配器(13 测试) |
| Phase 6 | ✓ | 三平台 CI YAML |
| Phase 7 | ✓ | 单机 UX 收尾(隐藏登录 / 切换数据目录向导) |
| Phase 5.y | ⏳ | Redis → cachetools 缓存层 |
| Phase 5.z | ⏳ | Celery → asyncio.Queue 队列层 |
| Phase 6.x | ⏳ | macOS notarize + Windows EV 签名 + Linux ARM runners + GH Release 自动上传 |
| Phase 7.x | ⏳ | 数据目录复制 / 校验自动化(目前用户手动 cp) |

---

## 10. 相关文档

| 文档 | 用途 |
| :--- | :--- |
| [`CLAUDE.md`](./CLAUDE.md) | 单机版总架构 + 任务清单(给 AI 助手用) |
| [`chayuan-server/packaging/pyinstaller/README.md`](./chayuan-server/packaging/pyinstaller/README.md) | PyInstaller spec / build.py 细节 |
| [`chayuan-client/.github/workflows/README.md`](./chayuan-client/.github/workflows/README.md) | CI 工作流细节 |
| [`chayuan-server/packaging/README.md`](./chayuan-server/packaging/README.md) | **多用户 / 企业版** 打包(与本文不同的另一条路径) |
