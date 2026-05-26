# vendor/services/llama-server

`llama.cpp` 官方 release 的 `llama-server` 二进制 + 依赖 DLL。集成版打包时由
`build.py sync_services()` 拷到 Tauri resources;装机后落在
`<install_dir>/services/llama-server/`。

## 命名约定

```
<os>-<arch>[-<cpu-variant>]/
```

- `os` ∈ {`win`, `macos`, `linux`}
- `arch` ∈ {`x64`, `arm64`}(x86_64 / aarch64 的短名)
- `cpu-variant` 仅 Win x64 上区分;非 Win 平台只有一个 SIMD 默认

## 平台矩阵(入 git,clone 即用)

| subdir              | 来源(b4404)                              | 默认在 | 用途                                         | 大小  |
|---------------------|-------------------------------------------|--------|---------------------------------------------|-------|
| `linux-x64/`        | `llama-bXXXX-bin-ubuntu-x64.zip`          | Linux  | Ubuntu 22.04+ glibc 2.34, x86_64            | ~5 MB |
| `linux-arm64/`      | Docker `ghcr.io/ggml-org/llama.cpp:server` (arm64) | Linux | Linux aarch64(Apple M VM / Raspberry Pi / AWS Graviton 等);动态链接,带 .so 共享库 | ~26 MB |
| `macos-arm64/`      | `llama-bXXXX-bin-macos-arm64.zip`         | macOS  | Apple Silicon(M1+),带 Metal shader        | ~6 MB |
| `macos-x64/`        | `llama-bXXXX-bin-macos-x64.zip`           | macOS  | Intel Mac                                   | ~5 MB |
| `win-x64/`          | `llama-bXXXX-bin-win-avx2-x64.zip`        | Win    | AVX2 默认(Haswell 2013+,绝大多数 PC)     | ~5 MB |
| `win-x64-avx/`      | `llama-bXXXX-bin-win-avx-x64.zip`         |        | AVX 但无 AVX2(Sandy/Ivy Bridge 2011-2013) | ~5 MB |
| `win-x64-avx512/`   | `llama-bXXXX-bin-win-avx512-x64.zip`      |        | AVX-512(Skylake-X / Ice Lake Xeon)        | ~5 MB |
| `win-x64-noavx/`    | `llama-bXXXX-bin-win-noavx-x64.zip`       |        | 无 AVX(Pentium/Celeron / VM)             | ~5 MB |
| `win-arm64/`        | `llama-bXXXX-bin-win-llvm-arm64.zip`      | Win    | Windows on ARM(Surface Pro X / Copilot+) | ~5 MB |

合计入 git ≈ 40 MB。每个子目录都附带 `LICENSE`(llama.cpp MIT)+ `VERSION` 元数据。

## 运行时自动选

`local_runtime.py:find_server_exe()` 按 `sys.platform` + `platform.machine()` 算
当前 OS 的 candidate 列表,然后第一个真存在 binary 的子目录胜出:

| 主机平台        | 默认 candidate 列表(高优先级在前)        |
|-----------------|-----------------------------------------|
| Win x86_64      | `[win-x64, win-x64-noavx]`               |
| Win ARM64       | `[win-arm64]`                            |
| macOS Apple Silicon | `[macos-arm64]`                      |
| macOS Intel     | `[macos-x64]`                            |
| Linux x86_64    | `[linux-x64]`                            |
| Linux aarch64   | `[linux-arm64]`                          |

**强制覆盖**:`CHAYUAN_VENDOR_PLATFORM=<subdir>` 环境变量。常见场景:

- Win 老 CPU 没 AVX2:`set CHAYUAN_VENDOR_PLATFORM=win-x64-noavx`
- Win 新 Xeon 强制 AVX-512:`set CHAYUAN_VENDOR_PLATFORM=win-x64-avx512`
- 用 install 脚本现拉的扁平 binary:`set CHAYUAN_VENDOR_PLATFORM=` (设空)

## GLIBC 要求(Linux)

Linux 二进制由 Ubuntu 22.04 编出,要 `GLIBC >= 2.34` / `GLIBCXX >= 3.4.32`。
RHEL 8 / Alibaba Cloud Linux 3 / Anolis / 老 Debian 跑会报 `version not found`。
解法:WSL2 / 容器 / 源码 `cmake -B build && cmake --build build --target llama-server`。

## 当前版本

`b4404`(2025-01,llama.cpp release tag)。

## 升级

```bash
# Linux / Mac dev
./scripts/install-llama-server.sh b<N>
# Win
.\scripts\install-llama-server.ps1 -Version b<N>
```

脚本现写到扁平 layout。要 commit 新版本到平台子目录:手动 mv + `git add -f`。
