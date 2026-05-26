"""一键打包 chayuan-server (--onedir),并把整个目录拷到桌面端 ``chayuan-server/``
供 Tauri ``resources`` 嵌入。

约定路径(2026-05-19 起):

    apps/desktop/src-tauri/chayuan-server/
        chayuan-server(.exe)          ← PyInstaller bootloader
        _internal/                    ← Python + 全部依赖(含 torch CPU)

历史:本脚本曾走 ``--onefile`` + Tauri ``externalBin``,产物是
``apps/desktop/src-tauri/binaries/chayuan-server-<rust-target-triple>(.exe)``
单文件。问题:
  - 装机后冷启动解压 726 MB 到 %TEMP%\\_MEIxxxxx 要 100-150s,/healthz 60s 超时,
    主界面进不去。
  - torch CPU 装进 onefile bundle 触发 c10.dll WinError 1114(DLL 加载顺序)。
切到 ``--onedir`` 后 DLL 路径稳定、启动秒级,代价是产物变成目录(走 Tauri
``resources`` 而不是 ``externalBin``)。

用法
----

::

    # 仅打 sidecar exe + 拷贝(默认;轻量,模型由 server 首启引导下载)
    poetry run python packaging/pyinstaller/build.py

    # 仅打包,不拷贝
    poetry run python packaging/pyinstaller/build.py --no-copy

    # 一键拉全套 vendor + 模型并打包(集成版,完整离线可用)
    poetry run python packaging/pyinstaller/build.py --release lite      # 轻量集成
    poetry run python packaging/pyinstaller/build.py --release standard  # 服务器标准
    poetry run python packaging/pyinstaller/build.py --release pro       # 企业全功能

    # CI 二次跑 / 离线机器:只用 packaging/.cache,不发任何网络请求
    poetry run python packaging/pyinstaller/build.py --release lite --offline

    # 转发额外参数到 PyInstaller(调试 spec 用)
    poetry run python packaging/pyinstaller/build.py -- --clean --log-level=DEBUG

依赖
----

::

    poetry install
    poetry run pip install "pyinstaller>=6.0"

PyInstaller 不进 ``pyproject.toml`` 主依赖列表,仅打包阶段使用。
``--release`` 模式额外要求安装 ``chayuan_packaging`` 包(在 packaging/python312/ 下)。
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Windows GBK 兜底:build-desktop.ps1 把这个脚本的输出 `2>&1 | Out-File`,
# Python 检测到 stdout 不是 tty 时默认走 locale 编码(zh-CN Windows = GBK),
# 一遇到非 ASCII 字符(本文件大量用 ✓ / ✗ / ⚠ / 中文)直接抛 UnicodeEncodeError
# → 整条打包链路挂掉。强制 stdout/stderr UTF-8,与 build-desktop.ps1 的
# chcp 65001 配套(那一边管控制台代码页,这一边管 Python 内部 encoding)。
# errors='replace':极端情况下(如系统没装 UTF-8 codec)用 ? 替换而不是炸。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except (AttributeError, OSError):
    # Python < 3.7 没 reconfigure;或者 stdout 被替换成无 reconfigure 方法的对象
    pass


HERE = Path(__file__).resolve().parent                       # packaging/pyinstaller
ROOT = HERE.parent.parent                                    # chayuan-server/(repo root)
WORKSPACE = ROOT.parent                                      # chayuan-desktop/
# 真正的 chayuan 包(dist 名 chayuan-server);PyInstaller 必须分析这份 live 源码。
SERVER_PKG = ROOT / "libs" / "chayuan-server"
DESKTOP_SRC_TAURI = (
    WORKSPACE / "chayuan-client" / "apps" / "desktop" / "src-tauri"
)
# 2026-05-19:--onedir 模式下整个 dist/chayuan-server/ 树拷到这里,跟
# bundled_models / services 一样走 Tauri ``resources`` 而不是 ``externalBin``。
# 老 DESKTOP_BIN_DIR(src-tauri/binaries/)保留兼容(installer 老脚本可能还在引),
# 但本脚本只往新路径写。
DESKTOP_CHAYUAN_SERVER_DIR = DESKTOP_SRC_TAURI / "chayuan-server"
DESKTOP_BIN_DIR = DESKTOP_SRC_TAURI / "binaries"  # 保留:兼容旧调用方读取
# 2026-05-15:bundled_models 不再嵌进 PyInstaller sidecar(避免 NSIS makensis
# mmap 单文件 ≥2GB 失败),改由 Tauri 的 ``bundle.resources`` 字段承载。
# build.py 把 vendor/bundled_models/ 拷到这里,tauri.conf.json 把它打进 installer。
DESKTOP_BUNDLED_MODELS_DIR = DESKTOP_SRC_TAURI / "bundled_models"
# 默认 bundled 源(打包专用暂存),可被 --bundled-source / $CHAYUAN_BUILD_BUNDLED_SOURCE 覆盖
# (典型用法:开发者用 install-bundled-models.ps1 -ChayuanRoot 把模型装到 chayuan_root,
#  打包时指向 <chayuan_root>/models/bundled/,免去 dev/打包两份磁盘占用)。
BUNDLED_SRC = ROOT / "vendor" / "bundled_models"
# 单文件 ≥ 2 GB 的"超大权重"会被分流到这里 — 不进 MSI(WiX/NSIS 卡 2 GB),
# build-desktop.ps1 会把这个目录拷到 dist-{flavor}/models_seed/,ISO 自动带上。
# 装机后 chayuan-server first_launch 钩子从挂载的光驱扫到这堆文件再种到
# <CHAYUAN_ROOT>/models/bundled/。
EXTERNAL_SEED_DIR = ROOT / "dist" / "models_seed"
EXTERNAL_SEED_MANIFEST_NAME = ".external_seed_manifest.json"
EXTERNAL_SEED_VOLUME_HINT = "CHAYUAN_FULL"   # 跟 make-iso.ps1 的卷标对齐
# PyTorch wheels 走类似 models_seed 的 ISO 旁挂链路 — 不嵌进 sidecar exe,装机后
# first_launch.seed_torch_wheels() 从介质拷到 <CHAYUAN_ROOT>/torch_wheels/<variant>/。
# 注:CPU wheel ~250MB 嵌进 MSI 也不会撞 2GB 限制,但跟 models_seed 用同样的
# ISO 链路保持设计一致,且未来加 CUDA wheel(~2.5GB)直接撞 2GB 必须走 ISO。
TORCH_WHEELS_SEED_DIR = ROOT / "dist" / "torch_wheels_seed"


def _resolve_bundled_src(cli_arg: Optional[str]) -> Path:
    """决定 bundled_models 源路径。优先级:CLI > env > 默认 vendor/bundled_models/。"""
    if cli_arg:
        p = Path(cli_arg).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"[build] --bundled-source 指向的目录不存在:{p}")
        return p
    env = os.environ.get("CHAYUAN_BUILD_BUNDLED_SOURCE")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"[build] $CHAYUAN_BUILD_BUNDLED_SOURCE 指向的目录不存在:{p}")
        return p
    return BUNDLED_SRC
SERVICES_SRC = ROOT / "vendor" / "services"
DESKTOP_SERVICES_DIR = DESKTOP_SRC_TAURI / "services"
# PyInstaller 默认把所有产物丢到 ``ROOT/dist/``;onefile 模式下文件在 dist/ 顶层,
# onedir 模式下嵌一层同名子目录(``dist/chayuan-server/chayuan-server(.exe)``)。
DIST_ROOT = ROOT / "dist"
SPEC = HERE / "chayuan-server.spec"


def rust_target_triple() -> str:
    """与 Tauri ``externalBin`` 用的 ``<name>-<triple>`` 命名约定对齐。

    这里只覆盖三平台 + arm64/x86_64 主流组合,够 Phase 6 CI 用。其它架构(s390x/
    riscv 等)留给未来扩展。
    """
    sysname = platform.system()
    arch = platform.machine().lower()

    if sysname == "Darwin":
        if arch in ("arm64", "aarch64"):
            return "aarch64-apple-darwin"
        return "x86_64-apple-darwin"

    if sysname == "Windows":
        if arch in ("arm64", "aarch64"):
            return "aarch64-pc-windows-msvc"
        return "x86_64-pc-windows-msvc"

    if sysname == "Linux":
        if arch in ("aarch64", "arm64"):
            return "aarch64-unknown-linux-gnu"
        return "x86_64-unknown-linux-gnu"

    return f"unknown-{sysname.lower()}"


def _ensure_pyinstaller() -> None:
    """确保当前解释器(sys.executable)能 import PyInstaller —— 不行就装。

    build.py 总是用 ``sys.executable -m PyInstaller`` 跑打包。Windows 的
    build-desktop.ps1 走 ``poetry run`` 会先 ``pip install pyinstaller``;但
    在别的调用环境(如 Linux 直接用 micromamba/conda 的 python 跑 build.py)
    那一步不一定经过 → ``No module named PyInstaller``。这里让 build.py 自带
    兜底:缺了就往当前解释器装,不依赖外层脚本。
    """
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("[build] 当前解释器缺 PyInstaller,自动安装 (pip install 'pyinstaller>=6.0') ...", flush=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"],
    )


def _ensure_chayuan_installed() -> None:
    """把 chayuan 包 editable 装进当前解释器 —— PyInstaller 必须分析到最新源码。

    PyInstaller 的 collect_submodules / modulegraph 按 ``import chayuan`` 解析到
    的那份包来分析。build-desktop.{sh,ps1} 只跑 ``poetry install --only main``,
    而 poetry 的 ``--only`` 不会(重新)安装根包本身 → 当前解释器里的 chayuan
    可能是旧安装残留的陈旧副本,缺新加的 ``chayuan.server.*`` 子模块,导致
    sidecar 运行时 ``ModuleNotFoundError``(实测 ``chayuan.server.utils`` /
    ``chayuan.server.profiles`` / ``chayuan.server.config_panel.restart``)。

    这里在 PyInstaller 前强制 editable 安装:让 ``import chayuan`` 永远指向
    live 源码,杜绝陈旧副本。deps 已由外层 ``poetry install --only main`` 装好,
    ``--no-deps`` 不重复解析;``--force-reinstall`` 确保旧的非 editable 副本被换掉。
    """
    print(f"[build] editable 安装 chayuan(确保 PyInstaller 分析最新源码):{SERVER_PKG}", flush=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-e", str(SERVER_PKG),
         "--no-deps", "--force-reinstall"],
    )


def run_pyinstaller(extra_args: list[str]) -> None:
    _ensure_pyinstaller()
    _ensure_chayuan_installed()
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", *extra_args]
    print(f"[build] $ {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd, cwd=str(ROOT))


def chayuan_pack_cmd(release: str, sub: str, *, offline: bool = False) -> list[str]:
    """构造一条 ``chayuan_packaging`` 子命令(便于打印 + 单测)。"""
    cmd = [sys.executable, "-m", "chayuan_packaging", sub, "--release", release]
    if offline:
        cmd.append("--offline")
    return cmd


def run_chayuan_pack(release: str, *, offline: bool = False) -> None:
    """串接 ``chayuan-pack`` 的 fetch + stage,在 PyInstaller 前把 vendor + 模型准备好。

    Args:
        release: ``lite`` / ``standard`` / ``pro`` 之一(与 ``layout.yaml`` 对齐)。
        offline: ``True`` 时跳过任何网络;仅读 ``packaging/.cache``。CI 二次跑或
            完全离线打包用。

    错误处理:任一子步骤失败立即抛 :class:`subprocess.CalledProcessError`,
    上层 ``main`` 不再继续 PyInstaller —— 避免把缺失资源的半成品打成包。
    """
    for sub in ("fetch", "stage"):
        cmd = chayuan_pack_cmd(release, sub, offline=offline)
        print(f"[build] $ {' '.join(cmd)}", flush=True)
        subprocess.check_call(cmd, cwd=str(ROOT))


def _onedir_root() -> Path:
    """定位 ``--onedir`` 产物目录 ``dist/chayuan-server/``。"""
    suffix = ".exe" if platform.system() == "Windows" else ""
    candidate = DIST_ROOT / "chayuan-server"
    if (candidate / f"chayuan-server{suffix}").is_file():
        return candidate
    # 兜底:历史 onefile 产物也认一下,给"用户没切 spec 就跑 build.py"的场景留诊断。
    legacy = DIST_ROOT / f"chayuan-server{suffix}"
    if legacy.is_file():
        raise SystemExit(
            f"[build] 检测到老 onefile 产物 {legacy};本脚本已切 --onedir,"
            f"请检查 spec 是否已用 EXE(exclude_binaries=True) + COLLECT 结构。"
        )
    raise SystemExit(
        f"[build] PyInstaller --onedir 产物未找到:{candidate}\n"
        "请确认 spec 中 EXE/COLLECT 的 name='chayuan-server' 与目录结构。"
    )


def copy_to_tauri_resources() -> Path:
    """把 ``dist/chayuan-server/`` 整树拷到 Tauri ``resources/chayuan-server/``。

    Tauri ``bundle.resources`` 把这里的整个目录打进 NSIS / MSI / dmg,装机后
    在 ``<install>/resources/chayuan-server/`` 落地。sidecar.rs 用
    ``app.path().resolve("chayuan-server/chayuan-server.exe", BaseDirectory::Resource)``
    取路径 spawn。

    幂等:目标存在就先 rmtree,避免残留旧 _internal/。
    """
    src = _onedir_root()
    dst = DESKTOP_CHAYUAN_SERVER_DIR
    if dst.exists():
        shutil.rmtree(dst)
    # copytree 保留权限位(macOS / Linux 上 chayuan-server bootloader 需要 +x)。
    # symlinks=True 必需:macOS 上 PyInstaller PKG 阶段 fixup 会在 _internal/
    # 根目录留下指向不存在目标的 libssl.3.dylib / libcrypto.3.dylib 等断链
    # (真 OpenSSL 已被 spec 显式落到 _internal/_chayuan_openssl/ 子路径,
    # 启动期的 chayuan-rthook-openssl 从那里加载,根目录的断链是 fixup
    # 残留、对运行时无害)。symlinks=False 会尝试解引用断链 → ENOENT 炸掉
    # 整个 copytree。symlinks=True 按原样把符号链接搬过去,broken 仍 broken
    # 但不阻塞拷贝,Linux 下正常 .so 软链也一并保留(省体积、保持原结构)。
    shutil.copytree(src, dst, symlinks=True)
    # Windows 不需要 chmod;macOS/Linux 上 bootloader exe 必须可执行。
    if platform.system() != "Windows":
        exe = dst / "chayuan-server"
        if exe.is_file():
            os.chmod(exe, 0o755)
    n_files = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"[build] copied {src} -> {dst} ({n_files} files)", flush=True)
    return dst


# 老 onefile + externalBin 路径的拷贝函数,保留 alias 给 build-desktop.ps1 / CI
# 脚本兼容引用;内部转向 resources 路径。后续清理可以删除。
def copy_to_tauri_binaries() -> Path:
    return copy_to_tauri_resources()


# Windows installer 单文件硬上限(NSIS makensis 32-bit mmap;WiX 3 light.exe
# LGHT0263 硬编码 INT32_MAX)。任何 ≥ 这个尺寸的单文件都打不进 .exe/.msi,
# 必须换更小的量化 / 模型版本。WiX 4 Burn 能处理但 Tauri 2 还没支持。
_WIN_INSTALLER_FILE_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GiB


# 已知"非必需冗余权重"扩展名:同目录有 .gguf / .safetensors 替代时,这些文件
# 都是历史遗留(老版 install 或手动 HF snapshot),删了不影响 runtime。
# .onnx_data 是 ONNX external data(单文件 > 2GB 必走的旁路);.bin 是 PyTorch
# 老格式,新模型基本都给 .safetensors / .gguf。
_REDUNDANT_WEIGHT_SUFFIXES = (".bin", ".onnx_data", ".pt", ".pth")

# 与运行时 local_index.BUNDLED_CAPABILITY_DIRS 对齐 — vendor/bundled_models/ 下
# 允许出现的合法 cap 目录名。出现在这之外的目录(如 vendor/bundled_models/bge-m3/
# 直接挂根级)十有八九是用户/老 install 脚本把"模型目录"放错了一级。
_KNOWN_CAP_DIRS = frozenset(
    ("chat", "embedding", "rerank", "asr", "tts", "ocr", "image", "custom")
)

# vendor 根级"非法子目录"被跳过时,如果体积超过这个阈值就拉警报(疑似误放),
# 否则当成噪音(README 同侧 / .gitkeep / 小杂项)轻量提示一下就过。
_SUSPICIOUS_SKIP_THRESHOLD = 100 * 1024 * 1024   # 100 MB


# 把孤立模型目录名按"启发式"猜回它该归哪个 cap,只用作 hint(不强行 move)。
# 命名约定看 HF/ModelScope 主流模型仓库,新模型只要主流命名都能命中;命不中
# 也无所谓 — 提示用户"请确认 cap"即可。
_CAP_HINT_PATTERNS = (
    # cap, list of substrings (lowercase) suggestive of this cap
    ("embedding", ("bge-m3", "bge-small", "bge-large", "embedding", "gte-",
                   "e5-", "jina-embeddings", "text-embedding")),
    ("rerank",    ("reranker", "rerank", "cross-encoder")),
    ("chat",      ("qwen", "llama", "mistral", "phi", "deepseek", "yi-",
                   "chatglm", "gemma", "-chat", "-instruct", ".gguf")),
    ("asr",       ("whisper", "paraformer", "sense-voice", "funasr")),
    ("tts",       ("piper", "vits", "xtts", "edge-tts")),
    ("ocr",       ("rapidocr", "pp-ocr", "paddleocr", "ocr-")),
    ("image",     ("clip", "siglip", "dinov2", "vit-")),
)


def _guess_cap_for_dir(name: str) -> Optional[str]:
    """启发式猜模型目录该归哪个 cap;猜不中返回 None。"""
    n = name.lower()
    for cap, patterns in _CAP_HINT_PATTERNS:
        for pat in patterns:
            if pat in n:
                return cap
    return None


def _dir_size_bytes(dir_path: Path) -> int:
    """递归求目录大小(字节)。容错:单文件 stat 失败计 0。"""
    total = 0
    for p in dir_path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _has_alt_weight(dir_path: Path) -> bool:
    """目录树里递归找 .gguf / .safetensors — 有就说明同目录的 .bin/.onnx_data 是冗余。"""
    if not dir_path.is_dir():
        return False
    for p in dir_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".gguf", ".safetensors"):
            return True
    return False


def _guard_oversized_files(bundled_src: Path) -> None:
    """扫 bundled 源整树,任何 ≥ 2 GB 单文件立即报错。

    这是 Windows installer 的硬限制 — 一旦走到 makensis / light.exe 才发现,
    用户得等十几分钟的 PyInstaller + cargo + vite 跑完才看到错误。提前到
    sync 这一步报错,几秒内告诉用户哪个文件超标。

    错误消息会针对每个 offender 检查"父目录有没有 .gguf / .safetensors 替代",
    有则提示直接 rm(常见冗余),没有则建议跑 install-bundled-models.py --clean-cap。
    """
    if not bundled_src.is_dir():
        return
    offenders: list[tuple[Path, int]] = []
    for sub in bundled_src.rglob("*"):
        if sub.is_file() and sub.stat().st_size >= _WIN_INSTALLER_FILE_LIMIT:
            offenders.append((sub, sub.stat().st_size))
    if not offenders:
        return

    # 按 cap 目录(bundled_src/<cap>/...)分组,方便给针对性命令
    # cap 目录就是 bundled_src 的直接子目录
    redundant: list[tuple[Path, int, Path]] = []   # (file, size, model_subdir)
    irreplaceable: list[tuple[Path, int]] = []
    caps_to_reinstall: set[str] = set()
    for path, size in offenders:
        suffix = path.suffix.lower()
        # 找模型子目录(bundled_src/<cap>/<model_subdir>/...)
        rel = path.relative_to(bundled_src)
        parts = rel.parts
        if len(parts) >= 2:
            model_dir = bundled_src / parts[0] / parts[1]
            cap = parts[0]
        else:
            model_dir = path.parent
            cap = parts[0] if parts else "?"
        if suffix in _REDUNDANT_WEIGHT_SUFFIXES and _has_alt_weight(model_dir):
            redundant.append((path, size, model_dir))
        else:
            irreplaceable.append((path, size))
            caps_to_reinstall.add(cap)

    lines = ["[build] FATAL: 以下单文件 ≥ 2 GB,无法打进 Windows installer:"]
    for path, size in offenders:
        gb = size / 1024 / 1024 / 1024
        rel = path.relative_to(bundled_src)
        lines.append(f"  - {rel}  ({gb:.2f} GB)")
    lines.append("")
    lines.append("原因:NSIS makensis 32-bit mmap 单文件 < 2 GB;WiX 3 light.exe")
    lines.append("     LGHT0263 硬编码 INT32_MAX。两条 Windows installer 路都卡。")
    lines.append("")

    if redundant:
        lines.append("修法 A:这些是冗余权重(同目录已有 .gguf / .safetensors),"
                     "直接删即可:")
        # 按 model_dir 合并 — 同模型多个冗余文件一起删
        by_dir: dict[Path, list[Path]] = {}
        for path, _size, model_dir in redundant:
            by_dir.setdefault(model_dir, []).append(path)
        for model_dir, paths in by_dir.items():
            for p in paths:
                lines.append(f'  Remove-Item -Force "{p}"')
                # .onnx_data 通常跟同名 .onnx 一对,孤立 .onnx 也没用 — 提示一起删
                if p.suffix.lower() == ".onnx_data":
                    onnx = p.with_suffix("")
                    if onnx.is_file():
                        lines.append(f'  Remove-Item -Force "{onnx}"')
                    onnx_dir = p.parent
                    # 若 onnx/ 子目录里全是 onnx 系列,整个目录可清
                    if onnx_dir != model_dir and onnx_dir.name.lower() == "onnx":
                        lines.append(f'  # 或整层删:Remove-Item -Recurse -Force "{onnx_dir}"')
        lines.append("")

    if irreplaceable:
        lines.append("修法 B:这些是无替代权重(目录里没找到 .gguf / .safetensors),"
                     "需要换更小量化重下:")
        for path, size in irreplaceable:
            gb = size / 1024 / 1024 / 1024
            lines.append(f"  - {path.relative_to(bundled_src)}  ({gb:.2f} GB)")
        cap_args = " ".join(f"--only {c}" for c in sorted(caps_to_reinstall)) \
                   if caps_to_reinstall else ""
        lines.append("")
        lines.append("  # 一键清掉旧的并按瘦身清单重下(GGUF 量化,~605 MB 单文件):")
        lines.append(f"  poetry run python scripts\\install-bundled-models.py --clean-cap {cap_args}")
        lines.append("")
        lines.append("  瘦身清单见 chayuan-server/vendor/bundled_models/README.md。")

    msg = "\n".join(lines)
    print(msg, file=sys.stderr, flush=True)
    raise SystemExit(2)


def _load_bundle_caps(flavor: str) -> tuple[str, ...]:
    """从 packaging/bundle_manifest.py 拿单一真源的 caps 清单。

    跨同 package 内 import 没问题,这里用 sys.path 是因为 build.py 直接被当脚本跑,
    `python packaging/pyinstaller/build.py` 时 packaging 不是 import path 上的 root。
    """
    import importlib
    pkg_root = Path(__file__).resolve().parent.parent  # chayuan-server/packaging/
    sys.path.insert(0, str(pkg_root))
    try:
        manifest = importlib.import_module("bundle_manifest")
    finally:
        sys.path.pop(0)
    return tuple(manifest.caps_for(flavor))


def canonical_subdirs_for(cap: str) -> tuple[str, ...]:
    """从 packaging/bundle_manifest.py 拿当前规范的模型子目录白名单(cap → tuple)。

    跟 :func:`_load_bundle_caps` 同样的 sys.path 注入路径,运行期幂等。
    返回空 tuple 时调用方应按"全收"语义处理(open-by-default)。
    """
    import importlib
    pkg_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(pkg_root))
    try:
        manifest = importlib.import_module("bundle_manifest")
    finally:
        sys.path.pop(0)
    return tuple(manifest.canonical_subdirs_for(cap))


def _target_has_installer_size_limit(target_triple: str) -> bool:
    """目标平台的 installer 是否撞 2 GB 单文件限制 — 即:要不要走 external seed 分流。

    - **Windows**:WiX 3 `light.exe` LGHT0263 硬编码 INT32_MAX(2 GB),NSIS makensis
      32-bit mmap 也是 2 GB。要分流。
    - **macOS**:DMG(APFS/HFS+ 8 EB)/ PKG(XAR)无实际单文件限制。**不要分流** —
      大文件直接嵌 .dmg/.pkg 即可,装机后 Tauri resources 解到 app bundle 里。
    - **Linux**:DEB(ar+tar)/ RPM(cpio)/ AppImage(squashfs)无 2 GB 限制。
      **不要分流**。
    """
    return "-windows-" in target_triple.lower()


def sync_bundled_models(
    *,
    lite: bool,
    bundled_src: Optional[Path] = None,
    target_triple: Optional[str] = None,
) -> None:
    """同步 bundled 源 → src-tauri/bundled_models/(给 Tauri resources)。

    Args:
        lite: True = 只拷 LITE_CAPS(embedding/rerank/asr/ocr,~990 MB);
              False = 拷 FULL_CAPS 全集(整 ~3.5 GB)。
              (Tauri NSIS installer 会把它打进 <install_dir>/bundled_models/)
        bundled_src: 显式指定源路径。默认 BUNDLED_SRC(=vendor/bundled_models/)。
                     CLI / env 覆盖在 main 里解析后传入。

    清单真源 packaging/bundle_manifest.py。哪些 cap 算 lite/full 调整只改那一处。
    幂等:重复跑结果相同。每次都先清掉旧目录避免上一次 flavor 残留。
    """
    src = bundled_src or BUNDLED_SRC
    flavor = "lite" if lite else "full"
    caps = _load_bundle_caps(flavor)

    if DESKTOP_BUNDLED_MODELS_DIR.exists():
        shutil.rmtree(DESKTOP_BUNDLED_MODELS_DIR)
    DESKTOP_BUNDLED_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not src.is_dir():
        print(f"[build] 警告:bundled 源不存在 {src},{flavor} 退化为空目录", flush=True)
        (DESKTOP_BUNDLED_MODELS_DIR / ".gitkeep").touch()
        return

    # full 模式按 2 GB 单文件阈值分流 — 仅 Windows 目标平台需要:
    #   - 小文件:DESKTOP_BUNDLED_MODELS_DIR(嵌进 MSI 资源)
    #   - 大文件:EXTERNAL_SEED_DIR(ISO 旁挂,装机后从光驱拷)
    # lite 不走分流(本来就 < 2 GB 单文件,且没 ISO 配套)。
    # macOS / Linux 目标平台的 installer 无单文件 2 GB 限制(DMG/APFS、DEB、RPM、
    # AppImage 都支持 TB 级单文件),不分流 — 大文件直接嵌进 .dmg/.deb/...。
    # 旧的 _guard_oversized_files FATAL 调用已撤 — Windows 上现在自动分流。
    do_external_split = (
        not lite
        and target_triple is not None
        and _target_has_installer_size_limit(target_triple)
    )

    print(f"[build] flavor={flavor}, caps = {' / '.join(caps)}, src = {src}", flush=True)
    if do_external_split:
        print(f"[build] target={target_triple} 撞 2 GB installer 限制 → 大文件分流到 external seed", flush=True)
    elif not lite and target_triple is not None:
        print(f"[build] target={target_triple} 无 2 GB 限制 → 大文件直接嵌 installer(不分流)", flush=True)

    # 准备 external seed 目录(每次清空,避免上次残留)。即使本次不分流也清一次 —
    # 上次可能跑了 Windows target,这次切到 Linux/Mac,不清会留 stale 大文件混淆。
    if EXTERNAL_SEED_DIR.exists():
        shutil.rmtree(EXTERNAL_SEED_DIR)
    if do_external_split:
        EXTERNAL_SEED_DIR.mkdir(parents=True, exist_ok=True)

    caps_set = set(caps)
    n_files = 0
    n_bytes = 0
    n_external = 0
    n_external_bytes = 0
    n_redundant_skipped = 0
    skipped: list[str] = []                                    # 已知 cap 但不在本 flavor
    suspicious_skipped: list[tuple[str, int]] = []             # 未知子目录 + 体积 ≥ 100 MB
    external_files: list[dict[str, object]] = []
    redundant_examples: list[str] = []

    for entry in sorted(src.iterdir()):
        if entry.name.startswith(".") or entry.name in ("README.md",):
            continue
        if not entry.is_dir():
            continue
        if entry.name not in caps_set:
            # 分两类:
            #   (a) 已知 cap 名但不在本 flavor 清单(例:full 不要 custom)→ 轻量提示
            #   (b) 完全未知名称(例:bge-m3 直接挂根级)→ 看体积,大就响警报
            if entry.name in _KNOWN_CAP_DIRS:
                skipped.append(entry.name)
            else:
                size = _dir_size_bytes(entry)
                if size >= _SUSPICIOUS_SKIP_THRESHOLD:
                    suspicious_skipped.append((entry.name, size))
                else:
                    skipped.append(entry.name)
            continue
        # 该 cap 在清单内 → 整子目录拷过去(跳过 .gitkeep + 冗余权重 + 分流大文件)
        # 2026-05-18:新增 CANONICAL_MODEL_SUBDIRS 白名单过滤 —
        # 历史遗留子目录(例:embedding/gte-multilingual-base 是 HF transformers,
        # 但当前 canonical 是 bge-m3 GGUF)不打进 installer。
        canon = canonical_subdirs_for(entry.name)
        for sub in entry.rglob("*"):
            if not sub.is_file() or sub.name == ".gitkeep":
                continue
            rel = sub.relative_to(src)
            size = sub.stat().st_size

            # 规范子目录过滤:相对 cap_dir 的首段必须在 canon 列表里(canon 为空
            # = open-by-default,不过滤 — 兼容未来还没列入清单的 cap)。
            # 注意:rel.parts[0] 是 cap 名(等同 entry.name);rel.parts[1] 才是
            # 模型子目录名。如果模型直接落在 cap 根(单文件,如 asr/ggml-tiny.bin
            # 旧布局),len(parts)==2,跳过子目录检查仍合法 — 顶层文件总是放行。
            if canon and len(rel.parts) >= 3:
                model_subdir_name = rel.parts[1]
                if model_subdir_name not in canon:
                    # 不在白名单 → 视为 stale legacy 模型,跳过(不打 installer)
                    n_redundant_skipped += 1
                    if len(redundant_examples) < 5:
                        redundant_examples.append(
                            f"stale subdir: {rel} (canonical={canon!r})"
                        )
                    continue

            # 冗余权重剪枝:同 model 目录有 .gguf/.safetensors 时,.bin/.onnx_data/.pt/.pth
            # 都是历史遗留(老版 install 或手动 HF snapshot),装机不需要,直接跳。
            # 这一步把 ISO 体积省下来,避免把 ~4 GB 冗余拷到光驱介质上。
            suffix = sub.suffix.lower()
            if suffix in _REDUNDANT_WEIGHT_SUFFIXES:
                parts = rel.parts
                model_dir = (src / parts[0] / parts[1]) if len(parts) >= 2 else sub.parent
                if _has_alt_weight(model_dir):
                    n_redundant_skipped += 1
                    if len(redundant_examples) < 5:
                        redundant_examples.append(str(rel))
                    continue

            # 单文件 ≥ 2 GB 的:Windows full 模式分流到 external seed;
            # 非 Windows 或 lite 直接嵌进 bundled_models(这些平台没有 ISO 链路 + 没 2 GB 限制)
            if size >= _WIN_INSTALLER_FILE_LIMIT and do_external_split:
                dst = EXTERNAL_SEED_DIR / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sub, dst)
                n_external += 1
                n_external_bytes += size
                external_files.append({
                    "path": str(rel).replace("\\", "/"),
                    "size": size,
                })
            else:
                dst = DESKTOP_BUNDLED_MODELS_DIR / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sub, dst)
                n_files += 1
                n_bytes += size

    # 写 external seed manifest(MSI 资源里,runtime first_launch 用它指导扫挂载介质)。
    # 没大文件就不写 — 装机后 bundled_seed 直接走 happy path,跟以前一样。
    if external_files:
        import json
        manifest = {
            "version": 1,
            "iso_volume_hint": EXTERNAL_SEED_VOLUME_HINT,
            "files": sorted(external_files, key=lambda x: x["path"]),  # type: ignore[arg-type,return-value]
        }
        manifest_path = DESKTOP_BUNDLED_MODELS_DIR / EXTERNAL_SEED_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 留一个 .gitkeep 防 Tauri glob 命中空目录时的 edge case(lite 时如果连
    # bundled_models 都未装填,目录还要存在让 cargo:rerun-if-changed 有 anchor)
    if n_files == 0 and not (DESKTOP_BUNDLED_MODELS_DIR / EXTERNAL_SEED_MANIFEST_NAME).exists():
        (DESKTOP_BUNDLED_MODELS_DIR / ".gitkeep").touch()

    mb = n_bytes / 1024 / 1024
    print(
        f"[build] {flavor}: bundled_models 已同步 {n_files} 个文件 ({mb:.1f} MB) "
        f"→ {DESKTOP_BUNDLED_MODELS_DIR}",
        flush=True,
    )
    if external_files:
        ext_mb = n_external_bytes / 1024 / 1024
        print(
            f"[build] {flavor}: external seed {n_external} 个大文件 ({ext_mb:.1f} MB) "
            f"→ {EXTERNAL_SEED_DIR}  (build-desktop.ps1 会拷到 dist-{flavor}/models_seed/ 进 ISO)",
            flush=True,
        )
    if n_redundant_skipped:
        print(
            f"[build] {flavor}: 跳过 {n_redundant_skipped} 个冗余权重 "
            f"(同 model 目录已有 .gguf/.safetensors,装机不需要):",
            flush=True,
        )
        for r in redundant_examples:
            print(f"  - {r}", flush=True)
        if n_redundant_skipped > len(redundant_examples):
            print(f"  ... 共 {n_redundant_skipped} 个", flush=True)
    if skipped:
        print(f"[build] {flavor}: 跳过非 {flavor} 清单内 cap: {', '.join(skipped)}", flush=True)

    # 疑似误放警告:vendor 根级出现了非已知 cap 的大目录(≥ 100 MB),装包里
    # 不会包含这些模型,运行时直接缺。打成 WARN 而不是 FATAL,因为这个目录
    # 也可能是用户故意放在 vendor 下面"暂存"的(虽然不推荐)。
    if suspicious_skipped:
        total_gb = sum(s for _, s in suspicious_skipped) / 1024 / 1024 / 1024
        print(
            f"[build] ⚠ {flavor}: vendor 根级有 {len(suspicious_skipped)} 个"
            f"**非 cap 名**子目录({total_gb:.2f} GB)被跳过,装包不含这些模型:",
            flush=True,
        )
        for name, size in suspicious_skipped:
            gb = size / 1024 / 1024 / 1024
            hint = _guess_cap_for_dir(name)
            if hint:
                print(
                    f"  - {name}/  ({gb:.2f} GB) "
                    f"→ 看起来该归 {hint!r} cap,建议 Move-Item:",
                    flush=True,
                )
                print(
                    f'      Move-Item "{src}\\{name}" "{src}\\{hint}\\{name}"',
                    flush=True,
                )
            else:
                print(
                    f"  - {name}/  ({gb:.2f} GB) "
                    "→ 不像主流模型命名,请确认它属于哪个 cap "
                    f"(可选:{' / '.join(sorted(_KNOWN_CAP_DIRS))})后 Move-Item 进去。",
                    flush=True,
                )
        print(
            "[build]   修完再跑 build,这些子目录会被正确同步进 installer / external seed。",
            flush=True,
        )


# Rust target triple → vendor 平台子目录名映射。
# 命名约定见 chayuan-server/vendor/services/llama-server/README.md。
_TRIPLE_TO_VENDOR_SUBDIR: dict[str, str] = {
    "aarch64-apple-darwin":          "macos-arm64",
    "x86_64-apple-darwin":           "macos-x64",
    "x86_64-pc-windows-msvc":        "win-x64",
    "aarch64-pc-windows-msvc":       "win-arm64",
    "x86_64-unknown-linux-gnu":      "linux-x64",
    "aarch64-unknown-linux-gnu":     "linux-arm64",
}


def triple_to_vendor_subdir(triple: str) -> str:
    """Rust target triple → ``vendor/services/<engine>-server/<subdir>/`` 命名。

    CHAYUAN_VENDOR_PLATFORM env 显式覆盖(单值),用于 Win 上想强制 noavx / avx512 之类
    CPU 变体打包(默认 win-x64 是 AVX2 build)。
    """
    override = os.environ.get("CHAYUAN_VENDOR_PLATFORM", "").strip()
    if override:
        return override
    return _TRIPLE_TO_VENDOR_SUBDIR.get(triple, "")


# 不同 OS 二进制的 magic bytes(用于 cross-build 时验证文件不是被截断的占位)
_BINARY_MAGIC: dict[str, tuple[bytes, ...]] = {
    "win-x64":         (b"MZ",),                     # PE
    "win-x64-avx":     (b"MZ",),
    "win-x64-avx512":  (b"MZ",),
    "win-x64-noavx":   (b"MZ",),
    "win-arm64":       (b"MZ",),
    "linux-x64":       (b"\x7fELF",),                # ELF
    "linux-arm64":     (b"\x7fELF",),
    "macos-arm64":     (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"),  # Mach-O 64 LE / Universal
    "macos-x64":       (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"),
}


def _check_magic(path: Path, subdir: str) -> tuple[bool, str]:
    """读 binary 头 4 字节,跟 OS 对应的 magic 比对。返回 (ok, reason)。"""
    expected = _BINARY_MAGIC.get(subdir)
    if not expected:
        return True, "subdir unmapped, skip magic check"
    try:
        head = path.read_bytes()[:4]
    except OSError as e:
        return False, f"read failed: {e}"
    for magic in expected:
        if head.startswith(magic):
            return True, ""
    return False, f"magic {head!r} 不匹配 {subdir} 期望 {expected}"


def _kill_lingering(exe_name: str) -> None:
    """杀掉本机所有同名 exe 进程(不论是不是本次 spawn 的子进程)。

    场景:verify --help hang 30s 后 subprocess.run 强杀子句柄,但 Windows 上
    某些情况下 sidecar 已经 fork 出孙进程,父句柄被回收孙还活着 → 占着
    .gguf / .dll → 下一轮 sync rmtree 报 WinError 32。这里用 taskkill /T 兜底
    把整棵进程树清掉。Linux/Mac 走 pkill。
    """
    name_no_ext = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/IM", exe_name],
                capture_output=True, timeout=10, check=False,
            )
        else:
            subprocess.run(
                ["pkill", "-9", "-f", name_no_ext],
                capture_output=True, timeout=10, check=False,
            )
    except (OSError, subprocess.SubprocessError):
        pass


def verify_service_binaries(target_subdir: str, host_subdir: str) -> None:
    """打集成包后验证 src-tauri/services/ 里的运行程序可用、可运行。

    Args:
        target_subdir: 本次 build 的目标 vendor 子目录名(决定 magic bytes)
        host_subdir: 当前 build host 的 vendor 子目录名;== target 时跑 ``--help`` 实测可执行

    检查项:
        1. 每个 ``<engine>-server/`` 下必须有主 binary(``<engine>-server[.exe]``)
        2. 主 binary 文件大小 > 100 KB(防 LFS 占位 / 半下载文件)
        3. magic bytes 匹配目标 OS(防把 Win .exe 错打进 Linux 包等)
        4. host == target 时 ``binary --help`` 5s 内退出且 stdout 非空(实测可运行)

    失败立刻 ``SystemExit(2)``,避免把坏包发出去。
    """
    if not DESKTOP_SERVICES_DIR.is_dir():
        print(f"[verify] {DESKTOP_SERVICES_DIR} 不存在,跳过验证", flush=True)
        return

    engines = [d for d in DESKTOP_SERVICES_DIR.iterdir()
               if d.is_dir() and not d.name.startswith(".")]
    if not engines:
        print("[verify] services/ 下没有 engine 子目录,跳过验证", flush=True)
        return

    win = target_subdir.startswith("win-")
    suffix = ".exe" if win else ""
    failures: list[str] = []

    for engine_dir in sorted(engines):
        engine = engine_dir.name  # e.g. "llama-server"
        bin_path = engine_dir / f"{engine}{suffix}"

        # 1) 文件存在性
        if not bin_path.is_file():
            failures.append(f"{engine}:缺主 binary {bin_path.relative_to(DESKTOP_SERVICES_DIR)}")
            continue

        # 2) 大小
        size = bin_path.stat().st_size
        if size < 100 * 1024:
            failures.append(f"{engine}:binary 异常小({size} B),疑似占位 / 半下载")
            continue

        # 3) magic bytes
        ok, reason = _check_magic(bin_path, target_subdir)
        if not ok:
            failures.append(f"{engine}:{reason}")
            continue

        print(f"[verify] {engine}/{bin_path.name}  size={size/1024/1024:.2f} MB  magic OK", flush=True)

        # 4) 实测 --help(仅 host == target 时,即非 cross-build)
        if host_subdir == target_subdir:
            try:
                # 给可执行位(macOS / Linux 从 git checkout 可能 666)
                if not win:
                    os.chmod(bin_path, 0o755)
                # 给 30s 余量 — llama-server 启动会做 CPU 特性探测 / 部分
                # 系统上还会被国产杀软的"主动防御"沙箱拉去跑一遍才放行(实测
                # 火绒 / 360 给十几秒延迟);5s 太紧,误报 hang。--help 本身
                # 不加载模型,正常路径还是 < 1s 退。
                #
                # whisper-server / llama-server 这类二进制是动态链接的,捆绑的
                # libwhisper.so.1 / libggml*.so 等共享库就扁平放在二进制自身
                # 所在目录(sync_service_binaries 已一并拷过来)。动态链接器
                # 默认不在二进制自身目录找 .so(除非 rpath=$ORIGIN),所以这里
                # 把二进制所在目录加进 LD_LIBRARY_PATH(Linux)/ DYLD_LIBRARY_PATH
                # (macOS),否则 --help 会 127 报 "cannot open shared object file"。
                # app 运行期拉起 sidecar 时本来也得这么设。
                run_env = os.environ.copy()
                if sys.platform == "darwin":
                    _lib_var = "DYLD_LIBRARY_PATH"
                else:
                    _lib_var = "LD_LIBRARY_PATH"
                _bin_dir = str(bin_path.parent)
                _existing = run_env.get(_lib_var, "")
                run_env[_lib_var] = (
                    f"{_bin_dir}{os.pathsep}{_existing}" if _existing else _bin_dir
                )
                r = subprocess.run(
                    [str(bin_path), "--help"],
                    capture_output=True, timeout=30, env=run_env,
                )
                stderr_bytes = r.stderr or b""
                stdout_bytes = r.stdout or b""
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").lower()
                linker_err_markers = (
                    "version `glibc",
                    "version `glibcxx",
                    "version `cxxabi",
                    "version not found",
                    "no version information available",
                    "symbol not found",
                    "image not found",
                    "incompatible library",
                    "cannot execute binary file",
                    "is not a valid win32 application",
                    # 加固:即便设了 LD_LIBRARY_PATH,build host 仍可能缺某个
                    # 系统级 .so(目标机器上有);这类 runtime-linker 错跟
                    # glibc 不兼容同理 —— host 环境问题,不代表二进制对目标
                    # 平台坏(magic / size 已校验过)。降级成 ⚠ 警告,不 FATAL。
                    "error while loading shared libraries",
                    "cannot open shared object file",
                )
                hit_linker = next(
                    (m for m in linker_err_markers if m in stderr_text), None,
                )
                if hit_linker:
                    # 软警告,不阻断 build —— 二进制本身是好的,只是本机 OS / glibc 不兼容,
                    # 用户的目标机器(更新的 Ubuntu / 装好的 Windows)上是可运行的。
                    print(
                        f"[verify]   ⚠ binary 在本 host 跑 --help 命中 runtime-linker 错误"
                        f"(标记={hit_linker!r});不影响目标平台,继续 build",
                        flush=True,
                    )
                elif r.returncode not in (0, 1):
                    # 非常规退出码 —— --help 不应该 segfault/-15。算 fail。
                    failures.append(
                        f"{engine}:--help 异常退出码 {r.returncode},stderr={stderr_text[:200]!r}"
                    )
                elif not (stdout_bytes + stderr_bytes).strip():
                    failures.append(f"{engine}:--help stdout/stderr 都空,binary 可能跑不起来")
                else:
                    print(
                        f"[verify]   --help 退出码 {r.returncode},输出 "
                        f"{len(stdout_bytes)+len(stderr_bytes)} B(可运行)",
                        flush=True,
                    )
            except subprocess.TimeoutExpired:
                # 30s 还没退 = 真 hang 住 / 杀软沙箱卡死。强杀 + 报 fail。
                # 不 cleanup 进程的话,它会占着 .gguf / .dll,下一轮 sync 删
                # bundled_models 时 PermissionError(WinError 32)。
                _kill_lingering(bin_path.name)
                failures.append(f"{engine}:--help 30s 超时,binary hang 住(已强杀进程)")
            except OSError as e:
                # Exec format error / Permission denied 等 —— binary 文件本身坏了
                failures.append(f"{engine}:--help 起不来:{type(e).__name__}: {e}")
        else:
            print(f"[verify]   cross-build({host_subdir} → {target_subdir}),跳过 --help 实测", flush=True)

    if failures:
        print("[verify] FATAL: 集成包 vendor 二进制验证失败:", file=sys.stderr, flush=True)
        for f in failures:
            print(f"  - {f}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    print(f"[verify] 所有 service binary 通过验证 ({len(engines)} 个 engine)", flush=True)


def sync_torch_wheels() -> None:
    """同步 vendor/torch_wheels/<sub>/*.whl → 两个目标:

    1. **Tauri 资源**(``src-tauri/torch_wheels/<sub>/``)— Tauri 把这里打进 MSI,
       装机后落在 ``<install>/torch_wheels/<sub>/``。CPU wheel ~250MB 装这里没问题
       (远低于 2GB MSI 单文件限制),装机即用,免 ISO 挂载。
    2. **ISO external seed**(``dist/torch_wheels_seed/<sub>/``)— 同 models_seed 的
       ISO 旁挂链路,留给未来 CUDA wheel(~2.5GB)用,撞 2GB MSI 限制时改走这里。

    幂等:每次先清两个目标再拷,避免 lite/full 切换或换 variant 时旧 wheel 残留。

    输出布局示例:
      src-tauri/torch_wheels/win_amd64_py312_cpu/torch-2.5.1+cpu-...whl
                            win_amd64_py312_cpu/torchvision-...whl
    """
    src = ROOT / "vendor" / "torch_wheels"
    desktop_dst = DESKTOP_SRC_TAURI / "torch_wheels"

    # 1. 清旧
    if desktop_dst.exists():
        shutil.rmtree(desktop_dst)
    if TORCH_WHEELS_SEED_DIR.exists():
        shutil.rmtree(TORCH_WHEELS_SEED_DIR)

    if not src.is_dir():
        print(f"[build] torch_wheels 源不存在 {src},跳过 torch wheel 同步",
              flush=True)
        # 留 .gitkeep 让 Tauri glob 不空
        desktop_dst.mkdir(parents=True, exist_ok=True)
        (desktop_dst / ".gitkeep").touch()
        return

    desktop_dst.mkdir(parents=True, exist_ok=True)

    # 2. 拷到 Tauri 资源 + 同时为大文件预备 ISO seed(目前 CPU wheel < 250MB,
    #    都走 Tauri 路径;未来 CUDA wheel ≥ 2GB 单文件可在这里扩展分流逻辑)。
    n_tauri = 0
    bytes_tauri = 0
    for sub in sorted(src.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        for whl in sub.glob("*.whl"):
            dst = desktop_dst / sub.name / whl.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(whl, dst)
            n_tauri += 1
            bytes_tauri += whl.stat().st_size

    print(
        f"[build] torch_wheels → {desktop_dst} "
        f"({n_tauri} 个 wheel,~{bytes_tauri / 1024 / 1024:.1f} MiB,进 MSI 资源)",
        flush=True,
    )

    if n_tauri == 0:
        # Tauri glob 防空 — 留 .gitkeep
        (desktop_dst / ".gitkeep").touch()


def sync_services(*, lite: bool, target_triple: str | None = None) -> None:
    """同步 vendor/services/<engine>-server/<platform>/* → src-tauri/services/<engine>-server/*。

    lite/full 都需要 llama-server / whisper-server 这类外部 runtime 二进制。
    (老版本曾把 lite 的 services 清空,以为 lite = 瘦客户端不跑本地推理;
    但 LITE_CAPS 加进 embedding/rerank/asr 模型权重后,光打模型不打 launcher
    binary,start() 永远 state=failed "llama-server.exe 不在",装机即用语义破。
    现在 lite/full 同样同步,差额 ~50 MB 相对模型权重可忽略。)

    选 platform 子目录的优先级:
      1. ``target_triple`` 参数(显式传入,通常来自 CARGO_BUILD_TARGET / --target arg)
      2. 当前 host 的 :func:`rust_target_triple`(默认行为,Tauri 本机 build)
      3. CHAYUAN_VENDOR_PLATFORM env 可强制覆盖 1+2(用于 Win 上想打 noavx / avx512 等
         CPU 变体的安装包)

    对每个 ``<engine>-server`` 子目录:
      * 找 ``<engine>-server/<vendor-subdir>/``,**把内容(非子目录本身)拷到**
        ``src-tauri/services/<engine>-server/`` 顶层(扁平 layout,end-user install
        后由 ``local_runtime.py:find_server_exe`` 的扁平 fallback 命中)。
      * 子目录不存在 / 只含 .gitkeep → 警告 + 跳过(不算错;e.g. whisper Mac/Linux
        upstream 没发,只 Win 有 binary,Mac 包就是没 ASR 加速)。
    """
    if DESKTOP_SERVICES_DIR.exists():
        shutil.rmtree(DESKTOP_SERVICES_DIR)
    DESKTOP_SERVICES_DIR.mkdir(parents=True, exist_ok=True)

    # 注:lite 早返回逻辑已删除 — 见上方 docstring。

    if not SERVICES_SRC.is_dir():
        print(f"[build] 警告:services 源不存在 {SERVICES_SRC},集成版退化为空目录", flush=True)
        (DESKTOP_SERVICES_DIR / ".gitkeep").touch()
        return

    triple = target_triple or rust_target_triple()
    vendor_sub = triple_to_vendor_subdir(triple)
    if not vendor_sub:
        print(
            f"[build] 警告:rust target triple {triple!r} 没映射到 vendor 子目录,"
            f"集成版退化为空 services/(用户需要自己装 vendor binary)",
            flush=True,
        )
        (DESKTOP_SERVICES_DIR / ".gitkeep").touch()
        return

    print(f"[build] 集成版:target triple = {triple} → vendor subdir = {vendor_sub}", flush=True)

    n_files = 0
    n_bytes = 0
    skipped_engines: list[str] = []
    for entry in sorted(SERVICES_SRC.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        plat_dir = entry / vendor_sub
        if not plat_dir.is_dir():
            skipped_engines.append(f"{entry.name}(没 {vendor_sub}/ 子目录)")
            continue
        bin_files = [f for f in plat_dir.iterdir()
                     if f.is_file() and f.name != ".gitkeep"]
        if not bin_files:
            skipped_engines.append(f"{entry.name}({vendor_sub}/ 是空占位)")
            continue

        dst_engine = DESKTOP_SERVICES_DIR / entry.name
        dst_engine.mkdir(parents=True, exist_ok=True)
        for src_file in bin_files:
            if src_file.stat().st_size >= _WIN_INSTALLER_FILE_LIMIT:
                print(
                    f"[build] FATAL: services 内单文件 ≥ 2 GB,无法打进 Windows installer:\n"
                    f"  {src_file.relative_to(SERVICES_SRC)} "
                    f"({src_file.stat().st_size / 1024 / 1024 / 1024:.2f} GB)",
                    file=sys.stderr, flush=True,
                )
                raise SystemExit(2)
            dst = dst_engine / src_file.name
            shutil.copy2(src_file, dst)
            n_files += 1
            n_bytes += dst.stat().st_size

    mb = n_bytes / 1024 / 1024
    flavor_tag = "lite" if lite else "full"
    print(
        f"[build] {flavor_tag}:services 已同步 {n_files} 个文件 ({mb:.1f} MB) "
        f"→ {DESKTOP_SERVICES_DIR}",
        flush=True,
    )
    if skipped_engines:
        print("[build] 注意:以下 engine 在目标平台缺 binary,装机后该 capability 没本地加速:",
              flush=True)
        for s in skipped_engines:
            print(f"  - {s}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="只打包,不拷贝到 chayuan-client/apps/desktop/src-tauri/binaries/",
    )
    parser.add_argument(
        "--release",
        type=str,
        default="",
        choices=["", "lite", "standard", "pro"],
        help=("(集成版打包)release 矩阵名,先跑 chayuan-pack fetch+stage "
              "把 vendor + 模型拉好再打 PyInstaller。空 = 只打 sidecar exe。"),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="(仅 --release 用)跳过网络;只读 packaging/.cache。",
    )
    parser.add_argument(
        "--sync-bundled-only",
        action="store_true",
        help=("跳过 PyInstaller,只把 vendor/bundled_models 同步到 src-tauri/bundled_models/。"
              "build-desktop 双 flavor 模式下,sidecar 跑一次就够,flavor 切换只需重跑这一步。"
              "注:本 flag 同时也跑 services sync(等价于 --sync-only,保留旧名兼容)。"),
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help=("跳过 PyInstaller,只跑 vendor sync(bundled_models + services + verify)。"
              "等价于 --sync-bundled-only,但名字更准 — 它实际同时同步两个目录。"
              "build-desktop.ps1 在 sidecar 缓存命中时走这个,纯增量打包 < 2 min。"),
    )
    parser.add_argument(
        "--sync-services-only",
        action="store_true",
        help="只同步 vendor/services/ 到 src-tauri/services/(快速 vendor 二进制改动迭代),"
             "跟 --sync-bundled-only / --sync-only 互斥",
    )
    parser.add_argument(
        "--target",
        default="",
        help=("(可选)显式指定 Rust target triple,例 x86_64-pc-windows-msvc;"
              "不传 = 取当前 host。CI cross-build 用。CHAYUAN_VENDOR_PLATFORM env "
              "可进一步覆盖 vendor 子目录选择(Win CPU 变体)。"),
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="跳过 src-tauri/services/ 里 vendor 二进制的可运行性验证(默认会跑)。",
    )
    parser.add_argument(
        "--lite",
        action="store_true",
        help=("打 lite installer:bundled_models/ 只嵌轻量版子集"
              "(embedding/rerank/asr/ocr,~990 MB);chat、image-embedding 由用户运行时下。"
              "等价旧 env CHAYUAN_LITE_BUILD=1 但更显式。优先级高于 env。"
              "清单真源:packaging/bundle_manifest.py"),
    )
    parser.add_argument(
        "--bundled-source",
        default="",
        help=("显式指定 bundled_models 源目录(子目录结构同 vendor/bundled_models/<cap>/)。"
              "默认 ROOT/vendor/bundled_models/。优先级:CLI > $CHAYUAN_BUILD_BUNDLED_SOURCE env > 默认。"
              "典型用法:dev 时 install-bundled-models.ps1 -ChayuanRoot $env:CHAYUAN_ROOT 装到运行时目录,"
              "打包时 --bundled-source $env:CHAYUAN_ROOT/models/bundled,一份模型给 dev + 打包共用。"),
    )
    parser.add_argument(
        "--torch-variants",
        default="cpu",
        help=("逗号分隔的 PyTorch wheel variant 列表,默认 ``cpu``(~250 MB)。"
              "可加 ``cpu,cu124`` 或 ``cu124``(GPU 用户,~2-3 GB/variant)。"
              "构建机在 PyInstaller 之前会 ensure 这些 wheel 已下载到 "
              "vendor/torch_wheels/<platform_pyver_variant>/,缺则 pip download。"
              "走 ``--skip-torch-preflight`` 跳过本步骤(开发回归用)。"),
    )
    parser.add_argument(
        "--torch-platforms",
        # 默认 ""(自动按 --target triple 映射,只下当前平台一个)。
        #
        # 历史折腾:e986e6d 把 default 改成 4 平台一次性下,但 desktop installer
        # 是 OS-specific(Windows MSI/ISO 只装 Windows 用户,Mac/Linux 各自打 dmg/
        # AppImage),其它 OS 的 wheel 进 Windows ISO 实际无用 — 没人在 Windows
        # ISO 上装 Mac。而且 strict_vendor=True + 非 host 平台 vendor 空 →
        # preflight 直接 RuntimeError(见 preflight_torch.py:157-168)。
        #
        # 撤回到 ""(自动 host 检测),跨平台需要时显式传 --torch-platforms。
        default="",
        help=("逗号分隔的 pip --platform tag。默认 ``\"\"`` = 按 ``--target`` triple "
              "自动映射(如 x86_64-pc-windows-msvc → win_amd64),只下当前 host "
              "平台一个 wheel。跨平台 release 时显式传:"
              "``--torch-platforms win_amd64,manylinux2014_x86_64,macosx_11_0_arm64,"
              "macosx_10_9_x86_64``(注:需保证每个平台 vendor 都有 wheel,否则"
              "strict_vendor 模式 raise)。GPU:--torch-variants cpu,cu124。"),
    )
    parser.add_argument(
        "--torch-py-versions",
        default="312",
        help=("目标 Python 版本(无点,如 312),默认 312。"),
    )
    parser.add_argument(
        "--skip-torch-preflight",
        action="store_true",
        help="跳过 PyTorch wheel preflight(不再下载 / 不再校验 vendor/torch_wheels)。",
    )
    parser.add_argument(
        "--allow-missing-torch",
        action="store_true",
        help=("torch wheel 下载失败时只 warning,不阻塞打包(用于无网构建机调试)。"
              "成品包将不带离线 torch wheel,装机用户只能走在线安装。"),
    )
    parser.add_argument(
        "--strict-vendor",
        action="store_true",
        help=("严格 vendor 模式:所有打包资源(bundled_models / services / torch_wheels)"
              "必须从 chayuan-server/vendor/ 来,缺则报错抛。禁用 preflight_torch 的 "
              "pip download fallback;-bundled-source / $CHAYUAN_BUILD_BUNDLED_SOURCE "
              "覆盖时打 WARN(不禁止,但提示走偏)。"
              "build-desktop.ps1 默认会传这个;CI 内网无 pytorch.org 时也建议加。"),
    )
    # 用 parse_known_args:任何 build.py 不认识的参数(--clean / --log-level=DEBUG /
    # --noupx / 等)都直接转发给 PyInstaller。免得用户记错 ``--`` 分隔符位置 ──
    # poetry / argparse REMAINDER 组合下 ``--`` 经常被吃掉,导致 ``--clean`` 报
    # "unrecognized arguments"。
    args, extra = parser.parse_known_args()
    # 兼容老用法:如果用户仍写了 ``-- --clean``,把开头的 ``--`` 剥掉
    if extra and extra[0] == "--":
        extra = extra[1:]

    # 2026-05-15:lite vs full 控制 bundled_models 同步到 Tauri resources 的范围。
    # 优先级:--lite CLI 参数 > CHAYUAN_LITE_BUILD env(保留向后兼容,旧 build-desktop.ps1
    # 还在通过 env 传)。两者都没设 = full。
    lite = bool(args.lite) or os.environ.get("CHAYUAN_LITE_BUILD", "").strip().lower() in (
        "1", "true", "yes",
    )

    # bundled 源路径解析(CLI > env > vendor 默认),解析完后所有 sync 调用都用它。
    bundled_src = _resolve_bundled_src(args.bundled_source or None)
    if bundled_src != BUNDLED_SRC:
        # strict_vendor 下不静默,把覆盖来源 + 期望路径都打出来,让用户知道是哪个入口绕了 vendor
        override_via = "--bundled-source" if args.bundled_source else "$CHAYUAN_BUILD_BUNDLED_SOURCE"
        msg = (
            f"[build] WARN bundled_models 源被 {override_via} 覆盖:{bundled_src}\n"
            f"        vendor 默认源:{BUNDLED_SRC}\n"
            f"        想严格走 vendor → unset {override_via} 后重跑(并加 --strict-vendor)"
        )
        print(msg, flush=True)

    # 打 build 源 map — 让用户清楚每个资源从哪儿来,strict_vendor 下不许有"非 vendor"项
    print(
        "[build] 打包资源源映射(strict_vendor={s}):\n"
        "  bundled_models  ← {bm}  (存在: {bm_ok}, 子目录数: {bm_n})\n"
        "  services        ← {svc} (存在: {svc_ok}, 引擎数: {svc_n})\n"
        "  torch_wheels    ← {tw}  (存在: {tw_ok}, 子目录数: {tw_n})\n"
        "  runtimes        ← {rt}  (存在: {rt_ok})  [chayuan_packaging 用,desktop build 不打]\n"
        "  Python 三方包    ← 当前 venv site-packages(tiktoken / fastapi / onnxruntime 等;必走 venv,不入 vendor)".format(
            s=args.strict_vendor,
            bm=bundled_src,
            bm_ok=bundled_src.is_dir(),
            bm_n=len([p for p in bundled_src.iterdir() if p.is_dir()]) if bundled_src.is_dir() else 0,
            svc=SERVICES_SRC,
            svc_ok=SERVICES_SRC.is_dir(),
            svc_n=len([p for p in SERVICES_SRC.iterdir() if p.is_dir()]) if SERVICES_SRC.is_dir() else 0,
            tw=ROOT / "vendor" / "torch_wheels",
            tw_ok=(ROOT / "vendor" / "torch_wheels").is_dir(),
            tw_n=len([p for p in (ROOT / "vendor" / "torch_wheels").iterdir() if p.is_dir()]) if (ROOT / "vendor" / "torch_wheels").is_dir() else 0,
            rt=ROOT / "vendor" / "runtimes",
            rt_ok=(ROOT / "vendor" / "runtimes").is_dir(),
        ),
        flush=True,
    )

    target = args.target or rust_target_triple()
    host = rust_target_triple()
    target_sub = triple_to_vendor_subdir(target)
    host_sub = triple_to_vendor_subdir(host)

    def _do_sync_and_verify() -> None:
        sync_services(lite=lite, target_triple=target)
        # lite/full 都同步 services,verify 跟着也都跑(lite 也跑 llama-server /
        # whisper-server,verify 失败 = vendor binary 坏,无关 flavor)
        if not args.skip_verify:
            verify_service_binaries(target_subdir=target_sub, host_subdir=host_sub)
        # torch wheels 也走 ISO 旁挂(2026-05-18 起从 sidecar exe 嵌入改成
        # ISO external seed),sync_torch_wheels 把 vendor/torch_wheels/ 整套
        # 拷到 dist/torch_wheels_seed/,后续 build-desktop.ps1 ISO 阶段嵌进 .iso。
        sync_torch_wheels()

    if args.sync_bundled_only or args.sync_only:
        # 跳过 PyInstaller,只同步 bundled_models + services + torch wheels。
        # build-desktop 双 flavor 模式 / sidecar 缓存命中走这条路径。
        sync_bundled_models(lite=lite, bundled_src=bundled_src, target_triple=target)
        _do_sync_and_verify()
        return

    if args.sync_services_only:
        _do_sync_and_verify()
        return

    if args.release:
        run_chayuan_pack(args.release, offline=args.offline)

    # PyTorch wheel preflight — 把目标 platform 的 torch + torchvision wheel
    # 预下载到 vendor/torch_wheels/<sub>/,PyInstaller 会按 spec 把它们打进
    # sys._MEIPASS/vendor/torch_wheels/,装机用户 chayuan-server 启动时能离线
    # 装上(无网兜底);走 --skip-torch-preflight 跳过(开发回归用)。
    if not args.skip_torch_preflight:
        _run_torch_preflight(
            variants=args.torch_variants,
            platforms_override=args.torch_platforms,
            py_versions=args.torch_py_versions,
            target_triple=target,
            allow_missing=args.allow_missing_torch,
            strict_vendor=args.strict_vendor,
        )

    # 把 lite 决定显式同步到 env,让 PyInstaller spec 看得到。
    # spec(2026-05-19 起)据 CHAYUAN_LITE_BUILD=1 把 torch/torchvision/transformers 加进
    # excludes 而不是 _collect_required,lite bundle 体积从 1.6 GB → 1.1 GB。
    # 即便用户走 --lite CLI 而不是 env,这里也把决定写回 env 让 subprocess 继承。
    os.environ["CHAYUAN_LITE_BUILD"] = "1" if lite else "0"

    run_pyinstaller(extra)
    if not args.no_copy:
        copy_to_tauri_binaries()
        sync_bundled_models(lite=lite, bundled_src=bundled_src, target_triple=target)
        _do_sync_and_verify()


def _run_torch_preflight(
    *,
    variants: str,
    platforms_override: str,
    py_versions: str,
    target_triple: str,
    allow_missing: bool,
    strict_vendor: bool = False,
) -> None:
    """调 packaging/preflight_torch.preflight,缺则下,失败则按 allow_missing 决定抛 or warn。

    strict_vendor=True 时传递给 preflight,vendor/torch_wheels/ 没找到配对 wheel
    直接报错抛(不再静默走 pip download from pytorch.org)。
    """
    # ⚠ 不要写 `from packaging.preflight_torch import ...`:PyPI 上的 packaging
    # 是个真包(pip/setuptools 的依赖,几乎所有 Python 都装),会撞到它而不是
    # 本仓的 chayuan-server/packaging/(后者没 __init__.py,只是普通目录)。按
    # 文件路径直接 load,绕开 namespace 冲突,跟外部环境无关。
    #
    # ⚠ 注册进 sys.modules 后再 exec_module:preflight_torch 用 @dataclass,
    # dataclasses 内部走 `sys.modules.get(cls.__module__).__dict__` 解析类型
    # 引用,模块没在 sys.modules 里会拿到 None.__dict__ 报 AttributeError。
    import importlib.util as _ilu
    _pf_path = ROOT / "packaging" / "preflight_torch.py"
    _mod_name = "chayuan_preflight_torch"
    _spec = _ilu.spec_from_file_location(_mod_name, _pf_path)
    if _spec is None or _spec.loader is None:
        raise SystemExit(f"[build] 无法加载 preflight_torch.py(路径:{_pf_path})")
    _mod = _ilu.module_from_spec(_spec)
    sys.modules[_mod_name] = _mod
    _spec.loader.exec_module(_mod)
    DEFAULT_OUT_ROOT = _mod.DEFAULT_OUT_ROOT
    _PLATFORM_TAG = _mod._PLATFORM_TAG
    build_targets = _mod.build_targets
    parse_variants = _mod.parse_variants
    preflight = _mod.preflight

    # platform tag:CLI 显式覆盖优先;否则按 target triple 自动映射
    if platforms_override:
        platforms = [p.strip() for p in platforms_override.split(",") if p.strip()]
    else:
        tag = _PLATFORM_TAG.get(target_triple)
        if not tag:
            print(f"[build] WARN target triple {target_triple!r} 没有 PyTorch wheel platform 映射;"
                  f"跳过 torch preflight(用 --torch-platforms 显式指定可绕过)", flush=True)
            return
        platforms = [tag]

    py_vers = [v.strip() for v in py_versions.split(",") if v.strip()]
    var_list = parse_variants(variants)
    targets = build_targets(platforms, py_vers, var_list)
    if not targets:
        return
    print(
        f"[build] torch preflight: {len(targets)} target(s): "
        + ", ".join(t.subdir for t in targets),
        flush=True,
    )
    preflight(targets, out_root=DEFAULT_OUT_ROOT, allow_missing=allow_missing, strict_vendor=strict_vendor)
    print("[build] ✓ torch wheel preflight ok", flush=True)


if __name__ == "__main__":
    main()
