#!/usr/bin/env bash
# 下载 llama-server pre-built binary 到 vendor/services/llama-server/<platform>/
#
# 注:版本号 / 资产名 schema / GitHub 镜像清单同时被「应用内运行时服务下载」
# 复用 —— 真源是 chayuan-server/.../chayuan/server/runtime/install_service.py
# (_LLAMA_VERSION / _llama_asset_for / _GH_MIRRORS)。改一边记得同步另一边。
# 例外:linux-x64 本脚本已改为 Docker(ubuntu:20.04)源码编译(官方 ubuntu-x64
# zip 是 24.04 CI 编的、需 GLIBC_2.38,老系统跑不起来)。install_service.py 的
# linux-x64 仍是下载,有同样隐患 —— 应用内若走那条路需另行处理。
#
# 用法:
#   bash scripts/install-llama-server.sh [version] [--target <plat>]
#
#   version: llama.cpp release tag,默认 b9174(与 install-llama-server.ps1 /
#            runtime/install_service.py 的 _LLAMA_VERSION 对齐)。
#            老 b4404 不支持 qwen3 等 2025 后架构,会撞 unknown model architecture。
#   --target: vendor 平台子目录名 + 对应 upstream asset。不传 = 自动按 host 选。
#     支持:
#       linux-x64          (Docker ubuntu:20.04 内源码编译,需 docker;老 glibc 通跑)
#       linux-arm64        (Docker fallback,不走本脚本;装方法见 README)
#       macos-arm64        (Apple Silicon,默认 macOS arm64 host)
#       macos-x64          (Intel Mac,默认 macOS x86_64 host)
#       win-x64            (AVX2 默认)
#       win-x64-avx        (AVX 老 Sandy/Ivy Bridge)
#       win-x64-avx512     (Skylake-X / Xeon)
#       win-x64-noavx      (Pentium/VM)
#       win-arm64          (Surface Pro X / Copilot+)
#
# 例:
#   bash scripts/install-llama-server.sh                      # 自动 host
#   bash scripts/install-llama-server.sh b9174 --target win-x64
set -euo pipefail

VERSION="b9174"
TARGET=""
# 解析 args:第一个非 -- 开头的非空 arg 当 VERSION,--target <val> 取 TARGET
while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --target=*) TARGET="${1#*=}"; shift ;;
        -h|--help)
            grep -E '^#' "$0" | sed 's/^# \?//'
            exit 0 ;;
        --) shift; break ;;
        -*) echo "[install] 未知参数 $1" >&2; exit 64 ;;
        *) VERSION="$1"; shift ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$HERE")"

# 自动 host 检测(TARGET 没传时用)
detect_host_target() {
    OS="$(uname -s)"
    M="$(uname -m)"
    case "$OS" in
        Linux)
            case "$M" in
                aarch64|arm64) echo "linux-arm64" ;;
                *)             echo "linux-x64" ;;
            esac ;;
        Darwin)
            case "$M" in
                arm64|aarch64) echo "macos-arm64" ;;
                *)             echo "macos-x64" ;;
            esac ;;
        *) echo "" ;;
    esac
}

if [ -z "$TARGET" ]; then
    TARGET="$(detect_host_target)"
    if [ -z "$TARGET" ]; then
        echo "[install] 不支持的 host OS $(uname -s),请显式传 --target" >&2
        exit 1
    fi
fi

