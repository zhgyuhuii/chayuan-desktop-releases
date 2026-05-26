# vendor/services/whisper-server

`whisper.cpp` 官方 release 的 `whisper-server` 二进制 + 依赖 DLL。集成版打包时由
`build.py sync_services()` 拷到 Tauri resources;装机后落在
`<install_dir>/services/whisper-server/`。

## 布局(平台预编译)

```
whisper-server/
├── win-x64/        ← upstream whisper-bin-x64.zip 解压(~2 MB,v1.7.6+)
│   ├── whisper-server.exe
│   ├── whisper.dll / ggml-base.dll / ggml-cpu.dll / ggml.dll
│   └── VERSION
├── linux-x64/      ← Docker `ghcr.io/ggml-org/whisper.cpp:main` (amd64) ~4 MB
│   ├── whisper-server
│   ├── libwhisper.so* / libggml*.so*
│   └── VERSION
├── linux-arm64/    ← (占位,upstream Docker 没发 arm64)
├── macos-arm64/    ← (占位)
└── macos-x64/      ← (占位)
```

**Upstream 现状**:whisper.cpp 官方只发 Windows pre-built(`whisper-bin-Win32.zip` /
`whisper-bin-x64.zip`,从 v1.7.6 起),**没有** Linux / macOS pre-built。所以
Mac / Linux 用户需要:

- **macOS**:`brew install whisper-cpp`(主仓 formula),然后跑
  `scripts/install-whisper-server.sh` 把 brew bin 拷到 `vendor/services/whisper-server/`。
- **Linux**:装 cmake + g++ + git,跑 `scripts/install-whisper-server.sh` 现场源码 build。

运行时 `local_runtime.py:find_server_exe()` 按 OS 自动挑子目录;扁平 layout
(install 脚本现拉现写的旧落点)作兜底。

## 当前版本

`v1.7.6`(2025-04 release;Win64 ~2 MB)。

## 升级

```bash
# Win
.\scripts\install-whisper-server.ps1 -Version v1.8.x

# macOS (brew 自动跟最新 formula)
brew upgrade whisper-cpp
./scripts/install-whisper-server.sh

# Linux (源码 build,指定 git tag)
./scripts/install-whisper-server.sh v1.8.0
```