# ── linux-x64:不下官方 zip,改 Docker(ubuntu:20.04)源码编译 ──────────────
# 官方 llama-bXXXX-bin-ubuntu-x64.zip 在 Ubuntu 24.04 CI 编译,需 GLIBC_2.38 /
# GLIBCXX_3.4.32 → 在 Ubuntu 22.04 / 统信 / 麒麟(glibc<=2.35)上起不来。
# 在 ubuntu:20.04 容器内源码编译 llama.cpp(master),产物 glibc<=2.29,老系统
# 通跑;GGML_NATIVE=OFF 让 CPU 也通吃(并保留运行时 CPU 派发)。
if [ "$TARGET" = "linux-x64" ]; then
    DEST="$WORKSPACE/chayuan-server/vendor/services/llama-server/linux-x64"
    mkdir -p "$DEST"
    if ! command -v docker >/dev/null 2>&1; then
        echo "[install-llama-server] linux-x64 需用 Docker(ubuntu:20.04)源码编译,但本机无 docker。" >&2
        echo "  先装:sudo apt-get install -y docker.io  (装完可能要 newgrp docker / 重新登录)" >&2
        echo "  或在一台 Ubuntu 20.04 机器上源码 cmake 编译后,把 llama-server + *.so 放进:" >&2
        echo "  $DEST" >&2
        exit 3
    fi
    OUT="/tmp/llama-src-build-$$"
    rm -rf "$OUT"; mkdir -p "$OUT"
    echo "[install-llama-server] linux-x64:在 ubuntu:20.04 容器内源码编译 llama.cpp(约 10-20 分钟)..."
    docker run --rm -v "$OUT:/out" ubuntu:20.04 bash -c '
        set -ex
        A="-o Acquire::Check-Valid-Until=false -o Acquire::AllowInsecureRepositories=true"
        apt-get $A update || apt-get $A update
        DEBIAN_FRONTEND=noninteractive apt-get $A install -y --allow-unauthenticated \
            build-essential cmake git ca-certificates
        git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp
        cd /tmp/llama.cpp
        git rev-parse --short HEAD > /out/COMMIT
        cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_NATIVE=OFF
        cmake --build build -j"$(nproc)" --target llama-server
        cp build/bin/llama-server /out/
        find build/bin -maxdepth 1 -name "*.so*" -exec cp {} /out/ \;
    '
    # 旧 binary / so 清掉,放新产物(LICENSE 等保留)
    find "$DEST" -maxdepth 1 -type f \
        \( -name 'llama-server' -o -name '*.so' -o -name '*.so.*' \) -delete 2>/dev/null || true
    cp -f "$OUT"/llama-server "$OUT"/*.so* "$DEST/"
    chmod +x "$DEST/llama-server"
    printf 'source-build: llama.cpp %s (ubuntu:20.04 容器,glibc<=2.29)\n%s\n' \
        "$(cat "$OUT/COMMIT" 2>/dev/null || echo unknown)" "$(date +%Y-%m-%d)" > "$DEST/VERSION"
    rm -rf "$OUT"
    echo
    echo "[install-llama-server] linux-x64 完成 → $DEST"
    ls -lh "$DEST"
    if command -v objdump >/dev/null 2>&1; then
        _hi="$(objdump -T "$DEST"/*.so 2>/dev/null | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1 || true)"
        echo "[install-llama-server] glibc 验收:.so 最高需 ${_hi:-?}(应远低于 2.38)"
    fi
    exit 0
fi

# target → upstream asset name
case "$TARGET" in
    # ⚠ b9174+ 起 llama.cpp 把 mac/Linux 的 release asset 从 .zip 改成 .tar.gz
    #   (只剩 Windows 仍是 .zip)。早期 b5xxx-b8xxx 用 .zip,但默认 b9174 已走
    #   新约定 —— 这里 mac/linux-arm64 一律 .tar.gz,Win 保持 .zip。
    # 同时 linux-x64(ubuntu-x64)b9174+ upstream 已不发 —— 走上面 Docker 源码
    #   编译分支,这里这条仍保留是历史兼容(旧版本 b5xxx 还能下到)。
    linux-x64)        ASSET="llama-$VERSION-bin-ubuntu-x64.zip" ;;
    linux-arm64)      ASSET="llama-$VERSION-bin-ubuntu-arm64.tar.gz" ;;
    macos-arm64)      ASSET="llama-$VERSION-bin-macos-arm64.tar.gz" ;;
    macos-x64)        ASSET="llama-$VERSION-bin-macos-x64.tar.gz" ;;
    win-x64|win-x64-avx|win-x64-avx512|win-x64-noavx)
        # b5400 起 llama.cpp 不再分 avx/avx2/avx512/noavx 多个 zip,合并成单一
        # win-cpu-x64.zip(运行时 SIMD dispatch)。默认 b9174 ≥ 5400 走新名;
        # 与 runtime/install_service.py 的 _llama_asset_for 保持一致。
        ASSET="llama-$VERSION-bin-win-cpu-x64.zip" ;;
    win-arm64)        ASSET="llama-$VERSION-bin-win-cpu-arm64.zip" ;;
    *) echo "[install] 未识别的 target: $TARGET" >&2; exit 1 ;;
esac

DEST="$WORKSPACE/chayuan-server/vendor/services/llama-server/$TARGET"
mkdir -p "$DEST"

# GitHub 镜像支持:墙内 github.com 不通时,设环境变量
#   export GITHUB_MIRROR="https://gh-proxy.com/"
# 下载 URL 会被拼成 https://gh-proxy.com/https://github.com/...(这类代理
# 就是把完整 github URL 接在自己后面)。不设则直连 github.com。
# 注:老的 ghproxy.com 已废弃,用 gh-proxy.com 或 ghfast.top。
RAW_URL="https://github.com/ggerganov/llama.cpp/releases/download/$VERSION/$ASSET"
if [ -n "${GITHUB_MIRROR:-}" ]; then
    URL="${GITHUB_MIRROR%/}/${RAW_URL}"
    echo "[install-llama-server] 经 GitHub 镜像:${GITHUB_MIRROR%/}/"
else
    URL="$RAW_URL"
fi
TMPZIP="/tmp/$ASSET"

echo "[install-llama-server] target=$TARGET asset=$ASSET dest=$DEST"
echo "[install-llama-server] 下载 $URL"
curl -L -o "$TMPZIP" "$URL"
echo "[install-llama-server] 下完 $(du -h "$TMPZIP" | cut -f1)"

TMP_EXTRACT="/tmp/llama-$VERSION-$TARGET-extract"
rm -rf "$TMP_EXTRACT"
mkdir -p "$TMP_EXTRACT"
# 解压:按 asset 后缀分派
#   *.tar.gz / *.tgz —— mac / Linux b9174+ 走这条(tar -xzf,mac/Linux 自带);
#   *.zip            —— Win + 老版本 mac/Linux,unzip 首选,bsdtar / python3
#                       zipfile 兜底(AL8 / Alpine minimal 默认不带 unzip)。
case "$ASSET" in
    *.tar.gz|*.tgz)
        tar -xzf "$TMPZIP" -C "$TMP_EXTRACT" ;;
    *.zip)
        if command -v unzip >/dev/null 2>&1; then
            unzip -q "$TMPZIP" -d "$TMP_EXTRACT"
        elif command -v bsdtar >/dev/null 2>&1; then
            bsdtar -xf "$TMPZIP" -C "$TMP_EXTRACT"
        elif command -v python3 >/dev/null 2>&1; then
            python3 -m zipfile -e "$TMPZIP" "$TMP_EXTRACT"
        else
            echo "[install-llama-server] 没找到 unzip / bsdtar / python3,无法解压 $TMPZIP" >&2
            exit 2
        fi ;;
    *)
        echo "[install-llama-server] 未知归档格式:$ASSET" >&2
        exit 2 ;;
esac

# 清旧 binary / dll / so / dylib(保留 LICENSE 之类不动)
# ⚠ 不能加 `-type f` —— 上一轮 cp -f 给 mac 留下的可能是符号链接(llama.cpp
# 官方 tar 把 libllama.dylib 软链到 libllama.<ver>.dylib),`-type f` 不匹配
# 软链 → 不清掉 → 下面 cp 同名时撞既有软链,2>/dev/null 把错吞了 → 真文件
# 没落盘。按名删,文件 / 软链一起清。
find "$DEST" -maxdepth 1 \
    \( -name '*.exe' -o -name '*.dll' -o -name '*.so' -o -name '*.dylib' \
       -o -name 'llama-server' -o -name 'ggml-metal.metal' \) \
    -delete 2>/dev/null || true

# Win 包结构:zip 顶层就是 *.exe + *.dll。Mac/Linux 包:b9174 起 tar 顶层
# llama-b9174/(没有嵌套的 build/bin/)直接放 binary + 多版本 .dylib。
#
# 关键:mac tar 里每个 .dylib 是三层软链结构(全部 same-dir 相对软链),例如:
#   libllama-common.dylib    → libllama-common.0.dylib       (软链 0 字节)
#   libllama-common.0.dylib  → libllama-common.0.0.9174.dylib (软链 0 字节)
#   libllama-common.0.0.9174.dylib                            (真文件 ~8 MB)
# binary 用 `@rpath/libllama-common.0.dylib`(SONAME),所以软链那一份必须搬。
#
# ⚠ 不能加 `-type f` —— 那只匹配真文件,软链(-type l)漏过 → DEST 里只有
# 带 0.0.9174 后缀的真文件,SONAME 软链缺失 → dyld 找不到。
# ⚠ 也不能用 cp -fL(上轮我误改的方向):那会把每个软链都 dereference 成
# 真文件,DEST 里同内容存 3 份,~3x 空间浪费。
# 正解:`cp -af`(BSD:-pPR)保留软链。tar 里软链目标都是 same-dir basename,
# 搬到 DEST 后软链在 DEST 内部解到 sibling 真文件,结构与上游一致。
find "$TMP_EXTRACT" \
    \( -name 'llama-server' -o -name 'llama-server.exe' \
       -o -name '*.dll' -o -name '*.so' -o -name '*.dylib' \
       -o -name 'ggml-metal.metal' \) \
    -exec cp -af {} "$DEST/" \;

[ -f "$DEST/llama-server" ] && chmod +x "$DEST/llama-server"

# macOS:给 binary + 所有 .dylib 显式加 @loader_path 进 rpath。
# llama.cpp 官方 mac release 的 binary 用 `@rpath/libllama-common.0.dylib`
# 这种引用,但 binary 自己的 rpath 不含同目录 → 转包后兄弟 .dylib 就找不到。
# 加 @loader_path 让每个 lib 都能在自己所在目录找兄弟。install_name_tool
# -add_rpath 在 rpath 已存在时返非零,这里吞掉(idempotent)。
if [ "$(uname -s)" = "Darwin" ] && command -v install_name_tool >/dev/null 2>&1; then
    for f in "$DEST/llama-server" "$DEST"/*.dylib; do
        [ -f "$f" ] || continue
        chmod u+w "$f" 2>/dev/null || true
        install_name_tool -add_rpath "@loader_path" "$f" 2>/dev/null || true
    done
fi

# 写元数据
echo -e "$VERSION\n$(date +%Y-%m-%d)" > "$DEST/VERSION"
# 如果 LICENSE 不存在,从 zip 里拷一份
if [ ! -f "$DEST/LICENSE" ]; then
    LIC=$(find "$TMP_EXTRACT" -type f -name 'LICENSE' | head -1 || true)
    [ -n "$LIC" ] && cp "$LIC" "$DEST/LICENSE"
fi

rm "$TMPZIP"
rm -rf "$TMP_EXTRACT"

echo
echo "[install-llama-server] 完成 → $DEST:"
ls -lh "$DEST"
